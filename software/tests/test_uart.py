"""The optional debug UART must sleep when idle or disconnected."""

import ast
import logging
import threading
import time
import unittest
from pathlib import Path
from queue import Empty, Queue
from types import SimpleNamespace
from unittest.mock import patch


class DebugPort:
    def __init__(self, *args, timeout=0.5):
        self.timeout = timeout
        self.messages = Queue()
        self.reads = 0
        self.closed = False
        self.disconnected = False

    @property
    def in_waiting(self):
        raise AssertionError('Debug worker must not busy-poll the serial port')

    def read_until(self, expected, size):
        self.reads += 1
        if self.disconnected:
            raise OSError('Debug adapter disconnected')
        try:
            return self.messages.get(timeout=self.timeout)
        except Empty:
            return b''

    def close(self):
        self.closed = True


class UARTTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).parents[1] / 'service.py').read_text())
        module = ast.Module(
            body=[
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == 'UART'
            ],
            type_ignores=[],
        )
        self.runtime = {}
        namespace = {
            'Thread': threading.Thread,
            'time': time,
            'logging': logging,
            'atexit': SimpleNamespace(register=lambda callback: None),
            'serial': SimpleNamespace(Serial=DebugPort, SerialException=OSError),
            'RUNTIME_STATUS': self.runtime,
        }
        exec(compile(module, 'service.py', 'exec'), namespace)
        self.worker = namespace['UART']()
        self.addCleanup(self.worker.shutdown)
        self.port = self.worker.uart1

    def test_idle_worker_waits_and_stops_cleanly(self):
        self.worker.start()
        time.sleep(0.8)
        self.assertTrue(self.worker.is_alive())
        self.assertLessEqual(self.port.reads, 2)
        self.worker.shutdown()
        self.assertFalse(self.worker.is_alive())
        self.assertTrue(self.port.closed)

    def test_message_after_decode_failure_still_reaches_telemetry(self):
        self.port.messages.put(b'\xff\n')
        self.port.messages.put(b'streaming=1\r\n')
        self.worker.start()
        deadline = time.monotonic() + 2
        while 'uart_message' not in self.runtime and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.runtime.get('uart_message'), 'streaming=1')
        self.assertTrue(self.worker.is_alive())

    def test_disconnected_port_backs_off_instead_of_spinning(self):
        self.port.disconnected = True
        with patch('logging.exception') as logger:
            self.worker.start()
            time.sleep(0.8)
            self.assertTrue(self.worker.is_alive())
            self.assertLessEqual(self.port.reads, 2)
            logger.assert_called()
            self.worker.shutdown()
        self.assertFalse(self.worker.is_alive())


if __name__ == '__main__':
    unittest.main()
