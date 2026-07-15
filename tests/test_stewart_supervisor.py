from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from stewart_supervisor import StewartSupervisor
from stewart_supervisor_client import StewartSupervisorClient


class FakeBackend:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.closed = False

    def transact(self, command: str, timeout: float = 1.0) -> str:
        self.commands.append(command)
        if command == "EXP?":
            return "OK EXP UIM5756PM_STEWART_EXP 1"
        if command == "STATUS":
            return "OK STATUS exp=1"
        if command.startswith("ARM "):
            return "OK ARM"
        if command == "ABORT":
            return "OK ABORT HOLDING"
        return "OK TEST"

    def close(self) -> None:
        self.closed = True


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp.name) / "stewart.sock"
        self.backend = FakeBackend()
        self.supervisor = StewartSupervisor(self.backend, self.socket_path)
        self.thread = threading.Thread(target=self.supervisor.serve, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 2.0
        while not self.socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.socket_path.exists())

    def tearDown(self) -> None:
        self.supervisor.running = False
        self.thread.join(timeout=2.0)
        self.temp.cleanup()

    def test_readonly_client_can_query_but_not_arm(self) -> None:
        client = StewartSupervisorClient(self.socket_path, mode="readonly")
        client.open()
        self.assertTrue(client.ping())
        self.assertTrue(client.exchange("EXP?").startswith("OK EXP"))
        with self.assertRaises(RuntimeError):
            client.exchange("ARM CONFIRM")
        client.close()
        self.assertNotIn("ARM CONFIRM", self.backend.commands)

    def test_motion_client_disconnect_aborts_active_motion(self) -> None:
        client = StewartSupervisorClient(self.socket_path, mode="motion")
        client.open()
        self.assertEqual(client.exchange("ARM CONFIRM"), "OK ARM")
        client.close()
        deadline = time.monotonic() + 1.0
        while "ABORT" not in self.backend.commands and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIn("ABORT", self.backend.commands)

    def test_local_rpc_can_sustain_sixty_hz_target_rate(self) -> None:
        client = StewartSupervisorClient(self.socket_path, mode="motion")
        client.open()
        started = time.monotonic()
        for _ in range(120):
            self.assertEqual(client.exchange("STATUS"), "OK STATUS exp=1")
        elapsed = time.monotonic() - started
        client.close()
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
