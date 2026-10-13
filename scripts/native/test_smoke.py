"""Abrupt restart must observe group removal, including Darwin's EPERM race."""
import signal
import unittest
from unittest.mock import Mock, patch

from smoke import Cell


class AbruptShutdown(unittest.TestCase):
    def setUp(self):
        self.process = Mock(pid=12345)
        self.process.poll.return_value = None
        self.cell = Cell.__new__(Cell)
        self.cell.expected_stops = set()
        self.cell.processes = [("main", self.process)]

    def test_permission_probe_must_eventually_observe_missing_group(self):
        with patch("smoke.os.killpg", side_effect=[None, PermissionError(), ProcessLookupError()]) as kill, \
                patch("smoke.time.monotonic", side_effect=[0, 0]), \
                patch("smoke.time.sleep") as sleep:
            self.cell.stop(self.process, abrupt=True)
        self.assertEqual(kill.call_args_list, [
            unittest.mock.call(12345, signal.SIGKILL),
            unittest.mock.call(12345, 0), unittest.mock.call(12345, 0)])
        sleep.assert_called_once_with(0.02)
        self.process.wait.assert_called_once_with(timeout=45)

    def test_permission_probe_does_not_bypass_deadline(self):
        with patch("smoke.os.killpg", side_effect=[None, PermissionError()]), \
                patch("smoke.time.monotonic", side_effect=[0, 11]), \
                self.assertRaisesRegex(RuntimeError, "was not reaped"):
            self.cell.stop(self.process, abrupt=True)

    def test_permission_to_send_kill_is_still_required(self):
        with patch("smoke.os.killpg", side_effect=PermissionError()), \
                self.assertRaises(PermissionError):
            self.cell.stop(self.process, abrupt=True)
        self.process.wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
