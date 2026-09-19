"""Read-only Linux and Raspberry Pi telemetry; missing sensors stay unknown."""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path


def command(arguments, timeout=2):
    try:
        result = subprocess.run(
            arguments, capture_output=True, text=True, timeout=timeout, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def read_text(path):
    try:
        return Path(path).read_text().strip().strip('\x00')
    except (OSError, UnicodeError):
        return None


def number(path, divisor=1):
    try:
        return float(read_text(path)) / divisor
    except (TypeError, ValueError):
        return None


def disk_status(path):
    path = Path(path)
    exists = path.exists()
    parent = path
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    try:
        usage = shutil.disk_usage(parent)
        vfs = os.statvfs(parent)
        return {
            'path': str(path),
            'exists': exists,
            'total': usage.total,
            'used': usage.used,
            'free': usage.free,
            'used_percent': round(usage.used / usage.total * 100, 1)
            if usage.total
            else 0,
            'inodes_free': vfs.f_favail,
            'inodes_total': vfs.f_files,
            'writable': exists and os.access(path, os.W_OK),
        }
    except OSError as error:
        return {'path': str(path), 'error': str(error)}


def power_supplies(root=Path('/sys/class/power_supply')):
    supplies = []
    for path in sorted(root.glob('*')):
        supply = {'name': path.name}
        for field in (
            'type',
            'status',
            'health',
            'model_name',
            'manufacturer',
            'technology',
        ):
            supply[field] = read_text(path / field)
        for field, divisor in {
            'capacity': 1,
            'voltage_now': 1e6,
            'current_now': 1e6,
            'power_now': 1e6,
            'energy_now': 1e6,
            'energy_full': 1e6,
            'charge_now': 1e6,
            'charge_full': 1e6,
            'temp': 10,
            'cycle_count': 1,
            'time_to_empty_now': 1,
            'time_to_full_now': 1,
            'online': 1,
            'present': 1,
        }.items():
            supply[field] = number(path / field, divisor)
        supplies.append(supply)
    return supplies


def service_status(name):
    properties = (
        'Id,LoadState,ActiveState,SubState,Result,MainPID,NRestarts,'
        'ActiveEnterTimestamp,MemoryCurrent,CPUUsageNSec,ExecMainStatus'
    )
    raw = command(['systemctl', 'show', name, f'--property={properties}', '--no-pager'])
    if raw is None:
        return {'name': name, 'available': False, 'active': 'unknown'}
    values = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    return {
        'name': name,
        'available': True,
        'active': values.get('ActiveState'),
        'substate': values.get('SubState'),
        'load': values.get('LoadState'),
        'result': values.get('Result'),
        'pid': values.get('MainPID'),
        'restarts': values.get('NRestarts'),
        'since': values.get('ActiveEnterTimestamp'),
        'memory_bytes': values.get('MemoryCurrent'),
        'cpu_ns': values.get('CPUUsageNSec'),
        'exit_status': values.get('ExecMainStatus'),
    }


class SystemTelemetry:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._cached_at = 0
        self._cached = None
        self._cpu_sample = None

    def cpu_usage(self):
        raw = read_text('/proc/stat')
        if not raw:
            return None
        values = [int(value) for value in raw.splitlines()[0].split()[1:9]]
        current = (sum(values), values[3] + values[4])
        previous, self._cpu_sample = self._cpu_sample, current
        if previous is None or current[0] <= previous[0]:
            return None
        return round(
            100 * (1 - (current[1] - previous[1]) / (current[0] - previous[0])), 1
        )

    def snapshot(self):
        with self._lock:
            if self._cached is not None and time.monotonic() - self._cached_at < 4:
                return self._cached
            memory = {}
            for line in (read_text('/proc/meminfo') or '').splitlines():
                key, value = line.split(':', 1)
                memory[key] = int(value.strip().split()[0]) * 1024
            total = memory.get('MemTotal')
            available = memory.get('MemAvailable')
            sensors = [
                {
                    'name': read_text(path / 'type') or path.name,
                    'celsius': number(path / 'temp', 1000),
                }
                for path in Path('/sys/class/thermal').glob('thermal_zone*')
            ]
            fans = [
                {
                    'name': read_text(path.parent / 'name') or path.parent.name,
                    'rpm': number(path),
                }
                for path in Path('/sys/class/hwmon').glob('hwmon*/fan*_input')
            ]
            throttled_raw = command(['vcgencmd', 'get_throttled'])
            flags = None
            if throttled_raw:
                match = re.search(r'0x[0-9a-fA-F]+', throttled_raw)
                flags = int(match[0], 16) if match else None
            flag_names = {
                0: 'Under-voltage',
                1: 'CPU frequency capped',
                2: 'Throttling',
                3: 'Soft temperature limit',
            }
            pmic = command(['vcgencmd', 'pmic_read_adc'])
            power_rails = []
            for line in (pmic or '').splitlines():
                match = re.search(r'(\w+)\s+[^=]*=\s*([-\d.]+)([AV])', line)
                if match:
                    power_rails.append(
                        {'name': match[1], 'value': float(match[2]), 'unit': match[3]}
                    )
            interfaces = []
            ip_raw = command(['ip', '-j', 'address', 'show'])
            try:
                for interface in json.loads(ip_raw or '[]'):
                    name = interface['ifname']
                    if name == 'lo':
                        continue
                    interfaces.append(
                        {
                            'name': name,
                            'state': interface.get('operstate'),
                            'addresses': [
                                item['local'] for item in interface.get('addr_info', [])
                            ],
                            'received_bytes': number(
                                Path('/sys/class/net') / name / 'statistics/rx_bytes'
                            ),
                            'transmitted_bytes': number(
                                Path('/sys/class/net') / name / 'statistics/tx_bytes'
                            ),
                        }
                    )
            except (ValueError, KeyError, TypeError):
                pass
            runtime = None
            runtime_error = None
            try:
                runtime = json.loads(
                    Path(self.settings.intellibat_telemetry_path).read_text()
                )
                runtime['age_seconds'] = max(
                    0, time.time() - float(runtime['updated_at'])
                )
                runtime['stale'] = runtime['age_seconds'] > 20
                last_audio = runtime.get('last_audio_at')
                runtime['audio_stalled'] = bool(
                    runtime.get('streaming')
                    and (not last_audio or time.time() - last_audio > 10)
                )
            except (OSError, ValueError, KeyError, TypeError) as error:
                runtime_error = f'Recorder heartbeat unavailable: {error}'
                runtime = None
            uptime_raw = read_text('/proc/uptime')
            try:
                uptime = float(uptime_raw.split()[0]) if uptime_raw else None
            except ValueError:
                uptime = None
            self._cached = {
                'updated_at': time.time(),
                'hostname': socket.gethostname(),
                'model': read_text('/proc/device-tree/model') or platform.machine(),
                'os': platform.platform(),
                'kernel': platform.release(),
                'architecture': platform.machine(),
                'cpu_count': os.cpu_count(),
                'cpu_percent': self.cpu_usage(),
                'load_average': list(os.getloadavg()),
                'cpu_mhz': number(
                    '/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq', 1000
                ),
                'uptime_seconds': uptime,
                'clock_synchronized': command(
                    ['timedatectl', 'show', '--property=NTPSynchronized', '--value']
                ),
                'memory': {
                    'total': total,
                    'available': available,
                    'used': total - available
                    if total is not None and available is not None
                    else None,
                    'swap_total': memory.get('SwapTotal'),
                    'swap_free': memory.get('SwapFree'),
                },
                'temperatures': sensors,
                'fans': fans,
                'power_supplies': power_supplies(),
                'throttling': {
                    'available': flags is not None,
                    'raw': throttled_raw,
                    'current': [
                        name
                        for bit, name in flag_names.items()
                        if flags is not None and flags & (1 << bit)
                    ],
                    'since_boot': [
                        name
                        for bit, name in flag_names.items()
                        if flags is not None and flags & (1 << (bit + 16))
                    ],
                },
                'power_rails': power_rails,
                'storage': [
                    disk_status('/'),
                    disk_status(self.settings.intellibat_recordings_path),
                    disk_status(self.settings.intellibat_spectrograms_path),
                ],
                'interfaces': interfaces,
                'services': [
                    service_status('intellibat.service'),
                    service_status('intellibat_config.service'),
                ],
                'serial_devices': [
                    {'path': path, 'present': Path(path).exists()}
                    for path in ('/dev/ttyACM0', '/dev/ttyAMA1')
                ],
                'runtime': runtime,
                'runtime_error': runtime_error,
            }
            self._cached_at = time.monotonic()
            return self._cached
