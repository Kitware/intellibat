"""Exercise the actual queues, worker threads and on-disk handoff without Pi I/O."""

import ast
import json
import os
import sys
import tempfile
import time
import unittest
import wave
from enum import Enum
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

from software.processing import Classifier, Spectrogram, recover_recordings


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.recordings = self.root / 'recordings'
        self.output = self.root / 'output'
        self.recordings.mkdir()
        self.output.mkdir()
        self.wav_queue, self.image_queue = Queue(), Queue()
        self.config = SimpleNamespace(
            config=SimpleNamespace(machine_learning_enabled=True)
        )
        self.compute = Mock(side_effect=self.render)
        self.predict = Mock(
            side_effect=lambda paths: [
                {'path': paths[0], 'label': 'EPFU', 'confidence': 0.8}
            ]
        )
        self.model = Mock(return_value=SimpleNamespace(classify=self.predict))
        batbot = SimpleNamespace(
            spectrogram=SimpleNamespace(compute=self.compute),
            classifier=SimpleNamespace(Classifier=self.model),
        )
        patcher = patch.dict(sys.modules, {'batbot': batbot})
        patcher.start()
        self.addCleanup(patcher.stop)
        logger = patch('software.processing.log')
        logger.start()
        self.addCleanup(logger.stop)
        self.renderer = Spectrogram(
            self.wav_queue,
            self.image_queue,
            self.output,
            retry_delay=0.01,
            max_attempts=2,
        )
        self.classifier = Classifier(
            self.image_queue, self.config, retry_delay=0.01, max_attempts=2
        )
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        for worker in (self.renderer, self.classifier):
            worker.stopped.set()
        for worker in (self.renderer, self.classifier):
            if worker.ident is not None:
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())

    def start_workers(self):
        self.renderer.start()
        self.classifier.start()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail(
                    f'Timed out: {self.renderer.snapshot()}, {self.classifier.snapshot()}'
                )
            time.sleep(0.01)

    def drain(self):
        self.wait_for(
            lambda: self.wav_queue.unfinished_tasks == 0
            and self.image_queue.unfinished_tasks == 0
        )

    def wav(self, name='sample.wav'):
        path = self.recordings / name
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(256000)
            stream.writeframes(b'\x00\x00' * 128)
        return path

    def products(self, path, count=1):
        images = [
            self.output / f'{Path(path).stem}.{i:02}of{count:02}.compressed.jpg'
            for i in range(1, count + 1)
        ]
        for image in images:
            image.write_bytes(b'fake image')
        (self.output / f'{Path(path).stem}.metadata.json').write_text(
            json.dumps(
                {
                    'wav.path': str(path),
                    'spectrogram': {'compressed.path': list(map(str, images))},
                }
            )
        )
        return images

    def render(self, path, **kwargs):
        # Read the WAV to prove only closed files with finalized headers arrive.
        with wave.open(path) as stream:
            self.assertGreater(stream.getnframes(), 0)
        self.assertEqual(
            kwargs['out_file_stem'], str(self.output.resolve() / Path(path).stem)
        )
        self.assertTrue(kwargs['force_overwrite'])
        self.products(path)

    def test_closed_recording_reaches_both_workers_and_publishes_ml_atomically(self):
        tree = ast.parse((Path(__file__).parents[1] / 'service.py').read_text())
        namespace = {
            'Enum': Enum,
            'time': time,
            'os': os,
            'RUNTIME_STATUS': {'recordings_completed': 0},
        }
        module = ast.Module(
            body=[
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef)
                and node.name in {'RecordingState', 'RecordingStateMachine'}
            ],
            type_ignores=[],
        )
        exec(compile(module, 'service.py', 'exec'), namespace)
        recorder = namespace['RecordingStateMachine'](self.config, self.wav_queue)
        path = self.wav()
        path.rename(str(path) + '.part')
        stream = wave.open(str(path) + '.part', 'rb')
        recorder.begin_recording(stream, str(path))
        self.start_workers()
        self.assertTrue(self.wav_queue.empty())
        recorder.stop_recording()
        self.drain()
        image = self.output / 'sample.01of01.compressed.jpg'
        self.predict.assert_called_once_with([str(image.resolve())])
        self.assertEqual(
            json.loads(image.with_suffix('.results.json').read_text())['label'], 'EPFU'
        )
        self.assertFalse(list(self.output.glob('*.tmp')))
        self.assertEqual(self.renderer.snapshot()['completed'], 1)
        self.assertEqual(self.classifier.snapshot()['completed'], 1)

    def test_bad_wav_is_retried_without_stopping_next_recording(self):
        bad = self.recordings / 'bad.wav'
        bad.write_bytes(b'broken wav')
        self.wav_queue.put(str(bad))
        self.wav_queue.put(str(self.wav()))
        self.start_workers()
        self.drain()
        self.assertEqual(self.renderer.snapshot()['failures'], 2)
        self.assertEqual(self.renderer.snapshot()['retries'], 1)
        self.assertEqual(self.classifier.snapshot()['completed'], 1)
        self.assertTrue(self.renderer.is_alive())
        self.assertTrue(self.classifier.is_alive())

    def test_disabled_ml_queues_images_and_resumes_after_configuration_change(self):
        self.config.config.machine_learning_enabled = False
        self.wav_queue.put(str(self.wav()))
        self.start_workers()
        self.wait_for(lambda: self.classifier.snapshot()['state'] == 'paused')
        self.model.assert_not_called()
        self.assertTrue((self.output / 'sample.metadata.json').exists())
        self.config.config.machine_learning_enabled = True
        self.drain()
        self.assertEqual(self.classifier.snapshot()['completed'], 1)

    def test_failed_ml_job_does_not_stop_next_image(self):
        bad, good = self.products(self.wav(), count=2)

        def classify(paths):
            if paths[0] == str(bad.resolve()):
                raise RuntimeError('model input error')
            return [{'path': paths[0], 'label': 'EPFU', 'confidence': 0.8}]

        self.predict.side_effect = classify
        self.image_queue.put(str(bad))
        self.image_queue.put(str(good))
        self.start_workers()
        self.drain()
        self.assertEqual(self.classifier.snapshot()['failures'], 2)
        self.assertTrue(good.with_suffix('.results.json').exists())
        self.assertFalse(bad.with_suffix('.results.json').exists())
        self.assertTrue(self.classifier.is_alive())

    def test_invalid_or_mismatched_predictions_are_not_published(self):
        image = self.products(self.wav())[0]
        for results in (
            [],
            [{'label': 'EPFU', 'confidence': 0.9, 'path': '/another.jpg'}],
            [{'label': 'EPFU', 'confidence': float('nan')}],
            [{'error': 'failed'}],
        ):
            with self.subTest(results=results):
                self.predict.return_value = results
                self.predict.side_effect = None
                with self.assertRaises(RuntimeError):
                    self.classifier.process(str(image))
                self.assertFalse(image.with_suffix('.results.json').exists())

    def test_startup_recovers_each_stage_and_ignores_partial_and_completed_files(self):
        unrendered = self.wav('new.wav')
        partial = self.wav('partial.wav')
        (self.output / 'partial.01of01.compressed.jpg').write_bytes(b'incomplete')
        ml_pending = self.products(self.wav('pending.wav'))[0]
        complete = self.products(self.wav('complete.wav'))[0]
        complete.with_suffix('.results.json').write_text(
            '{"label":"EPFU","confidence":0.8}'
        )
        self.products(self.wav('no-calls.wav'), count=0)
        (self.recordings / 'unfinished.wav.part').write_bytes(b'not finished')
        (self.recordings / 'linked.wav').symlink_to(unrendered)
        counts = recover_recordings(
            self.recordings, self.output, self.wav_queue, self.image_queue
        )
        self.assertEqual(counts, {'spectrogram': 2, 'classifier': 1})
        self.start_workers()
        self.drain()
        self.assertEqual(
            {call.args[0] for call in self.compute.call_args_list},
            {str(unrendered.resolve()), str(partial.resolve())},
        )
        self.assertTrue(ml_pending.with_suffix('.results.json').exists())
        self.assertEqual(
            recover_recordings(
                self.recordings, self.output, self.wav_queue, self.image_queue
            ),
            {'spectrogram': 0, 'classifier': 0},
        )

    def test_zero_extracted_calls_never_queues_empty_ml_job(self):
        path = self.wav()
        self.compute.side_effect = lambda path, **kwargs: self.products(path, count=0)
        self.wav_queue.put(str(path))
        self.start_workers()
        self.drain()
        self.model.assert_not_called()
        self.assertEqual(self.renderer.snapshot()['completed'], 1)


if __name__ == '__main__':
    unittest.main()
