from __future__ import annotations

import unittest

from arcade.pit_detection import PitDetector


class PitDetectorTests(unittest.TestCase):
    def test_pit_requires_confident_dwell(self) -> None:
        detector = PitDetector()
        self.assertFalse(
            detector.update(
                ball_cell="A1",
                is_pit=True,
                now=0.0,
                tracking_confidence=0.9,
                confirm_seconds=0.5,
            )
        )
        self.assertTrue(
            detector.update(
                ball_cell="A1",
                is_pit=True,
                now=0.5,
                tracking_confidence=0.9,
                confirm_seconds=0.5,
            )
        )

    def test_low_confidence_never_arms_pit(self) -> None:
        detector = PitDetector()
        for now in (0.0, 0.5, 1.0):
            self.assertFalse(
                detector.update(
                    ball_cell="A1",
                    is_pit=True,
                    now=now,
                    tracking_confidence=0.5,
                    confirm_seconds=0.5,
                )
            )

    def test_neutral_floor_clears_pending_pit_immediately(self) -> None:
        detector = PitDetector()
        detector.update(
            ball_cell="A1",
            is_pit=True,
            now=0.0,
            tracking_confidence=0.9,
            confirm_seconds=0.5,
        )
        self.assertFalse(
            detector.update(
                ball_cell="B1",
                is_pit=False,
                now=0.49,
                tracking_confidence=0.9,
                confirm_seconds=0.5,
            )
        )
        self.assertIsNone(detector.cell)


if __name__ == "__main__":
    unittest.main()
