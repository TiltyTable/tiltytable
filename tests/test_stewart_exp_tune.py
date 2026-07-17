from __future__ import annotations

import unittest
from unittest.mock import patch

from analysis.stewart_exp_kinematics import calibrated_solution
from stewart_exp_probe import ExpStatus
from stewart_exp_tune import TuningSession, direction_key


class FakeLink:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.targets = []
        self.wait_idle_calls = 0
        self._status = ExpStatus(
            calibrated=True,
            restored=False,
            calibrating=False,
            armed=False,
            enabled=False,
            moving=False,
            steps=(0, 0, 0),
            targets=(0, 0, 0),
            marked=(True, True, True),
            roll_deg=0.0,
            pitch_deg=0.0,
            heave_mm=30.0,
            max_speed_deg_s=40.0,
            max_accel_deg_s2=120.0,
        )

    def status(self):
        return self._status

    def require_ok(self, command: str, prefix: str = "OK") -> str:
        self.commands.append(command)
        if command.startswith("ARM "):
            self._status = ExpStatus(
                **{**self._status.__dict__, "armed": True, "enabled": True}
            )
            return "OK ARM"
        if command.startswith("PROFILE "):
            return "OK PROFILE speed=60.000 accel=200.000"
        if command == "HOLD":
            return "OK HOLD"
        return "OK TEST"

    def target(self, pose, offsets=(0, 0, 0)) -> None:
        self.targets.append((pose, offsets))

    def wait_idle(self):
        self.wait_idle_calls += 1
        return self._status


class TuningSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.link = FakeLink()
        self.session = TuningSession(self.link)
        self.session.current = calibrated_solution()

    def test_direction_keys(self) -> None:
        self.assertEqual(direction_key("roll", "+"), ("roll_positive", 1.0))
        self.assertEqual(direction_key("pitch", "-"), ("pitch_negative", -1.0))

    def test_profile_command_is_runtime_configurable(self) -> None:
        self.session.set_profile(60.0, 200.0)
        self.assertIn("PROFILE 60.000 200.000", self.link.commands)

    def test_small_pose_generates_zero_offset_targets_and_arms(self) -> None:
        self.session.move_to(0.2, 0.0, increment_deg=0.1)
        self.assertEqual(len(self.link.targets), 2)
        self.assertIn("ARM CONFIRM", self.link.commands)
        self.assertTrue(all(offsets == (0, 0, 0) for _, offsets in self.link.targets))
        self.assertAlmostEqual(self.session.current.roll_deg, 0.2)

    def test_default_move_uses_adaptive_waypoints(self) -> None:
        self.session.prepare_agile_pose()
        self.link.targets.clear()
        self.session.move_to(6.0, 0.0)
        self.assertLessEqual(len(self.link.targets), 12)

    def test_level_returns_to_model_zero(self) -> None:
        with patch.object(self.session, "move_to", return_value=1.0) as move:
            self.assertEqual(self.session.level(), 1.0)
        move.assert_called_once_with(0.0, 0.0)

    def test_recalibrate_uses_firmware_calibration_only(self) -> None:
        with patch("stewart_exp_tune.calibrate", return_value=self.link._status) as run:
            self.session.recalibrate_motors()
        run.assert_called_once_with(self.link)
        self.assertIsNotNone(self.session.current)


if __name__ == "__main__":
    unittest.main()
