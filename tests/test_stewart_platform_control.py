from __future__ import annotations

import argparse
import math
import unittest
from types import SimpleNamespace

from analysis.stewart_exp_kinematics import (
    NoSolutionError,
    calibrated_solution,
    experimental_geometry,
    plan_heave_transition,
    solve_pose_at_heave,
)
from stewart_platform_control_common import (
    EV_REL,
    EV_SYN,
    INPUT_EVENT,
    REL_X,
    REL_Y,
    SYN_REPORT,
    TrackballAccumulator,
    StewartPlatformController,
    add_common_arguments,
    decay_velocity,
    integrate_velocity,
)
from stewart_platform_control_position import (
    apply_position_counts,
    build_parser,
    command_or_retain_last_valid,
)
from stewart_platform_control_velocity import apply_velocity_counts


def event(event_type: int, code: int, value: int) -> bytes:
    return INPUT_EVENT.pack(0, 0, event_type, code, value & 0xFFFFFFFF)


class SharedStewartControlTests(unittest.TestCase):
    @staticmethod
    def controller_at_zero() -> StewartPlatformController:
        controller = StewartPlatformController.__new__(StewartPlatformController)
        controller.args = SimpleNamespace(
            startup_heave=0.0,
            heave_step=0.25,
            max_heave_step=0.5,
            platform_step=1.0,
            heave_min=-15.0,
            heave_max=30.0,
        )
        controller.geometry = experimental_geometry()
        controller.step_offsets = (0, 0, 0)
        controller.current = plan_heave_transition(calibrated_solution(), 0.0)[-1]
        controller.armed = True

        class FakeLink:
            def __init__(self) -> None:
                self.targets = []

            def target(self, pose, offsets) -> None:
                self.targets.append((pose, offsets))

            def wait_idle(self):
                return None

        controller.link = FakeLink()
        return controller

    def test_position_x_maps_to_pitch_and_y_maps_to_roll(self) -> None:
        roll, pitch = apply_position_counts(
            0.0,
            0.0,
            10,
            -4,
            degrees_per_count=0.1,
            roll_sign=1.0,
            pitch_sign=1.0,
            max_tilt_deg=10.0,
        )
        self.assertAlmostEqual(roll, -0.4)
        self.assertAlmostEqual(pitch, 1.0)

    def test_default_startup_resumes_held_pose(self) -> None:
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)
        args = parser.parse_args([])
        self.assertFalse(args.zero_on_start)

    def test_position_controller_live_defaults(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.max_tilt, 9.0)
        self.assertEqual(args.degrees_per_count, 0.03)
        self.assertEqual(args.crank_accel, 500.0)
        self.assertEqual(args.crank_speed, 60.0)
        self.assertEqual(args.deadband, 0)
        self.assertEqual(args.rate_hz, 90.0)
        self.assertTrue(args.yes)
        self.assertFalse(build_parser().parse_args(["--no-yes"]).yes)

    def test_unreachable_position_retains_last_valid_target(self) -> None:
        class BoundaryController:
            current = SimpleNamespace(roll_deg=-4.5, pitch_deg=6.25)
            rebased = False

            @staticmethod
            def at_target(_roll: float, _pitch: float) -> bool:
                return False

            @staticmethod
            def command_toward(_roll: float, _pitch: float) -> None:
                raise NoSolutionError("outside continuous workspace")

            @classmethod
            def hold_and_rebase(cls):
                cls.rebased = True
                cls.current = SimpleNamespace(roll_deg=0.25, pitch_deg=1.5)
                return cls.current

        roll, pitch, warning = command_or_retain_last_valid(
            BoundaryController(), -6.0, 7.0
        )
        self.assertEqual((roll, pitch), (0.25, 1.5))
        self.assertTrue(BoundaryController.rebased)
        self.assertIn("outside continuous workspace", warning or "")

    def test_position_target_is_radially_clamped(self) -> None:
        roll, pitch = apply_position_counts(
            0.0,
            0.0,
            100,
            100,
            degrees_per_count=1.0,
            roll_sign=1.0,
            pitch_sign=1.0,
            max_tilt_deg=10.0,
        )
        self.assertAlmostEqual(math.hypot(roll, pitch), 10.0)

    def test_swipe_adds_angular_velocity_on_matching_axes(self) -> None:
        roll_velocity, pitch_velocity = apply_velocity_counts(
            0.0,
            0.0,
            8,
            -3,
            velocity_per_count=0.5,
            roll_sign=1.0,
            pitch_sign=1.0,
            max_velocity_deg_s=30.0,
        )
        self.assertAlmostEqual(roll_velocity, -1.5)
        self.assertAlmostEqual(pitch_velocity, 4.0)

    def test_velocity_decay_is_exponential(self) -> None:
        roll_velocity, pitch_velocity = decay_velocity(10.0, -5.0, 0.5, 0.5)
        self.assertAlmostEqual(roll_velocity, 10.0 / math.e)
        self.assertAlmostEqual(pitch_velocity, -5.0 / math.e)

    def test_outward_velocity_is_removed_at_tilt_limit(self) -> None:
        roll, pitch, roll_velocity, pitch_velocity = integrate_velocity(
            9.9, 0.0, 10.0, 2.0, 0.1, 10.0
        )
        self.assertAlmostEqual(math.hypot(roll, pitch), 10.0)
        radial_velocity = roll_velocity * roll + pitch_velocity * pitch
        self.assertLessEqual(radial_velocity, 1e-8)

    def test_partial_trackball_frame_is_not_published_early(self) -> None:
        accumulator = TrackballAccumulator()
        accumulator.feed_bytes(event(EV_REL, REL_X, 7))
        self.assertEqual(accumulator.pop(), (0, 0))
        accumulator.feed_bytes(
            event(EV_REL, REL_Y, -4) + event(EV_SYN, SYN_REPORT, 0)
        )
        self.assertEqual(accumulator.pop(), (7, -4))

    def test_symmetric_calibration_transition_uses_one_branch(self) -> None:
        final = plan_heave_transition(calibrated_solution(), 0.0)[-1]
        self.assertEqual(final.branch_index, (0, 0, 0))
        self.assertEqual(final.steps[0], final.steps[1])
        self.assertEqual(final.steps[1], final.steps[2])

    def test_exact_zero_requires_canonical_startup_heave(self) -> None:
        controller = self.controller_at_zero()
        controller.current = solve_pose_at_heave(
            experimental_geometry(),
            0.0,
            0.0,
            0.5,
            (172.0, 172.0, 172.0),
            estimate_torque=False,
        )
        assert controller.current is not None
        self.assertFalse(controller.at_target(0.0, 0.0))
        controller.current = plan_heave_transition(calibrated_solution(), 0.0)[-1]
        self.assertTrue(controller.at_target(0.0, 0.0))

    def test_zero_pose_round_trip_returns_to_canonical_heave(self) -> None:
        controller = self.controller_at_zero()
        controller.move_to(6.0, 4.0)
        self.assertFalse(controller.at_target(0.0, 0.0))
        final = controller.move_to(0.0, 0.0)
        self.assertEqual((final.roll_deg, final.pitch_deg), (0.0, 0.0))
        self.assertAlmostEqual(final.heave_mm, 0.0)
        self.assertTrue(controller.at_target(0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
