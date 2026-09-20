"""Background WAV -> spectrogram -> classifier pipeline, independent of Pi I/O."""

import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from queue import Empty

# Spectrograms run in a service thread, without a GUI or display server.
os.environ.setdefault('MPLBACKEND', 'Agg')
log = logging.getLogger(__name__)


def spectrogram_products(wav_path, output_dir):
    """Return complete image products, or None when rendering is still needed.

    An empty image list with valid metadata means no calls were extracted; it
    is a completed spectrogram job and must not be passed to the classifier.
    """
    wav_path, output_dir = Path(wav_path), Path(output_dir).resolve()
    try:
        metadata = json.loads(
            (output_dir / f'{wav_path.stem}.metadata.json').read_text()
        )
        if Path(metadata['wav.path']).name != wav_path.name:
            return None
        products = metadata['spectrogram']['compressed.path']
        if not isinstance(products, list):
            return None
        images = []
        for product in products:
            # Older metadata may refer to a different working directory/device.
            # Products must still exist under the configured output directory.
            image = output_dir / Path(product).name
            if (
                not image.name.startswith(f'{wav_path.stem}.')
                or image.suffix.lower() not in {'.jpg', '.jpeg', '.png'}
                or image.is_symlink()
                or not image.is_file()
                or image.stat().st_size == 0
            ):
                return None
            images.append(str(image))
        return images
    except (OSError, ValueError, KeyError, TypeError):
        return None


