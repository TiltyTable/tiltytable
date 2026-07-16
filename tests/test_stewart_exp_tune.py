from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis.stewart_exp_kinematics import calibrated_solution
import stewart_exp_roller_ball
from stewart_exp_probe import ExpStatus
from stewart_exp_tune import (
    MAX_TARGET_JUMP_STEPS,
    TuningResults,
    TuningSession,
    clear_after_fresh_crank_calibration,
    direction_key,
)


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
        if command.startswith("TARGET "):
            parts = command.split()
            steps = tuple(int(value) for value in parts[1:4])
            self._status = ExpStatus(
                **{
                    **self._status.__dict__,
                    "steps": steps,
                    "targets": steps,
                    "roll_deg": float(parts[4]),
                    "pitch_deg": float(parts[5]),
                    "heave_mm": float(parts[6]),
                }
            )
            return "OK TARGET"
        if command == "HOLD":
            return "OK HOLD"
        return "OK TEST"

    def target(self, pose, offsets=(0, 0, 0)) -> None:
        self.targets.append((pose, offsets))

    def wait_following(self, _steps, _max_error):
        raise AssertionError("agile host must not block on every target")

    def wait_idle(self):
        self.wait_idle_calls += 1
        return self._status


class TuningResultsTests(unittest.TestCase):
    def test_direction_keys(self) -> None:
        self.assertEqual(direction_key("roll", "+"), ("roll_positive", 1.0))
        self.assertEqual(direction_key("pitch", "-"), ("pitch_negative", -1.0))

    def test_recommendation_uses_largest_directional_threshold(self) -> None:
        results = TuningResults(
            thresholds={
                "roll_positive": [0.4, 0.5],
                "pitch_negative": [0.7],
            }
        )
        self.assertEqual(results.recompute_recommendation(0.1), 0.8)

    def test_results_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "results.json"
            original = TuningResults(
                thresholds={"roll_positive": [0.5]},
                motor_trim_steps=[10, -20, 30],
                level_anchor_steps=[1010, 1980, 3030],
                level_anchor_model={
                    "roll_deg": 0.4,
                    "pitch_deg": -0.2,
                    "heave_mm": -1.5,
                    "model_steps": [1000, 2000, 3000],
                    "branch_index": [1, 0, 1],
                },
            )
            original.recompute_recommendation()
            original.save(path)
            loaded = TuningResults.load(path)
            self.assertEqual(loaded.thresholds, original.thresholds)
            self.assertEqual(
                loaded.recommended_activation_deg,
                original.recommended_activation_deg,
            )
            self.assertEqual(loaded.level_anchor_steps, original.level_anchor_steps)
            self.assertEqual(loaded.level_anchor_model, original.level_anchor_model)


class TuningSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.link = FakeLink()
        self.session = TuningSession(
            self.link,
            TuningResults(),
            Path(self.temp.name) / "results.json",
        )
        self.session.current = calibrated_solution()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_profile_command_is_runtime_configurable(self) -> None:
        self.session.set_profile(60.0, 200.0)
        self.assertIn("PROFILE 60.000 200.000", self.link.commands)

    def test_small_pose_generates_bounded_targets_and_arms(self) -> None:
        self.session.move_to(0.2, 0.0, increment_deg=0.1)
        self.assertEqual(len(self.link.targets), 2)
        self.assertIn("ARM CONFIRM", self.link.commands)
        self.assertAlmostEqual(self.session.current.roll_deg, 0.2)

    def test_default_move_uses_adaptive_waypoints(self) -> None:
        self.session.prepare_agile_pose()
        self.link.targets.clear()
        self.session.move_to(6.0, 0.0)
        self.assertLessEqual(len(self.link.targets), 12)

    def test_roller_and_tuning_paths_do_not_wait_per_target(self) -> None:
        self.assertNotIn(
            ".wait_following(",
            inspect.getsource(stewart_exp_roller_ball.main),
        )
        self.assertNotIn(
            ".wait_following(",
            inspect.getsource(TuningSession.move_to),
        )

    def test_manual_mark_uses_configured_margin_and_persists(self) -> None:
        self.session.threshold_margin_deg = 0.2
        self.session.record_threshold("pitch", "-", 0.7)
        self.assertEqual(
            self.session.results.thresholds["pitch_negative"], [0.7]
        )
        self.assertAlmostEqual(
            self.session.results.recommended_activation_deg, 0.9
        )
        loaded = TuningResults.load(self.session.results_path)
        self.assertAlmostEqual(loaded.recommended_activation_deg, 0.9)

    def test_select_current_profile(self) -> None:
        self.session.select_current_profile()
        self.assertEqual(
            self.session.results.selected_profile,
            {"speed_deg_s": 40.0, "accel_deg_s2": 120.0},
        )

    def test_marked_level_captures_exact_absolute_anchor(self) -> None:
        self.link._status = ExpStatus(
            **{
                **self.link._status.__dict__,
                "steps": (1234, -5678, 9012),
                "targets": (1234, -5678, 9012),
                "roll_deg": 1.2,
                "pitch_deg": -0.8,
                "heave_mm": -2.5,
            }
        )
        self.session.results.motor_trim_steps = [10, -20, 30]
        self.session.mark_level_trim()
        loaded = TuningResults.load(self.session.results_path)
        self.assertEqual(loaded.level_anchor_steps, [1234, -5678, 9012])
        self.assertEqual(
            loaded.level_anchor_model["model_steps"],
            [1224, -5658, 8982],
        )
        self.assertAlmostEqual(loaded.level_trim_roll_deg, 1.2)
        self.assertAlmostEqual(loaded.level_trim_pitch_deg, -0.8)

    def test_level_returns_exact_anchor_across_nonunique_model_level(self) -> None:
        anchor_steps = (-8340, -5200, -5300)
        trims = (-40, 70, -90)
        self.session.results.motor_trim_steps = list(trims)
        self.session.results.level_anchor_steps = list(anchor_steps)
        self.session.results.level_anchor_model = {
            "roll_deg": 0.0,
            "pitch_deg": 0.0,
            "heave_mm": -3.0,
            "model_steps": [
                anchor_steps[axis] - trims[axis] for axis in range(3)
            ],
            "branch_index": [1, 1, 0],
        }
        start = (6000, -7000, 8000)
        self.link._status = ExpStatus(
            **{
                **self.link._status.__dict__,
                "steps": start,
                "targets": start,
                "roll_deg": 0.0,
                "pitch_deg": 0.0,
                "heave_mm": 20.0,
            }
        )

        self.session.level()

        target_commands = [
            command for command in self.link.commands if command.startswith("TARGET ")
        ]
        commanded_steps = [
            tuple(int(value) for value in command.split()[1:4])
            for command in target_commands
        ]
        previous = start
        for waypoint in commanded_steps:
            self.assertLessEqual(
                max(abs(waypoint[axis] - previous[axis]) for axis in range(3)),
                MAX_TARGET_JUMP_STEPS,
            )
            previous = waypoint
        self.assertEqual(commanded_steps[-1], anchor_steps)
        self.assertNotEqual(
            commanded_steps[-1],
            tuple(anchor_steps[axis] + trims[axis] for axis in range(3)),
        )
        self.assertEqual(self.link.wait_idle_calls, 1)

    def test_motor_adjustment_is_saved_and_applied_to_future_targets(self) -> None:
        self.session.adjust_motor(1, 20)
        self.assertEqual(self.session.results.motor_trim_steps, [0, 20, 0])
        self.session.move_to(0.1, 0.0, increment_deg=0.1)
        _, offsets = self.link.targets[-1]
        self.assertEqual(offsets, (0, 20, 0))
        loaded = TuningResults.load(self.session.results_path)
        self.assertEqual(loaded.motor_trim_steps, [0, 20, 0])

    def test_new_calibration_clears_stale_anchor_and_trims(self) -> None:
        self.session.results.motor_trim_steps = [-880, 700, -700]
        self.session.results.level_anchor_steps = [1, 2, 3]
        self.session.results.level_anchor_model = {
            "roll_deg": 0.0,
            "pitch_deg": 0.0,
            "heave_mm": 0.0,
            "model_steps": [881, -698, 703],
        }
        clear_after_fresh_crank_calibration(
            self.session.results, self.session.results_path
        )
        self.assertEqual(self.session.results.motor_trim_steps, [0, 0, 0])
        self.assertIsNone(self.session.results.level_anchor_steps)
        loaded = TuningResults.load(self.session.results_path)
        self.assertEqual(loaded.motor_trim_steps, [0, 0, 0])
        self.assertIsNone(loaded.level_anchor_steps)

    def test_recalibrate_command_clears_all_motion_calibration(self) -> None:
        self.session.results.level_trim_roll_deg = 1.25
        self.session.results.level_trim_pitch_deg = -0.75
        self.session.results.motor_trim_steps = [10, -20, 30]
        self.session.results.level_anchor_steps = [1, 2, 3]
        self.session.results.level_anchor_model = {
            "roll_deg": 0.0,
            "pitch_deg": 0.0,
            "heave_mm": 0.0,
            "model_steps": [1, 2, 3],
        }
        with patch("stewart_exp_tune.calibrate", return_value=self.link._status) as run:
            self.session.recalibrate_motors()
        run.assert_called_once_with(self.link)
        self.assertEqual(self.session.results.motor_trim_steps, [0, 0, 0])
        self.assertEqual(self.session.results.level_trim_roll_deg, 0.0)
        self.assertEqual(self.session.results.level_trim_pitch_deg, 0.0)
        self.assertIsNone(self.session.results.level_anchor_steps)
        self.assertIsNotNone(self.session.current)
        loaded = TuningResults.load(self.session.results_path)
        self.assertEqual(loaded.motor_trim_steps, [0, 0, 0])


if __name__ == "__main__":
    unittest.main()
