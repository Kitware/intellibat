"""Remote clock controls through systemd's narrowly authorized time service."""

import subprocess
import threading
import time
from datetime import timezone


class ClockError(RuntimeError):
    pass


class DeviceClock:
    def __init__(self):
        self._lock = threading.Lock()

    def command(self, *arguments):
        try:
            result = subprocess.run(
                ['timedatectl', '--no-ask-password', *arguments],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as error:
            raise ClockError(
                'Clock controls require timedatectl on the Raspberry Pi.'
            ) from error
        except subprocess.TimeoutExpired as error:
            raise ClockError(
                'The clock service timed out. Refresh the clock status before retrying.'
            ) from error
        except OSError as error:
            raise ClockError(f'Could not access the clock service: {error}') from error
        if result.returncode:
            reason = (
                result.stderr.strip() or 'The system time service rejected the request.'
            )
            raise ClockError(
                f'{reason} Check that the IntelliBat clock permission rule is installed.'
            )
        return result.stdout.strip()

    def snapshot(self):
        status = {
            'timestamp': time.time(),
            'available': False,
            'timezone': None,
            'ntp_enabled': None,
            'synchronized': None,
            'can_ntp': None,
            'error': None,
        }
        try:
            raw = self.command('show', '--property=Timezone,NTP,NTPSynchronized,CanNTP')
            values = dict(
                line.split('=', 1) for line in raw.splitlines() if '=' in line
            )
            status.update(available=True, timezone=values.get('Timezone'))
            for key, field in [
                ('NTP', 'ntp_enabled'),
                ('NTPSynchronized', 'synchronized'),
                ('CanNTP', 'can_ntp'),
            ]:
                status[field] = {'yes': True, 'no': False}.get(values.get(key))
        except ClockError as error:
            status['error'] = str(error)
        status['timestamp'] = time.time()
        return status

    def set_manual(self, instant):
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ClockError('Include a timezone or UTC offset in the requested time.')
        try:
            instant = instant.astimezone(timezone.utc)
        except (ValueError, OverflowError) as error:
            raise ClockError(
                'The requested time is outside the supported date range.'
            ) from error
        if instant.year < 1970:
            raise ClockError('Choose a date on or after January 1, 1970.')
        if not self._lock.acquire(blocking=False):
            raise ClockError(
                'Another clock change is in progress. Please retry shortly.'
            )
        try:
            before = self.snapshot()
            if not before['available'] or before['ntp_enabled'] is None:
                raise ClockError(
                    before['error'] or 'Could not read network time settings.'
                )
            ntp_was_enabled = before['ntp_enabled']
            try:
                if ntp_was_enabled:
                    self.command('set-ntp', 'false')
                # An explicit UTC suffix prevents browser and device timezones
                # from changing the meaning of the supplied instant.
                self.command(
                    'set-time',
                    instant.strftime('%Y-%m-%d %H:%M:%S.%f UTC'),
                )
            except ClockError as error:
                if ntp_was_enabled:
                    try:
                        self.command('set-ntp', 'true')
                    except ClockError as restore_error:
                        raise ClockError(
                            f'{error} Network time could not be restored: {restore_error}'
                        ) from error
                raise
            return self.snapshot()
        finally:
            self._lock.release()

    def enable_network_time(self):
        if not self._lock.acquire(blocking=False):
            raise ClockError(
                'Another clock change is in progress. Please retry shortly.'
            )
        try:
            self.command('set-ntp', 'true')
            return self.snapshot()
        finally:
            self._lock.release()
