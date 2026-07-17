from __future__ import annotations

import math
import unittest

from analysis.stewart_exp_kinematics import (
    calibrated_solution,
    endpoint_heave_range,
    experimental_geometry,
    plan_circle,
    reconstruct_pose_from_cranks,
    solve_pose_at_heave,
    solve_crank_branches,
    steps_to_crank_deg,
    top_joint_position,
    unwrap_toward,
)
from stewart_exp_probe import parse_status


class BranchAwareIkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.geometry = experimental_geometry()

    def test_axis_two_is_experimental_cardinal_zero(self) -> None:
        self.assertEqual(
            self.geometry.leg_azimuth_deg, (120.0, 240.0, 0.0)
        )

    def test_both_triangle_branches_are_exposed(self) -> None:
        top = top_joint_position(self.geometry, 0, 0.0, 0.0, 20.0)
        branches = solve_crank_branches(self.geometry, 0, top)
        self.assertEqual(len(branches), 2)
        self.assertNotAlmostEqual(
            branches[0].wrapped_deg, branches[1].wrapped_deg
        )

    def test_unwrap_chooses_nearest_full_turn(self) -> None:
        self.assertAlmostEqual(unwrap_toward(5.0, 362.0), 365.0)
        self.assertAlmostEqual(unwrap_toward(355.0, -2.0), -5.0)

    def test_step_conversion_supports_multiple_turns(self) -> None:
        self.assertAlmostEqual(steps_to_crank_deg(16_000), 450.0)
        self.assertAlmostEqual(steps_to_crank_deg(-16_000), -270.0)

    def test_ten_degrees_has_closure_in_every_direction(self) -> None:
        for direction in range(0, 360, 10):
            radians = math.radians(direction)
            heave_range = endpoint_heave_range(
                10.0 * math.sin(radians),
                10.0 * math.cos(radians),
            )
            self.assertIsNotNone(heave_range, f"{direction=}")

    def test_ten_degree_circle_has_continuous_plan(self) -> None:
        planned = plan_circle(10.0)
        self.assertEqual(len(planned), 480)
        self.assertLessEqual(
            max(pose.max_crank_delta_deg for pose in planned), 12.0
        )
        self.assertGreaterEqual(min(pose.heave_mm for pose in planned), -15.0)
        self.assertLessEqual(max(pose.heave_mm for pose in planned), 30.0)

    def test_plan_starts_from_calibrated_pose_without_branch_jump(self) -> None:
        planned = plan_circle(6.0, initial=calibrated_solution())
        self.assertLess(planned[0].max_crank_delta_deg, 12.0)

    def test_model_level_is_nonunique_across_heave_and_branch(self) -> None:
        high = solve_pose_at_heave(
            self.geometry,
            0.0,
            0.0,
            20.0,
            (90.0, 90.0, 90.0),
            estimate_torque=False,
        )
        low = solve_pose_at_heave(
            self.geometry,
            0.0,
            0.0,
            -5.0,
            (-90.0, -90.0, -90.0),
            estimate_torque=False,
        )
        self.assertIsNotNone(high)
        self.assertIsNotNone(low)
        assert high is not None and low is not None
        self.assertEqual((high.roll_deg, high.pitch_deg), (0.0, 0.0))
        self.assertEqual((low.roll_deg, low.pitch_deg), (0.0, 0.0))
        self.assertNotEqual(high.steps, low.steps)

    def test_forward_reconstruction_recovers_held_pose(self) -> None:
        expected = solve_pose_at_heave(
            self.geometry,
            5.0,
            -3.0,
            10.0,
            (120.0, 120.0, 120.0),
            estimate_torque=False,
        )
        self.assertIsNotNone(expected)
        assert expected is not None
        reconstructed = reconstruct_pose_from_cranks(
            self.geometry,
            expected.crank_deg,
            initial_roll_deg=-4.0,
            initial_pitch_deg=6.0,
            initial_heave_mm=14.0,
        )
        self.assertAlmostEqual(reconstructed.roll_deg, expected.roll_deg, places=6)
        self.assertAlmostEqual(reconstructed.pitch_deg, expected.pitch_deg, places=6)
        self.assertAlmostEqual(reconstructed.heave_mm, expected.heave_mm, places=6)


class ExperimentalProtocolTests(unittest.TestCase):
    def test_parse_status(self) -> None:
        status = parse_status(
            "OK STATUS exp=1 calibrated=1 restored=0 calibrating=0 "
            "armed=1 enabled=1 moving=0 "
            "s0=100 s1=-200 s2=300 t0=100 t1=-200 t2=300 "
            "m0=1 m1=1 m2=1 roll=4.0 pitch=-3.0 heave=-2.5 "
            "vmax=40.0 amax=120.0"
        )
        self.assertTrue(status.calibrated)
        self.assertTrue(status.armed)
        self.assertEqual(status.steps, (100, -200, 300))
        self.assertEqual(status.marked, (True, True, True))
        self.assertAlmostEqual(status.heave_mm, -2.5)
        self.assertAlmostEqual(status.max_speed_deg_s, 40.0)
        self.assertAlmostEqual(status.max_accel_deg_s2, 120.0)
        trimmed_pose = status.as_pose((100, -200, 300))
        self.assertAlmostEqual(trimmed_pose.crank_deg[0], 90.0)
        self.assertAlmostEqual(trimmed_pose.crank_deg[1], 90.0)
        self.assertAlmostEqual(trimmed_pose.crank_deg[2], 90.0)
        self.assertAlmostEqual(trimmed_pose.roll_deg, 0.0)
        self.assertAlmostEqual(trimmed_pose.pitch_deg, 0.0)
        self.assertAlmostEqual(trimmed_pose.heave_mm, 30.0)

    def test_reject_production_status(self) -> None:
        with self.assertRaises(ValueError):
            parse_status("OK calibrated 1 enabled 0")

if __name__ == "__main__":
    unittest.main()
