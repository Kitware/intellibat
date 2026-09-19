"""Inventory recordings without importing the recorder's audio or ML stack."""

import json
import math
import os
import re
import stat
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

WINDOW_SECONDS = 900
LATIN_NAMES = {
    'ANPA': 'Antrozous pallidus',
    'COTO': 'Corynorhinus townsendii',
    'EPFU': 'Eptesicus fuscus',
    'EUMA': 'Euderma maculatum',
    'LACI': 'Lasiurus cinereus',
    'LANO': 'Lasionycteris noctivagans',
    'MYCA': 'Myotis californicus',
    'MYCI': 'Myotis ciliolabrum',
    'MYEV': 'Myotis evotis',
    'MYLU': 'Myotis lucifugus',
    'MYTH': 'Myotis thysanodes',
    'MYVO': 'Myotis volans',
    'MYYU': 'Myotis yumanensis',
    'PAHE': 'Parastrellus hesperus',
    'TABR': 'Tadarida brasiliensis',
}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}


@dataclass(frozen=True)
class RecordingFile:
    path: Path
    relative: str
    size: int
    mtime_ns: int
    inode: int

    @property
    def signature(self):
        return [self.size, self.mtime_ns]

    def unchanged(self):
        try:
            current = self.path.lstat()
            return (
                stat.S_ISREG(current.st_mode)
                and current.st_ino == self.inode
                and [current.st_size, current.st_mtime_ns] == self.signature
            )
        except OSError:
            return False


