from __future__ import annotations

import unittest

from arcade.food_frenzy import (
    FoodFrenzyParams,
    start_food_frenzy,
    tick_food_frenzy,
)


def row_col() -> dict[str, tuple[int, int]]:
    return {
        f"{chr(65 + col)}{row}": (row - 1, col)
        for row in range(1, 13)
        for col in range(12)
    }


def blank_cells() -> dict[str, dict[str, object]]:
    return {
        key: {"value": 0, "color": "#567DBB"}
        for key in row_col()
    }


class FoodFrenzyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.params = FoodFrenzyParams(
            round_seconds=30,
            target_confirm_frames=2,
            blink_seconds=0.25,
            celebration_seconds=1,
            seed=10,
        )
        self.session = start_food_frenzy(
            self.params,
            blank_cells(),
            row_col(),
            "A1",
            0.0,
        )

    def test_round_adds_food_after_all_targets_are_collected(self) -> None:
        self.assertEqual(len(self.session.target_cells), 1)
        target = next(iter(self.session.target_cells))
        first = tick_food_frenzy(
            self.session,
            target,
            0.1,
            observation_frame=1,
        )
        self.assertEqual(first.score, 0)
        cleared = tick_food_frenzy(
            self.session,
            target,
            0.14,
            observation_frame=2,
        )
        self.assertEqual(cleared.score, 1)
        self.assertTrue(cleared.celebrating)
        self.assertEqual(cleared.effect, "flash_all")

        next_round = tick_food_frenzy(
            self.session,
            target,
            1.15,
            observation_frame=3,
        )
        self.assertEqual(next_round.round_number, 2)
        self.assertEqual(len(next_round.target_cells), 2)
        self.assertEqual(next_round.remaining_seconds, 30)

    def test_food_targets_blink_off_and_on(self) -> None:
        target = next(iter(self.session.target_cells))
        off = tick_food_frenzy(self.session, None, 0.25)
        self.assertEqual(off.hardware_updates[-1]["key"], target)
        self.assertEqual(off.hardware_updates[-1]["color"], "#000000")

        on = tick_food_frenzy(self.session, None, 0.50)
        self.assertEqual(on.hardware_updates[-1]["key"], target)
        self.assertEqual(on.hardware_updates[-1]["color"], "#001FFF")

    def test_duplicate_camera_frame_does_not_collect_food(self) -> None:
        target = next(iter(self.session.target_cells))
        tick_food_frenzy(self.session, target, 0.1, observation_frame=7)
        duplicate = tick_food_frenzy(
            self.session,
            target,
            0.2,
            observation_frame=7,
        )
        self.assertEqual(duplicate.score, 0)

    def test_round_timer_expiry_loses(self) -> None:
        result = tick_food_frenzy(
            self.session,
            None,
            30.0,
            observation_frame=1,
        )
        self.assertTrue(result.lost)
        self.assertEqual(result.score, 0)


if __name__ == "__main__":
    unittest.main()
