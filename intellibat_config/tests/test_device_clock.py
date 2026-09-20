import subprocess
import unittest
from datetime import datetime
from unittest.mock import patch

from intellibat_config.device_clock import ClockError, DeviceClock


class DeviceClockTests(unittest.TestCase):
    def setUp(self):
        self.clock = DeviceClock()
        self.ntp = True
        self.calls = []
        self.failure = None
        patcher = patch(
            'intellibat_config.device_clock.subprocess.run',
            side_effect=self.run_command,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_command(self, arguments, **kwargs):
        self.calls.append(arguments)
        self.assertEqual(arguments[:2], ['timedatectl', '--no-ask-password'])
        self.assertNotIn('shell', kwargs)
        action = arguments[2]
        if action == 'show':
            output = f'Timezone=Etc/UTC\nCanNTP=yes\nNTP={"yes" if self.ntp else "no"}\nNTPSynchronized=no'
        elif action == 'set-ntp':
            self.ntp = arguments[3] == 'true'
            output = ''
        else:
            if self.failure:
                raise self.failure
            output = ''
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr='')

    def test_manual_time_uses_explicit_utc_and_disables_ntp(self):
        result = self.clock.set_manual(
            datetime.fromisoformat('2026-09-19T12:34:56-07:00')
        )
        self.assertFalse(result['ntp_enabled'])
        self.assertIn(
            [
                'timedatectl',
                '--no-ask-password',
                'set-time',
                '2026-09-19 19:34:56.000000 UTC',
            ],
            self.calls,
        )
        self.assertEqual(
            [call[2] for call in self.calls], ['show', 'set-ntp', 'set-time', 'show']
        )

    def test_ntp_can_be_restored_without_claiming_synchronization(self):
        self.ntp = False
        result = self.clock.enable_network_time()
        self.assertTrue(result['ntp_enabled'])
        self.assertFalse(result['synchronized'])

    def test_failed_set_restores_original_ntp_state(self):
        self.failure = subprocess.TimeoutExpired('timedatectl', 10)
        with self.assertRaisesRegex(ClockError, 'timed out'):
            self.clock.set_manual(datetime.fromisoformat('2026-09-19T12:00:00+00:00'))
        self.assertTrue(self.ntp)
        self.assertEqual(self.calls[-1][2:], ['set-ntp', 'true'])
        # Failure while already in manual mode must leave that mode intact.
        self.ntp = False
        self.calls.clear()
        with self.assertRaises(ClockError):
            self.clock.set_manual(datetime.fromisoformat('2026-09-19T12:00:00+00:00'))
        self.assertFalse(self.ntp)
        self.assertNotIn('set-ntp', [call[2] for call in self.calls])

    def test_missing_service_and_denied_permissions_are_explicit(self):
        with patch(
            'intellibat_config.device_clock.subprocess.run',
            side_effect=FileNotFoundError,
        ):
            self.assertFalse(self.clock.snapshot()['available'])
            with self.assertRaisesRegex(ClockError, 'require timedatectl'):
                self.clock.enable_network_time()
        with patch(
            'intellibat_config.device_clock.subprocess.run',
            return_value=subprocess.CompletedProcess([], 1, '', 'Access denied'),
        ):
            with self.assertRaisesRegex(ClockError, 'permission rule'):
                self.clock.enable_network_time()

    def test_requires_timezone_and_serializes_changes(self):
        with self.assertRaisesRegex(ClockError, 'timezone'):
            self.clock.set_manual(datetime(2026, 9, 19))
        self.clock._lock.acquire()
        self.addCleanup(self.clock._lock.release)
        with self.assertRaisesRegex(ClockError, 'in progress'):
            self.clock.enable_network_time()
        self.assertEqual(self.calls, [])

    def test_out_of_range_utc_time_is_rejected_before_disabling_ntp(self):
        with self.assertRaisesRegex(ClockError, 'date range'):
            self.clock.set_manual(datetime.fromisoformat('9999-12-31T23:00:00-07:00'))
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
