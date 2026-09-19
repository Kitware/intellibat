"""Run with PYTHONPATH=intellibat_config/src python -m unittest discover -s intellibat_config/tests."""

import ast
import json
import math
import os
import shutil
import tempfile
import threading
import time
import unittest
import wave
from collections import defaultdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import patch

from intellibat_config.recordings import RecordingIndex, aggregate_species
from intellibat_config.storage import (
    CopyManager,
    ExternalStorage,
    StorageError,
    folder_name,
)
from intellibat_config.telemetry import SystemTelemetry, power_supplies

ROOT = Path(__file__).resolve().parents[2]


def write_file(path, contents=b'audio', age=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    timestamp = time.time() - age
    os.utime(path, (timestamp, timestamp))
    return path


class TestStorage(ExternalStorage):
    """A temporary directory acting as a removable filesystem in tests only."""

    def __init__(self, root, source_roots):
        super().__init__(source_roots)
        self.root = root
        self.connected = True

    def list(self):
        usage = shutil.disk_usage(self.root)
        return {
            'error': None,
            'drives': [
                {
                    'id': 'test-drive',
                    'label': 'Test drive',
                    'device': '/dev/test-only',
                    'filesystem': 'test',
                    'mountpoint': str(self.root),
                    'writable': True,
                    'readonly': False,
                    'size': usage.total,
                    'free': usage.free,
                    'total': usage.total,
                }
            ]
            if self.connected
            else [],
        }

    def validate_mount(self, drive, device_number):
        if not self.connected or self.root.stat().st_dev != device_number:
            raise StorageError('Test drive disconnected')


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recordings = self.root / 'recordings'
        self.output = self.root / 'output'
        self.drive = self.root / 'drive'
        for path in (self.recordings, self.output, self.drive):
            path.mkdir()
        self.index = RecordingIndex(self.recordings, self.output, 'America/Los_Angeles')
        self.storage = TestStorage(self.drive, self.index.roots.values())
        self.copies = CopyManager(self.index, self.storage)

    def wait_job(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.copies.snapshot()
            if job['state'] not in ('estimating', 'copying'):
                return job
            time.sleep(0.01)
        self.fail(f'Job did not finish: {self.copies.snapshot()}')

    def estimate(self, folder=''):
        self.copies.estimate('test-drive', folder)
        job = self.wait_job()
        self.assertEqual(job['state'], 'ready', job)
        return job

    def copy(self):
        self.copies.start(self.copies.snapshot()['id'])
        job = self.wait_job()
        self.assertEqual(job['state'], 'complete', job)
        return job


class RecordingTests(WorkspaceTest):
    def test_inventory_groups_tiles_and_reads_legacy_output(self):
        write_file(self.recordings / 'chunk_1787130104.wav')
        write_file(self.recordings / 'chunk_1787130105.wav.part')
        for index, label in [(1, 'LANO'), (2, 'LANO')]:
            stem = f'chunk_1787130104.{index:02}of02.compressed'
            write_file(self.output / f'{stem}.jpg', b'image')
            result = {
                'label': label,
                'confidence': 0.8,
                'window_count': 3,
                'top': [
                    {'label': label, 'confidence': 0.8},
                    {'label': 'LACI', 'confidence': 0.2},
                ],
                'scores': {label: 0.8, 'LACI': 0.2},
            }
            write_file(
                self.output / f'{stem}.results.json', json.dumps(result).encode()
            )
        write_file(
            self.output / 'chunk_1787130104.metadata.json',
            b'{"sr.hz":384000,"duration.ms":5000}',
        )
        summary = self.index.summary()
        self.assertEqual(summary['total_files'], 6)
        self.assertEqual(summary['recordings'], 1)
        self.assertEqual(summary['pending_files'], 1)
        self.assertEqual(summary['species']['histogram'][0]['count'], 1)
        self.assertEqual(len(summary['recent']), 2)
        self.assertEqual(summary['recent'][0]['predictions'][0]['label'], 'LANO')
        self.assertEqual(summary['recent'][0]['duration_ms'], 5000)

    def test_recent_is_limited_and_malformed_results_do_not_break_page(self):
        for index in range(12):
            stem = f'chunk_{1787130104 + index}.01of01.compressed'
            write_file(self.output / f'{stem}.jpg', b'image')
        write_file(
            self.output / 'chunk_1787130115.01of01.compressed.results.json',
            b'{unfinished',
        )
        summary = self.index.summary()
        self.assertEqual(len(summary['recent']), 10)
        self.assertIn('1787130115', summary['recent'][0]['name'])
        self.assertTrue(summary['errors'])

    def test_symlink_and_media_traversal(self):
        secret = write_file(self.root / 'outside.jpg', b'private')
        (self.recordings / 'link.jpg').symlink_to(secret)
        self.assertEqual(self.index.summary()['total_files'], 0)
        for relative in (
            'recordings/../outside.jpg',
            'recordings/link.jpg',
            '/etc/passwd',
        ):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.index.media_path(relative)

    def test_matches_species_timeline_confidence_and_count_semantics(self):
        source = ast.parse((ROOT / 'scripts/species_timeline.py').read_text())
        selected = ast.Module(
            body=[
                node
                for node in source.body
                if isinstance(node, ast.FunctionDef)
                and node.name in ('aggregate_windows', 'window_level')
            ],
            type_ignores=[],
        )
        namespace = {
            'defaultdict': defaultdict,
            'math': math,
            'MIN_CONFIDENCE': 0.1,
            'WINDOW_SECONDS': 900,
        }
        exec(compile(selected, 'species_timeline.py', 'exec'), namespace)
        entries = [
            (1800, [('LANO', 0.51), ('LACI', 0.25)]),
            (1800, [('NOISE', 0.7), ('LANO', 0.3)]),
            (1850, [('LACI', 0.5), ('LANO', 0.25)]),
            (2700, [('LANO', 0.25)]),
            (3600, [('LANO', 0.10)]),
            (4500, [('LACI', 0.09)]),
            (5400, []),
        ]
        expected = namespace['aggregate_windows'](entries, ('NOISE',))
        labels, windows, _, _, levels, _, top1, counts, totals = expected
        actual = aggregate_species(entries)
        by_window = {item['timestamp']: item for item in actual['bins']}
        self.assertEqual(actual['labels'], labels)
        for position, window in enumerate(windows):
            bucket = by_window.get(window, {'detections': 0, 'species': {}})
            self.assertEqual(bucket['detections'], totals[position])
            for label in labels:
                value = bucket['species'].get(
                    label, {'level': 0, 'top1': False, 'count': 0}
                )
                self.assertEqual(value['level'], levels[label][position])
                self.assertEqual(value['top1'], top1[label][position])
                self.assertEqual(value['count'], counts[label][position])


class CopyTests(WorkspaceTest):
    def test_writable_child_on_drive_with_unwritable_root(self):
        write_file(self.recordings / 'a.wav')
        (self.drive / 'Writable folder').mkdir()
        self.drive.chmod(0o555)
        self.addCleanup(self.drive.chmod, 0o755)
        inventory = self.storage.list()
        inventory['drives'][0]['writable'] = False
        with patch.object(self.storage, 'list', return_value=inventory):
            job = self.estimate('Writable folder')
            self.assertEqual(job['copy_files'], 1)
            self.copy()

    def test_repeat_copy_adds_only_new_files_and_new_ml_results(self):
        write_file(self.recordings / 'chunk_1787130104.wav', b'audio')
        write_file(self.output / 'chunk_1787130104.01of01.compressed.jpg', b'image')
        self.storage.create_folder('test-drive', 'Survey')
        job = self.estimate('Survey')
        self.assertEqual((job['copy_files'], job['copy_bytes']), (2, 10))
        complete = self.copy()
        self.assertEqual((complete['copied_files'], complete['copied_bytes']), (2, 10))
        original = (
            (self.drive / 'Survey/recordings/chunk_1787130104.wav').stat().st_mtime_ns
        )
        write_file(self.recordings / 'chunk_1787133704.wav', b'new audio')
        write_file(
            self.output / 'chunk_1787130104.01of01.compressed.results.json', b'{}'
        )
        with patch(
            'intellibat_config.storage.digest_file',
            side_effect=AssertionError(
                'Manifest should avoid rehashing unchanged files'
            ),
        ):
            job = self.estimate('Survey')
        self.assertEqual((job['copy_files'], job['skipped_files']), (2, 2))
        self.copy()
        self.assertEqual(
            (self.drive / 'Survey/recordings/chunk_1787130104.wav').stat().st_mtime_ns,
            original,
        )
        self.assertEqual(self.estimate('Survey')['copy_files'], 0)

    def test_existing_different_files_are_not_overwritten(self):
        write_file(self.recordings / 'same.wav', b'source')
        target = write_file(self.drive / 'recordings/same.wav', b'other!')
        job = self.estimate()
        self.assertEqual((job['conflicts'], job['copy_files']), (1, 0))
        self.copy()
        self.assertEqual(target.read_bytes(), b'other!')

    def test_existing_identical_files_without_manifest_are_skipped(self):
        write_file(self.recordings / 'same.wav', b'content')
        write_file(self.drive / 'recordings/same.wav', b'content')
        job = self.estimate()
        self.assertEqual((job['skipped_files'], job['copy_files']), (1, 0))

    def test_active_and_newly_modified_files_are_deferred(self):
        write_file(self.recordings / 'active.wav.part', b'active')
        write_file(self.output / 'new.jpg', b'image', age=0)
        job = self.estimate()
        self.assertEqual(job['pending_files'], 2)
        self.assertEqual(job['copy_files'], 0)

    def test_changed_source_requires_another_estimate(self):
        source = write_file(self.recordings / 'source.wav')
        job = self.estimate()
        source.write_bytes(b'changed after estimate')
        self.copies.start(job['id'])
        self.assertEqual(self.wait_job()['state'], 'failed')
        self.assertFalse((self.drive / 'recordings/source.wav').exists())

    def test_destination_created_after_preview_is_preserved(self):
        write_file(self.recordings / 'source.wav')
        job = self.estimate()
        target = write_file(
            self.drive / 'recordings/source.wav', b'created by another process'
        )
        self.copies.start(job['id'])
        self.assertEqual(self.wait_job()['state'], 'failed')
        self.assertEqual(target.read_bytes(), b'created by another process')
        self.assertFalse(list(self.drive.rglob('*.part')))

    def test_disconnect_during_copy_cleans_incomplete_file(self):
        write_file(self.recordings / 'large.wav', b'x' * (3 * 1024 * 1024))
        job = self.estimate()
        original = self.storage.validate_mount

        def disconnect(drive, device_number):
            if self.copies.snapshot()['copied_bytes']:
                self.storage.connected = False
            original(drive, device_number)

        with patch.object(self.storage, 'validate_mount', side_effect=disconnect):
            self.copies.start(job['id'])
            self.assertEqual(self.wait_job()['state'], 'failed')
        self.assertFalse((self.drive / 'recordings/large.wav').exists())
        self.assertFalse(list(self.drive.rglob('*.part')))

    def test_cancellation_keeps_completed_files_and_can_resume(self):
        write_file(self.recordings / 'a.wav', b'a')
        write_file(self.recordings / 'b.wav', b'b' * (3 * 1024 * 1024))
        job = self.estimate()
        original = self.storage.validate_mount

        def cancel(drive, device_number):
            if self.copies.snapshot()['copied_bytes'] > 1:
                self.copies.cancel(job['id'])
            original(drive, device_number)

        with patch.object(self.storage, 'validate_mount', side_effect=cancel):
            self.copies.start(job['id'])
            self.assertEqual(self.wait_job()['state'], 'cancelled')
        self.assertTrue((self.drive / 'recordings/a.wav').exists())
        self.assertFalse((self.drive / 'recordings/b.wav').exists())
        job = self.estimate()
        self.assertEqual((job['copy_files'], job['skipped_files']), (1, 1))
        self.copy()

    def test_insufficient_space_and_expired_estimate(self):
        write_file(self.recordings / 'a.wav')
        job = self.estimate()
        self.copies.update(enough_space=False)
        with self.assertRaises(StorageError):
            self.copies.start(job['id'])
        self.copies.update(enough_space=True, estimated_at=time.time() - 901)
        with self.assertRaises(StorageError):
            self.copies.start(job['id'])

    def test_folder_traversal_and_destination_symlinks(self):
        for name in ('..', '../elsewhere', '/tmp', 'a/b', 'a\\b', '.hidden'):
            with self.subTest(name=name), self.assertRaises(StorageError):
                folder_name(name)
        (self.drive / 'Escape').symlink_to(self.root, target_is_directory=True)
        self.copies.estimate('test-drive', 'Escape')
        self.assertEqual(self.wait_job()['state'], 'failed')
        write_file(self.recordings / 'a.wav')
        (self.drive / 'recordings').symlink_to(self.output, target_is_directory=True)
        job = self.estimate()
        self.assertEqual((job['copy_files'], job['conflicts']), (0, 1))


class HardwareTests(WorkspaceTest):
    def test_missing_and_stale_heartbeat_are_explicit(self):
        settings = SimpleNamespace(
            intellibat_telemetry_path=str(self.root / 'heartbeat.json'),
            intellibat_recordings_path=str(self.recordings),
            intellibat_spectrograms_path=str(self.output),
        )
        with patch('intellibat_config.telemetry.command', return_value=None):
            status = SystemTelemetry(settings).snapshot()
        self.assertIsNone(status['runtime'])
        self.assertTrue(status['runtime_error'])
        self.assertFalse(status['services'][0]['available'])
        self.assertFalse(status['throttling']['available'])
        write_file(
            Path(settings.intellibat_telemetry_path),
            json.dumps(
                {
                    'updated_at': time.time() - 30,
                    'streaming': True,
                    'last_audio_at': time.time() - 20,
                }
            ).encode(),
        )
        with patch('intellibat_config.telemetry.command', return_value=None):
            status = SystemTelemetry(settings).snapshot()
        self.assertTrue(status['runtime']['stale'])
        self.assertTrue(status['runtime']['audio_stalled'])

    def test_battery_units_and_missing_sensors(self):
        battery = self.root / 'power/BAT0'
        values = {
            'type': 'Battery',
            'status': 'Discharging',
            'capacity': '73',
            'voltage_now': '7400000',
            'current_now': '1250000',
            'energy_now': '18000000',
            'temp': '285',
        }
        for key, value in values.items():
            write_file(battery / key, value.encode())
        supply = power_supplies(self.root / 'power')[0]
        self.assertEqual(supply['capacity'], 73)
        self.assertEqual(supply['voltage_now'], 7.4)
        self.assertEqual(supply['current_now'], 1.25)
        self.assertEqual(supply['energy_now'], 18)
        self.assertEqual(supply['temp'], 28.5)
        self.assertIsNone(supply['time_to_empty_now'])
        self.assertEqual(power_supplies(self.root / 'missing'), [])

    def test_discovery_excludes_usb_boot_drive_and_inherits_usb_transport(self):
        payload = {
            'blockdevices': [
                {
                    'name': '/dev/sda',
                    'tran': 'usb',
                    'children': [
                        {
                            'name': '/dev/sda1',
                            'type': 'part',
                            'fstype': 'ext4',
                            'mountpoints': ['/'],
                        }
                    ],
                },
                {
                    'name': '/dev/sdb',
                    'tran': 'usb',
                    'children': [
                        {
                            'name': '/dev/sdb1',
                            'type': 'part',
                            'fstype': 'exfat',
                            'mountpoints': [None],
                            'uuid': 'external',
                        }
                    ],
                },
                {
                    'name': '/dev/mmcblk0',
                    'tran': None,
                    'rm': False,
                    'type': 'disk',
                    'fstype': 'ext4',
                    'mountpoints': [None],
                },
            ]
        }
        with patch(
            'intellibat_config.storage.command', return_value=json.dumps(payload)
        ):
            data = ExternalStorage(self.index.roots.values()).list()
        self.assertEqual([drive['device'] for drive in data['drives']], ['/dev/sdb1'])
        self.assertFalse(data['drives'][0]['writable'])


class WebTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        from intellibat_config import ConfigManager

        self.config = self.root / 'config.json'
        self.config.write_text((ROOT / 'software/default_config.json').read_text())
        with patch.dict(os.environ, {'INTELLIBAT_CONFIG_PATH': str(self.config)}):
            from intellibat_config.main import app
        from fastapi.testclient import TestClient

        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for target, value in [
            (
                'intellibat_config.api.config_manager',
                ConfigManager.from_file(self.config),
            ),
            ('intellibat_config.dashboard.index', self.index),
            ('intellibat_config.dashboard.storage', self.storage),
            ('intellibat_config.dashboard.copies', self.copies),
        ]:
            manager = patch(target, value)
            manager.start()
            self.addCleanup(manager.stop)

    def test_default_status_tab_and_all_pages(self):
        response = self.client.get('/', follow_redirects=False)
        self.assertEqual(response.headers['location'], '/status')
        for path, label in [
            ('/config/', 'Settings'),
            ('/status', 'System status'),
            ('/data', 'Recorded data'),
        ]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'aria-current="page">{label}', response.text)
            self.assertLess(
                response.text.index('>Settings</a>'),
                response.text.index('>System status</a>'),
            )
            self.assertLess(
                response.text.index('>System status</a>'),
                response.text.index('>Recorded data</a>'),
            )
        self.assertEqual(self.client.get('/static/app.js').status_code, 200)

    def test_settings_keep_led_option_and_return_to_settings(self):
        data = json.loads(self.config.read_text())
        form = {
            key: str(value)
            for key, value in data.items()
            if not isinstance(value, bool)
        }
        form.update(
            {
                key: 'on'
                for key, value in data.items()
                if value is True and key != 'led_enabled'
            }
        )
        response = self.client.post('/config/', data=form, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/config/?saved=1')
        self.assertFalse(json.loads(self.config.read_text())['led_enabled'])
        form['led_enabled'] = 'on'
        self.client.post('/config/', data=form)
        self.assertTrue(json.loads(self.config.read_text())['led_enabled'])
        form['sample_rate'] = 'invalid'
        self.assertEqual(self.client.post('/config/', data=form).status_code, 400)

    def test_media_and_copy_api(self):
        write_file(self.recordings / 'a.wav')
        write_file(self.output / 'a.jpg', b'image')
        self.assertEqual(self.client.get('/api/recordings').json()['total_files'], 2)
        self.assertEqual(
            self.client.get('/api/recordings/image/spectrograms/a.jpg').content,
            b'image',
        )
        self.assertEqual(
            self.client.get('/api/recordings/image/recordings/a.wav').status_code, 404
        )
        response = self.client.post(
            '/api/copies/estimate', json={'drive_id': 'test-drive', 'folder': ''}
        )
        self.assertEqual(response.status_code, 202)
        job = self.wait_job()
        self.assertEqual(self.client.get('/api/copies/current').json()['id'], job['id'])
        self.assertEqual(
            self.client.post(f'/api/copies/{job["id"]}/start', json={}).status_code, 202
        )
        self.assertEqual(self.wait_job()['state'], 'complete')
        self.assertEqual(self.client.get('/config/health').json(), {'status': 'ok'})

    def test_cross_site_mutation_and_invalid_folder_rejected(self):
        response = self.client.post(
            '/api/storage/folders',
            json={'drive_id': 'test-drive', 'folder': 'outside'},
            headers={'Origin': 'https://other.invalid'},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            '/api/storage/folders',
            json={'drive_id': 'test-drive', 'folder': '../outside'},
        )
        self.assertEqual(response.status_code, 400)


class RecorderTests(WorkspaceTest):
    def service_definitions(self, names, namespace):
        # service.py opens serial ports at import; isolate the exact production
        # classes while exercising real WAVs, queues and heartbeat files.
        tree = ast.parse((ROOT / 'software/service.py').read_text())
        module = ast.Module(
            body=[
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name in names
            ],
            type_ignores=[],
        )
        exec(compile(module, 'service.py', 'exec'), namespace)
        return namespace

    def test_wav_is_published_only_after_recording_closes(self):
        runtime = {'recordings_completed': 0}
        namespace = self.service_definitions(
            {'RecordingState', 'RecordingStateMachine'},
            {'Enum': Enum, 'time': time, 'os': os, 'RUNTIME_STATUS': runtime},
        )
        queue = Queue()
        config = SimpleNamespace(
            config=SimpleNamespace(
                maximum_recording_length=5, triggered_recording=True, trigger_window=1
            )
        )
        recorder = namespace['RecordingStateMachine'](config, queue)
        target = self.recordings / 'chunk_1787130104.wav'
        stream = wave.open(str(target) + '.part', 'wb')
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(256000)
        recorder.begin_recording(stream, str(target))
        recorder.handle_chunk(b'\x00\x00' * 128, True)
        self.assertFalse(target.exists())
        self.assertEqual(self.index.inventory()[1], 1)
        recorder.stop_recording()
        self.assertTrue(target.exists())
        self.assertFalse(Path(str(target) + '.part').exists())
        with wave.open(str(target)) as completed:
            self.assertEqual(completed.getnframes(), 128)
        self.assertEqual(queue.get_nowait(), str(target))
        self.assertEqual(runtime['recordings_completed'], 1)
        self.assertFalse(runtime['recording'])

    def test_heartbeat_reports_live_configuration_and_workers(self):
        from intellibat_config import IntellibatConfig

        config = IntellibatConfig.model_validate_json(
            (ROOT / 'software/default_config.json').read_text()
        )
        config.led_enabled = False
        path = self.root / 'runtime/status.json'
        namespace = self.service_definitions(
            {'Telemetry'},
            {
                'Thread': threading.Thread,
                'threading': threading,
                'time': time,
                'os': os,
                'json': json,
                'RUNTIME_STATUS': {'recording': False},
                'config_manager': SimpleNamespace(config=config),
                'DEVICE_STREAMING': True,
                'CONFIGURED_SAMPLE_RATE': config.sample_rate,
                'PENDING_SAMPLE_BYTES': b'\x00\x00',
                'LAST_DROPPED_SAMPLES': 7,
                'INCOMING': Queue(),
                'OUTGOING': Queue(),
                'recording_schedule': SimpleNamespace(
                    start_time=config.start_time, end_time=config.end_time
                ),
                'LAST_RELOAD_TIME': datetime.now(),
                'WORKER_THREADS': [threading.current_thread()],
                'TELEMETRY_PATH': path,
            },
        )
        worker = namespace['Telemetry']()
        worker.stopped.wait = lambda seconds: worker.stopped.set()
        worker.run()
        status = json.loads(path.read_text())
        self.assertFalse(status['led_enabled'])
        self.assertEqual(status['dropped_samples'], 7)
        self.assertEqual(status['buffered_bytes'], 2)
        self.assertTrue(status['threads']['MainThread'])
        self.assertFalse(path.with_suffix('.tmp').exists())


if __name__ == '__main__':
    unittest.main()
