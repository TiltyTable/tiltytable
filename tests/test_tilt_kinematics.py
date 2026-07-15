from __future__ import annotations

import math
import unittest

from analysis.tilt_kinematics import Geometry, max_tilt, pose_feasible


class AsBuiltGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.geometry = Geometry()

    def test_defaults_match_firmware_geometry(self) -> None:
        self.assertEqual(self.geometry.platform_rod_radius_mm, 119.0)
        self.assertEqual(self.geometry.base_motor_radius_mm, 119.0)
        self.assertEqual(self.geometry.crank_radius_mm, 30.0)
        self.assertEqual(self.geometry.arm_length_mm, 110.0)

    def test_zero_heave_level_pose_exceeds_rod_end_limit(self) -> None:
        ok, misalignment, reason = pose_feasible(self.geometry, 0.0, 0.0, 0.0)
        self.assertFalse(ok)
        self.assertIsNotNone(misalignment)
        self.assertGreater(misalignment, self.geometry.rod_end_limit_deg)
        self.assertEqual(reason, "rod-end angle exceeded")

    def test_operating_heave_is_level_and_feasible(self) -> None:
        ok, misalignment, reason = pose_feasible(
            self.geometry, 0.0, 0.0, 20.0
        )
        self.assertTrue(ok)
        self.assertLess(misalignment, self.geometry.rod_end_limit_deg)
        self.assertEqual(reason, "ok")

    def test_4_6_degree_circle_is_feasible_in_all_directions(self) -> None:
        for direction in range(0, 360, 10):
            radians = math.radians(direction)
            roll = 4.6 * math.sin(radians)
            pitch = 4.6 * math.cos(radians)
            ok, _, reason = pose_feasible(
                self.geometry, roll, pitch, heave_mm=20.0
            )
            self.assertTrue(ok, f"{direction=} {roll=} {pitch=} {reason=}")

    def test_operating_envelope_is_about_4_8_degrees(self) -> None:
        guaranteed, best, _ = max_tilt(
            self.geometry, heave_mm=20.0, step=0.1
        )
        self.assertGreaterEqual(guaranteed, 4.7)
        self.assertLessEqual(guaranteed, 4.9)
        self.assertGreater(best, guaranteed)


if __name__ == "__main__":
    unittest.main()