def classification_complete(image_path):
    try:
        result = json.loads(Path(image_path).with_suffix('.results.json').read_text())
        confidence = result['confidence']
        return (
            not result.get('error')
            and isinstance(result['label'], str)
            and isinstance(confidence, (int, float))
            and math.isfinite(confidence)
            and 0 <= confidence <= 1
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


class ProcessingWorker(threading.Thread):
    """Keep accepting work after a bad input, with bounded per-file retries."""

    def __init__(self, name, queue, retry_delay=2, max_attempts=3):
        super().__init__(name=name, daemon=True)
        self.queue = queue
        self.stopped = threading.Event()
        self.retry_delay = retry_delay
        self.max_attempts = max_attempts
        self._attempts = {}
        self._lock = threading.Lock()
        self._status = {
            'state': 'starting',
            'current_file': None,
            'completed': 0,
            'skipped': 0,
            'failures': 0,
            'retries': 0,
            'last_completed_at': None,
            'last_error': None,
            'last_error_at': None,
        }

    def snapshot(self):
        with self._lock:
            return dict(self._status)

    def update_status(self, **values):
        with self._lock:
            self._status.update(values)

    def process(self, path):
        raise NotImplementedError

    def run(self):
        self.update_status(state='idle')
        while not self.stopped.is_set():
            try:
                item = self.queue.get(timeout=0.25)
            except Empty:
                continue
            path = str(item)
            self.update_status(state='processing', current_file=path)
            try:
                processed = self.process(path)
                self._attempts.pop(path, None)
                if processed:
                    self.update_status(
                        completed=self._status['completed'] + 1,
                        last_completed_at=time.time(),
                    )
                else:
                    self.update_status(skipped=self._status['skipped'] + 1)
            except Exception as error:
                attempts = self._attempts.get(path, 0) + 1
                self._attempts[path] = attempts
                self.update_status(
                    failures=self._status['failures'] + 1,
                    last_error=f'{path}: {error}',
                    last_error_at=time.time(),
                )
                log.exception(
                    '[%s] Failed to process %s (attempt %s/%s)',
                    self.name,
                    path,
                    attempts,
                    self.max_attempts,
                )
                if attempts < self.max_attempts:
                    self.update_status(state='retrying')
                    if not self.stopped.wait(self.retry_delay):
                        self.queue.put(path)
                        self.update_status(retries=self._status['retries'] + 1)
                else:
                    self._attempts.pop(path, None)
            finally:
                self.queue.task_done()
                self.update_status(state='idle', current_file=None)
        self.update_status(state='stopped')


class Spectrogram(ProcessingWorker):
    def __init__(self, spectrogram_queue, classifier_queue, output_dir, **kwargs):
        super().__init__('spectrogram', spectrogram_queue, **kwargs)
        self.classifier_queue = classifier_queue
        self.output_dir = Path(output_dir).resolve()

    def process(self, path):
        wav_path = Path(path).resolve()
        if wav_path.suffix.lower() != '.wav' or not wav_path.is_file():
            raise ValueError('The spectrogram worker requires a completed WAV file')
        products = spectrogram_products(wav_path, self.output_dir)
        rendered = products is None
        if rendered:
            import batbot

            self.output_dir.mkdir(parents=True, exist_ok=True)
            batbot.spectrogram.compute(
                str(wav_path),
                output_folder=str(self.output_dir),
                out_file_stem=str(self.output_dir / wav_path.stem),
                # A failed render may leave images behind. BatBot otherwise
                # skips the entire WAV when any matching product exists.
                force_overwrite=True,
                fast_mode=True,
                quiet=True,
                debug=False,
            )
            products = spectrogram_products(wav_path, self.output_dir)
            if products is None:
                raise RuntimeError(
                    'Spectrogram rendering did not produce complete metadata and images'
                )
        for image in products:
            if not classification_complete(image):
                # Only real compressed images are ML inputs, one image per job.
                self.classifier_queue.put(image)
        log.info('[spectrogram] %s: %s compressed images', wav_path.name, len(products))
        return rendered


class Classifier(ProcessingWorker):
    def __init__(self, classifier_queue, config_manager, **kwargs):
        super().__init__('classifier', classifier_queue, **kwargs)
        self.config_manager = config_manager
        self.runner = None

    def process(self, path):
        image = Path(path).resolve()
        if image.suffix.lower() not in {'.jpg', '.jpeg', '.png'} or not image.is_file():
            raise ValueError('The classifier worker requires a spectrogram image')
        if classification_complete(image):
            return False
        while not self.config_manager.config.machine_learning_enabled:
            self.update_status(state='paused')
            if self.stopped.wait(0.5):
                return False
        self.update_status(state='processing')
        if self.runner is None:
            import batbot

            self.runner = batbot.classifier.Classifier(batch_size=1, num_workers=1)
        results = self.runner.classify([str(image)])
        if len(results) != 1 or not isinstance(results[0], dict):
            raise RuntimeError('Classifier must return exactly one result per image')
        result = results[0]
        if result.get('error'):
            raise RuntimeError(result['error'])
        if result.get('path') and Path(result['path']).resolve() != image:
            raise RuntimeError('Classifier returned a result for a different image')
        confidence = result.get('confidence')
        if (
            not isinstance(result.get('label'), str)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise RuntimeError('Classifier returned an invalid prediction')
        result_path = image.with_suffix('.results.json')
        temporary = result_path.with_suffix('.json.tmp')
        try:
            temporary.write_text(json.dumps(result, indent=4))
            temporary.replace(result_path)
        finally:
            temporary.unlink(missing_ok=True)
        log.info('[classifier] Created %s', result_path)
        return True


def recover_recordings(recordings_dir, output_dir, spectrogram_queue, classifier_queue):
    """Requeue interrupted work after restart; leave completed products intact."""
    queued = {'spectrogram': 0, 'classifier': 0}

    def on_error(error):
        raise error

    for directory, folders, files in os.walk(recordings_dir, onerror=on_error):
        folders[:] = [
            name
            for name in folders
            if not name.startswith('.') and not (Path(directory) / name).is_symlink()
        ]
        for name in sorted(files):
            wav_path = Path(directory) / name
            if (
                name.startswith('.')
                or wav_path.suffix.lower() != '.wav'
                or wav_path.is_symlink()
                or not wav_path.is_file()
            ):
                continue
            products = spectrogram_products(wav_path, output_dir)
            if products is None:
                spectrogram_queue.put(str(wav_path.resolve()))
                queued['spectrogram'] += 1
            else:
                for image in products:
                    if not classification_complete(image):
                        classifier_queue.put(image)
                        queued['classifier'] += 1
    return queued
