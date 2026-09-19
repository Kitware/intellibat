"""Discover external filesystems and copy an immutable, incremental snapshot."""

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from intellibat_config.telemetry import command, disk_status

MANIFEST = '.intellibat-copy-manifest.json'
CHUNK_BYTES = 1024 * 1024
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class StorageError(ValueError):
    pass


def folder_name(value):
    if (
        not isinstance(value, str)
        or len(value.encode()) > 200
        or value.startswith('.')
        or any(character in value for character in '/\\\x00')
        or any(ord(character) < 32 for character in value)
        or value != value.strip()
    ):
        raise StorageError(
            'Choose a single folder name, without slashes or leading dots.'
        )
    return value


class ExternalStorage:
    def __init__(self, source_roots):
        self.source_roots = list(source_roots)

    def list(self):
        raw = command(
            [
                'lsblk',
                '--json',
                '--bytes',
                '--paths',
                '--output',
                'NAME,TYPE,TRAN,RM,HOTPLUG,SIZE,LABEL,UUID,FSTYPE,MOUNTPOINTS,RO,MAJ:MIN',
            ]
        )
        if raw is None:
            return {
                'drives': [],
                'error': 'External drive discovery requires Linux lsblk (util-linux).',
            }
        try:
            devices = json.loads(raw)['blockdevices']
        except (ValueError, KeyError):
            return {
                'drives': [],
                'error': 'Could not read the external drive inventory.',
            }
        drives = []

        def mounts(node):
            return [value for value in node.get('mountpoints', []) if value]

        def system_disk(node):
            return any(
                value in ('/', '/boot', '/boot/firmware') for value in mounts(node)
            ) or any(system_disk(child) for child in node.get('children', []))

        def visit(node, external=False, transport=None):
            transport = node.get('tran') or transport
            external = (
                external
                or node.get('rm') in (True, 1, '1')
                or node.get('hotplug') in (True, 1, '1')
                or transport == 'usb'
            )
            if (
                external
                and node.get('fstype')
                and node.get('type') in ('disk', 'part', 'crypt')
            ):
                mountpoints = mounts(node)
                mount = next(
                    (value for value in mountpoints if value.startswith('/')), None
                )
                # Never export into the source tree or onto the OS filesystem.
                unsafe = mount and any(
                    root.is_relative_to(Path(mount).resolve())
                    or Path(mount).resolve().is_relative_to(root)
                    for root in self.source_roots
                )
                if not unsafe:
                    identity = (
                        f"{node['name']}:{node.get('uuid')}:{node.get('maj:min')}"
                    )
                    info = disk_status(mount) if mount else {}
                    readonly = node.get('ro') in (True, 1, '1')
                    drives.append(
                        {
                            'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                            'device': node['name'],
                            'label': node.get('label') or Path(node['name']).name,
                            'uuid': node.get('uuid'),
                            'filesystem': node.get('fstype'),
                            'transport': transport,
                            'size': node.get('size'),
                            'mountpoint': mount,
                            'readonly': readonly,
                            'writable': bool(
                                mount and not readonly and info.get('writable')
                            ),
                            'free': info.get('free'),
                            'total': info.get('total'),
                            'used': info.get('used'),
                            'error': info.get('error'),
                        }
                    )
            for child in node.get('children', []):
                visit(child, external, transport)

        for device in devices:
            if not system_disk(device):
                visit(device)
        return {
            'drives': list({drive['id']: drive for drive in drives}.values()),
            'error': None,
        }

    def resolve(self, drive_id, writable=True):
        inventory = self.list()
        drive = next(
            (drive for drive in inventory['drives'] if drive['id'] == drive_id), None
        )
        if not drive:
            raise StorageError(
                inventory['error'] or 'The selected drive is no longer connected.'
            )
        if writable and (not drive['mountpoint'] or drive['readonly']):
            raise StorageError(
                'Mount this drive with write access for the intellibat user first.'
            )
        return drive

    def mount(self, drive_id):
        drive = self.resolve(drive_id, writable=False)
        if drive['mountpoint']:
            return drive
        try:
            result = subprocess.run(
                [
                    'udisksctl',
                    'mount',
                    '--no-user-interaction',
                    '--block-device',
                    drive['device'],
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StorageError(
                f'Could not mount drive: {error}. Mount it on the Pi and refresh.'
            ) from error
        if result.returncode:
            raise StorageError(
                f'Mount failed: {result.stderr.strip()[:400]}. Check mount permissions on the Pi.'
            )
        return self.resolve(drive_id)

    def validate_mount(self, drive, device_number):
        mount = Path(drive['mountpoint'])
        if not mount.is_mount() or mount.stat().st_dev != device_number:
            raise StorageError(
                'The destination drive was removed or its mount changed. Reconnect and preview again.'
            )

    @contextmanager
    def directory(self, drive, folder='', create=False, writable=False):
        folder = folder_name(folder)
        mount = Path(drive['mountpoint'])
        descriptor = os.open(mount, DIRECTORY_FLAGS)
        try:
            self.validate_mount(drive, os.fstat(descriptor).st_dev)
            if folder:
                if create:
                    os.mkdir(folder, mode=0o775, dir_fd=descriptor)
                child = os.open(folder, DIRECTORY_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            if writable and not os.access('.', os.W_OK, dir_fd=descriptor):
                raise StorageError(
                    'The selected folder is not writable by the intellibat user. Choose a writable folder or adjust its permissions on the Pi.'
                )
            yield descriptor
        finally:
            os.close(descriptor)

    def folders(self, drive_id):
        drive = self.resolve(drive_id)
        with self.directory(drive) as descriptor:
            with os.scandir(descriptor) as entries:
                folders = sorted(
                    entry.name
                    for entry in entries
                    if not entry.name.startswith('.')
                    and entry.is_dir(follow_symlinks=False)
                )
        return {'drive': drive, 'folders': folders}

    def create_folder(self, drive_id, name):
        if not folder_name(name):
            raise StorageError('Enter a folder name.')
        with self.directory(self.resolve(drive_id), name, create=True):
            pass
        return self.folders(drive_id)


@contextmanager
def parent_directory(root_descriptor, relative, create=False):
    parts = Path(relative).parts
    if (
        not parts
        or any(part in ('..', '.', '') for part in parts)
        or Path(relative).is_absolute()
    ):
        raise StorageError('Invalid recording path.')
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o775, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def signature(info):
    return [info.st_size, info.st_mtime_ns]


def digest_file(descriptor, check=None):
    digest = hashlib.sha256()
    with os.fdopen(descriptor, 'rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b''):
            if check:
                check()
            digest.update(chunk)
    return digest.hexdigest()


def publish_file(directory, temporary, name):
    """Atomic publication without replacing another file, including on FAT/exFAT."""
    if sys.platform == 'linux':
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(directory, os.fsencode(temporary), directory, os.fsencode(name), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), name)
    else:
        os.link(
            temporary,
            name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=directory)


def load_manifest(descriptor):
    try:
        file = os.open(MANIFEST, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        with os.fdopen(file) as stream:
            data = json.load(stream)
        files = data.get('files', {}) if data.get('version') == 1 else {}
        return (
            {key: value for key, value in files.items() if isinstance(value, dict)}
            if isinstance(files, dict)
            else {}
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def sync_directory(descriptor):
    try:
        os.fsync(descriptor)
    except OSError as error:
        if error.errno not in (errno.EINVAL, errno.ENOTSUP):
            raise


def save_manifest(descriptor, manifest):
    temporary = f'.intellibat-manifest-{uuid.uuid4().hex}.tmp'
    try:
        file = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o664,
            dir_fd=descriptor,
        )
        with os.fdopen(file, 'w') as stream:
            json.dump({'version': 1, 'files': manifest}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, MANIFEST, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        sync_directory(descriptor)
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass


class CopyManager:
    """One job at a time; progress persists across browser navigation/reloads."""

    def __init__(self, index, storage):
        self.index, self.storage = index, storage
        self.lock = threading.RLock()
        self.job = None
        self.plan = None
        self.cancelled = threading.Event()

    def snapshot(self, job_id=None):
        with self.lock:
            if job_id and (not self.job or self.job['id'] != job_id):
                raise StorageError(
                    'This copy job is no longer available. Preview a new copy.'
                )
            if not self.job:
                return None
            job = dict(self.job)
            elapsed = time.monotonic() - job.get('_started_monotonic', time.monotonic())
            job.pop('_started_monotonic', None)
            speed = (
                job.get('copied_bytes', 0) / elapsed
                if elapsed > 0 and job['state'] == 'copying'
                else 0
            )
            job['bytes_per_second'] = speed
            job['eta_seconds'] = (
                max(0, job.get('copy_bytes', 0) - job.get('copied_bytes', 0)) / speed
                if speed
                else None
            )
            return job

    def update(self, **values):
        with self.lock:
            self.job.update(values)

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise StorageError(
                'Copy cancelled. Completed files are retained; preview again to copy the rest.'
            )

    def estimate(self, drive_id, folder):
        folder_name(folder)
        drive = self.storage.resolve(drive_id)
        with self.lock:
            if self.job and self.job['state'] in ('estimating', 'copying'):
                raise StorageError('A copy or estimate is already running.')
            self.cancelled.clear()
            self.plan = None
            self.job = {
                'id': uuid.uuid4().hex,
                'state': 'estimating',
                'drive_id': drive_id,
                'drive_label': drive['label'],
                'folder': folder,
                'destination': str(Path(drive['mountpoint']) / folder),
                'created_at': time.time(),
                'scanned_files': 0,
                'copy_files': 0,
                'copy_bytes': 0,
                'copied_bytes': 0,
                'copied_files': 0,
                'skipped_files': 0,
                'conflicts': 0,
                'conflict_examples': [],
                'pending_files': 0,
                'current_file': None,
                'error': None,
            }
            threading.Thread(
                target=self._estimate, args=(drive, folder), daemon=True
            ).start()
            return self.snapshot()

    def _estimate(self, drive, folder):
        try:
            with self.storage.directory(drive, folder, writable=True) as descriptor:
                device_number = os.fstat(descriptor).st_dev
                manifest = load_manifest(descriptor)
                files, pending, errors = self.index.inventory()
                if errors:
                    raise StorageError('Some source files cannot be read: ' + errors[0])
                self.update(total_files=len(files), pending_files=pending)
                planned, conflicts, skipped, examples = [], 0, 0, []
                for position, item in enumerate(files):
                    self.check_cancelled()
                    if (
                        not item.unchanged()
                        or time.time_ns() - item.mtime_ns < 2_000_000_000
                    ):
                        pending += 1
                        continue
                    self.storage.validate_mount(drive, device_number)
                    self.update(current_file=item.relative, scanned_files=position + 1)
                    try:
                        with parent_directory(descriptor, item.relative) as (
                            parent,
                            name,
                        ):
                            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                            cached = manifest.get(item.relative, {})
                            if (
                                stat.S_ISREG(info.st_mode)
                                and cached.get('source') == item.signature
                                and cached.get('destination') == signature(info)
                            ):
                                skipped += 1
                                continue
                            if stat.S_ISREG(info.st_mode) and info.st_size == item.size:
                                destination_hash = digest_file(
                                    os.open(
                                        name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent
                                    ),
                                    self.check_cancelled,
                                )
                                source_hash = digest_file(
                                    os.open(item.path, os.O_RDONLY | os.O_NOFOLLOW),
                                    self.check_cancelled,
                                )
                                if source_hash == destination_hash and item.unchanged():
                                    manifest[item.relative] = {
                                        'source': item.signature,
                                        'destination': signature(info),
                                        'sha256': source_hash,
                                    }
                                    skipped += 1
                                    continue
                            conflicts += 1
                            if len(examples) < 10:
                                examples.append(item.relative)
                    except FileNotFoundError:
                        planned.append(item)
                    except OSError as error:
                        if error.errno in (errno.ELOOP, errno.ENOTDIR):
                            conflicts += 1
                            if len(examples) < 10:
                                examples.append(item.relative)
                        else:
                            raise
                free = (
                    os.fstatvfs(descriptor).f_bavail * os.fstatvfs(descriptor).f_frsize
                )
                copy_bytes = sum(item.size for item in planned)
                block = os.fstatvfs(descriptor).f_frsize or 4096
                required = (
                    sum(((item.size + block - 1) // block) * block for item in planned)
                    + len(planned) * block
                    + max(1024 * 1024, (len(manifest) + len(planned)) * 1024)
                )
                with self.lock:
                    self.plan = (drive, folder, device_number, planned, manifest)
                    self.update(
                        state='ready',
                        copy_files=len(planned),
                        copy_bytes=copy_bytes,
                        required_bytes=required,
                        free_bytes=free,
                        enough_space=required <= free,
                        skipped_files=skipped,
                        conflicts=conflicts,
                        conflict_examples=examples,
                        pending_files=pending,
                        current_file=None,
                        estimated_at=time.time(),
                    )
        except Exception as error:
            self.update(
                state='cancelled' if self.cancelled.is_set() else 'failed',
                error=str(error),
            )

    def start(self, job_id):
        with self.lock:
            self.snapshot(job_id)
            if self.job['state'] != 'ready' or self.plan is None:
                raise StorageError('Preview this copy before starting.')
            if time.time() - self.job['estimated_at'] > 900:
                raise StorageError(
                    'This estimate expired. Preview again before copying.'
                )
            if not self.job['enough_space']:
                raise StorageError(
                    'There is not enough available storage for this copy.'
                )
            self.update(state='copying', _started_monotonic=time.monotonic())
            threading.Thread(target=self._copy, daemon=True).start()
            return self.snapshot()

    def cancel(self, job_id):
        with self.lock:
            self.snapshot(job_id)
            if self.job['state'] in ('estimating', 'copying'):
                self.cancelled.set()
                self.update(cancelling=True)
            return self.snapshot()

    def _copy(self):
        drive, folder, device_number, planned, manifest = self.plan
        copied_bytes = copied_files = 0
        try:
            current_drive = self.storage.resolve(drive['id'])
            if current_drive['mountpoint'] != drive['mountpoint']:
                raise StorageError('The destination mount changed. Preview again.')
            with self.storage.directory(drive, folder, writable=True) as descriptor:
                self.storage.validate_mount(drive, device_number)
                if (
                    shutil.disk_usage(drive['mountpoint']).free
                    < self.job['required_bytes']
                ):
                    raise StorageError('Available storage changed. Preview again.')
                try:
                    for item in planned:
                        self.check_cancelled()
                        self.storage.validate_mount(drive, device_number)
                        if not item.unchanged():
                            raise StorageError(
                                f'{item.relative} changed since the estimate. Preview again.'
                            )
                        self.update(current_file=item.relative)
                        with parent_directory(
                            descriptor, item.relative, create=True
                        ) as (parent, name):
                            temporary = f'.intellibat-{self.job["id"]}.part'
                            try:
                                output = os.open(
                                    temporary,
                                    os.O_WRONLY
                                    | os.O_CREAT
                                    | os.O_EXCL
                                    | os.O_NOFOLLOW,
                                    0o664,
                                    dir_fd=parent,
                                )
                                digest = hashlib.sha256()
                                with os.fdopen(output, 'wb') as outgoing:
                                    source = os.open(
                                        item.path, os.O_RDONLY | os.O_NOFOLLOW
                                    )
                                    with os.fdopen(source, 'rb') as incoming:
                                        remaining = item.size
                                        while remaining:
                                            self.check_cancelled()
                                            self.storage.validate_mount(
                                                drive, device_number
                                            )
                                            chunk = incoming.read(
                                                min(CHUNK_BYTES, remaining)
                                            )
                                            if not chunk:
                                                raise StorageError(
                                                    f'{item.relative} changed during copying.'
                                                )
                                            outgoing.write(chunk)
                                            digest.update(chunk)
                                            copied_bytes += len(chunk)
                                            remaining -= len(chunk)
                                            self.update(copied_bytes=copied_bytes)
                                    outgoing.flush()
                                    os.utime(
                                        outgoing.fileno(),
                                        ns=(item.mtime_ns, item.mtime_ns),
                                    )
                                    os.fsync(outgoing.fileno())
                                self.check_cancelled()
                                if not item.unchanged():
                                    raise StorageError(
                                        f'{item.relative} changed during copying. Preview again.'
                                    )
                                publish_file(parent, temporary, name)
                                sync_directory(parent)
                                info = os.stat(
                                    name, dir_fd=parent, follow_symlinks=False
                                )
                                manifest[item.relative] = {
                                    'source': item.signature,
                                    'destination': signature(info),
                                    'sha256': digest.hexdigest(),
                                }
                                copied_files += 1
                                self.update(copied_files=copied_files)
                            finally:
                                try:
                                    os.unlink(temporary, dir_fd=parent)
                                except FileNotFoundError:
                                    pass
                        # Checkpoint periodically; a missing checkpoint only makes
                        # the next estimate hash existing files, never recopy them.
                        if copied_files % 100 == 0:
                            save_manifest(descriptor, manifest)
                finally:
                    self.storage.validate_mount(drive, device_number)
                    save_manifest(descriptor, manifest)
                self.update(
                    state='complete', completed_at=time.time(), current_file=None
                )
        except Exception as error:
            self.update(
                state='cancelled' if self.cancelled.is_set() else 'failed',
                error=str(error),
            )