def read_json(path, limit=8 * 1024 * 1024):
    with open(path, 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('JSON file is too large')
    return json.loads(data)


def recording_key(path):
    name = path.name
    return (
        re.split(r'\.\d+of\d+\.', name)[0]
        if re.search(r'\.\d+of\d+\.', name)
        else re.sub(
            r'\.(metadata|results)\.json$|\.(wav|jpg|jpeg|png)$',
            '',
            name,
            flags=re.IGNORECASE,
        )
    )


def timestamp_for(item):
    match = re.match(r'chunk_(\d{9,11})(?:\.|$)', item.path.name)
    return int(match[1]) if match else item.mtime_ns / 1e9


def predictions(result):
    """Accept current BatBot results and legacy lists of label/confidence pairs."""
    if isinstance(result, dict):
        values = result.get('top') or result.get('scores')
        if not values and 'label' in result:
            values = [
                {'label': result['label'], 'confidence': result.get('confidence')}
            ]
    else:
        values = result
    if isinstance(values, dict):
        values = list(values.items())
    if not isinstance(values, list):
        return []
    parsed = []
    seen = set()
    for value in values:
        try:
            label, confidence = (
                (value['label'], value['confidence'])
                if isinstance(value, dict)
                else value
            )
            confidence = float(confidence)
            if (
                isinstance(label, str)
                and label not in seen
                and label
                and math.isfinite(confidence)
                and 0 <= confidence <= 1
            ):
                seen.add(label)
                parsed.append((label, confidence))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(parsed, key=lambda pair: -pair[1])[:5]


def aggregate_species(entries):
    """Sparse equivalent of scripts/species_timeline.py's 15-minute bins.

    Each entry is one recording. NOISE still competes for top-1. Numbered
    detections require a winning confidence >=25%; alternative bars >=10%.
    """
    bins = {}
    totals = Counter()
    labels = set()
    noise = low_confidence = unclassified = 0
    for timestamp, ranked in entries:
        window = math.floor(timestamp / WINDOW_SECONDS) * WINDOW_SECONDS
        bucket = bins.setdefault(
            window,
            {'timestamp': window, 'detections': 0, 'recordings': 0, 'species': {}},
        )
        bucket['recordings'] += 1
        if not ranked:
            unclassified += 1
            continue
        winner, confidence = ranked[0]
        if winner == 'NOISE':
            noise += 1
        elif confidence < 0.25:
            low_confidence += 1
        else:
            totals[winner] += 1
            bucket['detections'] += 1
        for label, confidence in ranked:
            if label == 'NOISE' or confidence < 0.10:
                continue
            labels.add(label)
            row = bucket['species'].setdefault(
                label, {'level': 0, 'top1': False, 'count': 0, 'max_confidence': 0}
            )
            level = 1 if confidence > 0.5 else 0.5 if confidence >= 0.25 else 0.1
            row['level'] = max(row['level'], level)
            row['max_confidence'] = max(row['max_confidence'], confidence)
            row['top1'] |= label == winner
            row['count'] += int(label == winner and confidence >= 0.25)
    return {
        'window_seconds': WINDOW_SECONDS,
        'labels': sorted(labels),
        'bins': [bins[key] for key in sorted(bins)],
        'histogram': [
            {'label': label, 'name': LATIN_NAMES.get(label, ''), 'count': count}
            for label, count in totals.most_common()
        ],
        'latin_names': LATIN_NAMES,
        'noise_recordings': noise,
        'low_confidence_recordings': low_confidence,
        'unclassified_recordings': unclassified,
        'qualified_recordings': sum(totals.values()),
    }


class RecordingIndex:
    def __init__(self, recordings_path, spectrograms_path, timezone):
        self.roots = {
            'recordings': Path(recordings_path).resolve(),
            'spectrograms': Path(spectrograms_path).resolve(),
        }
        # New spectrograms can live beneath recordings; don't count/copy twice.
        if self.roots['spectrograms'].is_relative_to(self.roots['recordings']):
            del self.roots['spectrograms']
        self.timezone = timezone
        self._lock = threading.Lock()
        self._cached = None
        self._cached_at = 0
        self._json_cache = {}

    def inventory(self):
        files, pending, errors = [], 0, []
        for prefix, root in self.roots.items():
            if not root.exists():
                continue

            def on_error(error):
                errors.append(str(error))

            for directory, folders, names in os.walk(
                root, followlinks=False, onerror=on_error
            ):
                folders[:] = sorted(
                    name
                    for name in folders
                    if not name.startswith('.')
                    and not (Path(directory) / name).is_symlink()
                )
                for name in sorted(names):
                    if name.startswith('.') or name.endswith(('.part', '.tmp')):
                        pending += int(name.endswith(('.part', '.tmp')))
                        continue
                    path = Path(directory) / name
                    try:
                        info = path.lstat()
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        files.append(
                            RecordingFile(
                                path,
                                f'{prefix}/{path.relative_to(root).as_posix()}',
                                info.st_size,
                                info.st_mtime_ns,
                                info.st_ino,
                            )
                        )
                    except OSError as error:
                        errors.append(str(error))
        return files, pending, errors

    def media_path(self, relative):
        parts = Path(relative).parts
        if not parts or parts[0] not in self.roots or '..' in parts:
            raise ValueError('Unknown recording')
        root = self.roots[parts[0]]
        path = root.joinpath(*parts[1:])
        if (
            not path.resolve().is_relative_to(root)
            or path.is_symlink()
            or path.suffix.lower() not in IMAGE_EXTENSIONS
            or not path.is_file()
        ):
            raise ValueError('Unknown recording image')
        return path

    def _json(self, item):
        cached = self._json_cache.get(item.relative)
        if cached and cached[0] == item.signature:
            return cached[1]
        result = read_json(item.path)
        if item.path.name.endswith('.metadata.json') and isinstance(result, dict):
            # Per-call metadata can be large. The dashboard only needs these
            # two fields; retaining every call's pixels would exhaust Pi RAM.
            result = {key: result.get(key) for key in ('duration.ms', 'sr.hz')}
        self._json_cache[item.relative] = (item.signature, result)
        return result

    def summary(self, refresh=False):
        with self._lock:
            if (
                not refresh
                and self._cached is not None
                and time.monotonic() - self._cached_at < 15
            ):
                return self._cached
            files, pending, errors = self.inventory()
            groups, results, metadata = defaultdict(list), {}, {}
            for item in files:
                groups[recording_key(item.path)].append(item)
                if item.path.name.endswith(('.results.json', '.metadata.json')):
                    try:
                        data = self._json(item)
                        if item.path.name.endswith('.results.json'):
                            results[item.relative.removesuffix('.results.json')] = data
                        elif isinstance(data, dict):
                            metadata[recording_key(item.path)] = data
                    except (OSError, ValueError, TypeError) as error:
                        errors.append(f'{item.relative}: {error}')
            current_paths = {item.relative for item in files}
            self._json_cache = {
                key: value
                for key, value in self._json_cache.items()
                if key in current_paths
            }
            entries = []
            for group in groups.values():
                wavs = [item for item in group if item.path.suffix.lower() == '.wav']
                if not wavs:
                    continue
                scores, weight_sum = defaultdict(float), 0
                for item in group:
                    if not item.path.name.endswith('.results.json'):
                        continue
                    data = results.get(item.relative.removesuffix('.results.json'), {})
                    ranked = predictions(data)
                    if not ranked:
                        continue
                    weight = (
                        data.get('window_count', 1) if isinstance(data, dict) else 1
                    )
                    if (
                        not isinstance(weight, (int, float))
                        or not math.isfinite(weight)
                        or weight <= 0
                    ):
                        weight = 1
                    weight_sum += weight
                    # Prefer full scores when combining multiple image tiles.
                    full_scores = data.get('scores') if isinstance(data, dict) else None
                    values = (
                        full_scores.items() if isinstance(full_scores, dict) else ranked
                    )
                    for label, confidence in values:
                        if (
                            isinstance(label, str)
                            and isinstance(confidence, (int, float))
                            and math.isfinite(confidence)
                            and 0 <= confidence <= 1
                        ):
                            scores[label] += confidence * weight
                ranked = (
                    sorted(
                        (
                            (label, value / weight_sum)
                            for label, value in scores.items()
                        ),
                        key=lambda pair: -pair[1],
                    )[:5]
                    if weight_sum
                    else []
                )
                entries.extend((timestamp_for(item), ranked) for item in wavs)
            images = sorted(
                (
                    item
                    for item in files
                    if item.path.suffix.lower() in IMAGE_EXTENSIONS
                ),
                key=lambda item: (timestamp_for(item), item.relative),
                reverse=True,
            )
            # Prefer the compressed images consumed by ML; retain unprocessed
            # images when no compressed counterpart exists for that recording.
            compressed_keys = {
                recording_key(item.path)
                for item in images
                if '.compressed.' in item.path.name
            }
            images = [
                item
                for item in images
                if '.compressed.' in item.path.name
                or recording_key(item.path) not in compressed_keys
            ]
            recent = []
            for item in images[:10]:
                result = results.get(item.relative.rsplit('.', 1)[0], {})
                meta = metadata.get(recording_key(item.path), {})
                recent.append(
                    {
                        'name': item.path.name,
                        'timestamp': timestamp_for(item),
                        'bytes': item.size,
                        'image_url': '/api/recordings/image/' + quote(item.relative),
                        'predictions': [
                            {'label': label, 'confidence': value}
                            for label, value in predictions(result)
                        ],
                        'ml_error': result.get('error')
                        if isinstance(result, dict)
                        else None,
                        'window_count': result.get('window_count')
                        if isinstance(result, dict)
                        else None,
                        'duration_ms': meta.get('duration.ms'),
                        'sample_rate': meta.get('sr.hz'),
                    }
                )
            counts = Counter(item.path.suffix.lower() for item in files)
            self._cached = {
                'updated_at': time.time(),
                'timezone': self.timezone,
                'roots': {name: str(path) for name, path in self.roots.items()},
                'total_files': len(files),
                'total_bytes': sum(item.size for item in files),
                'recordings': counts['.wav'],
                'images': sum(counts[ext] for ext in IMAGE_EXTENSIONS),
                'ml_results': sum(
                    item.path.name.endswith('.results.json') for item in files
                ),
                'pending_files': pending,
                'errors': errors[:20],
                'first_recording': min((entry[0] for entry in entries), default=None),
                'last_recording': max((entry[0] for entry in entries), default=None),
                'recent': recent,
                'species': aggregate_species(entries),
            }
            self._cached_at = time.monotonic()
            return self._cached
