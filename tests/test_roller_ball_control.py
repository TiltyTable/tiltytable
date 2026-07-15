from __future__ import annotations

import math
import unittest

from roller_ball import (
    apply_trackball_counts,
    clamp_tilt_vector,
    parse_status_pose,
    smooth_tilt,
)


class TiltEnvelopeTests(unittest.TestCase):
    def test_inside_vector_is_unchanged(self) -> None:
        self.assertEqual(clamp_tilt_vector(2.0, -3.0, 4.6), (2.0, -3.0))

    def test_corner_is_clamped_radially(self) -> None:
        roll, pitch = clamp_tilt_vector(5.0, 5.0, 4.6)
        self.assertAlmostEqual(math.hypot(roll, pitch), 4.6)
        self.assertAlmostEqual(roll, pitch)

    def test_reversing_from_limit_moves_immediately_inward(self) -> None:
        roll, pitch = apply_trackball_counts(
            0.0,
            -4.6,
            4,
            0,
            scale=0.04,
            roll_sign=1.0,
            pitch_sign=1.0,
            max_tilt=4.6,
        )
        self.assertEqual(roll, 0.0)
        self.assertGreater(pitch, -4.6)


class PositionResponseTests(unittest.TestCase):
    def test_default_direct_response_reaches_target_in_one_update(self) -> None:
        self.assertEqual(
            smooth_tilt(0.0, 0.0, 2.0, -3.0, 1.0, 4.6),
            (2.0, -3.0),
        )

    def test_idle_holds_last_position(self) -> None:
        self.assertEqual(
            smooth_tilt(1.25, -2.5, 1.25, -2.5, 1.0, 4.6),
            (1.25, -2.5),
        )

    def test_restored_status_pose_is_parsed(self) -> None:
        self.assertEqual(
            parse_status_pose(
                "OK calibrated 1 restored 1 roll 1.250 pitch -2.500 heave 20.000"
            ),
            (1.25, -2.5, 20.0),
        )

    def test_pitch_sign_is_applied_to_x(self) -> None:
        _, pitch = apply_trackball_counts(
            0.0,
            0.0,
            2,
            0,
            scale=0.04,
            roll_sign=1.0,
            pitch_sign=-1.0,
            max_tilt=4.6,
        )
        self.assertLess(pitch, 0.0)


if __name__ == "__main__":
    unittest.main()
