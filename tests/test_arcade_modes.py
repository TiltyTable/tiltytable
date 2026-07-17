from __future__ import annotations

import unittest

from arcade.hex_fall import HexFallParams, start_hex_fall, tick_hex_fall
from arcade.levels import load_levels, load_map
from arcade.target_hunt import (
    TargetHuntParams,
    reachable_cells,
    start_target_hunt,
    tick_target_hunt,
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


class CatalogTests(unittest.TestCase):
    def test_catalog_contains_only_the_three_arcade_modes(self) -> None:
        catalog = load_levels()
        self.assertEqual(
            [(level.id, level.mode) for level in catalog.levels],
            [
                ("lava-survival", "survival_lava"),
                ("hex-a-fall", "hex_fall"),
                ("snake", "target_hunt"),
            ],
        )
        for level in catalog.levels:
            colors = {str(cell["color"]).upper() for cell in load_map(level).values()}
            self.assertNotIn("#680056", colors)
            self.assertNotIn("#FF00AA", colors)


class HexFallTests(unittest.TestCase):
    def test_unique_floor_touches_score_once(self) -> None:
        mapping = row_col()
        params = HexFallParams(
            survival_seconds=45,
            collapse_every_seconds=5,
            points_per_tile=1,
        )
        session = start_hex_fall(params, 0.0)
        self.assertEqual(
            tick_hex_fall(session, params, "A1", 0.0, mapping).score,
            1,
        )
        self.assertEqual(
            tick_hex_fall(session, params, "B1", 0.2, mapping).score,
            2,
        )
        result = tick_hex_fall(session, params, "A1", 1.0, mapping)
        self.assertEqual(result.score, 2)
        self.assertEqual(result.tiles_touched, 2)

    def test_random_collapse_warns_led_only_then_sinks(self) -> None:
        mapping = row_col()
        params = HexFallParams(
            survival_seconds=45,
            collapse_every_seconds=1,
            collapse_warn_seconds=1,
            seed=11,
        )
        session = start_hex_fall(params, 0.0)
        tick_hex_fall(session, params, "A1", 0.0, mapping)
        warning = tick_hex_fall(session, params, "A1", 1.1, mapping)
        warned = [update for update in warning.hardware_updates if update["key"] != "A1"]
        self.assertTrue(warned)
        self.assertTrue(all(update.get("leds_only") for update in warned))
        sunk = tick_hex_fall(session, params, "A1", 2.2, mapping)
        self.assertTrue(any(update["value"] == -1 for update in sunk.hardware_updates))

    def test_timer_win_preserves_touch_score(self) -> None:
        mapping = row_col()
        params = HexFallParams(
            survival_seconds=2,
            collapse_every_seconds=10,
            points_per_tile=1,
        )
        session = start_hex_fall(params, 0.0)
        tick_hex_fall(session, params, "A1", 0.0, mapping)
        result = tick_hex_fall(session, params, "B1", 2.0, mapping)
        self.assertTrue(result.survived)
        self.assertEqual(result.remaining_seconds, 0)
        self.assertEqual(result.score, 2)


class SnakeTests(unittest.TestCase):
    def test_food_scores_and_adds_one_wall_and_one_pit(self) -> None:
        cells = blank_cells()
        mapping = row_col()
        params = TargetHuntParams(target_confirm_seconds=0.1, seed=7)
        session = start_target_hunt(params, cells, mapping, "A1", 0.0)
        target = session.target_cell
        self.assertIsNotNone(target)
        tick_target_hunt(session, target, 0.0)
        result = tick_target_hunt(session, target, 0.2)
        self.assertEqual(result.score, 1)
        self.assertEqual(result.targets_reached, 1)
        self.assertEqual(
            sum(int(cell["value"]) == 1 for cell in session.cells.values()),
            1,
        )
        self.assertEqual(
            sum(int(cell["value"]) == -1 for cell in session.cells.values()),
            1,
        )
        self.assertGreater(len(reachable_cells(target, session.cells, mapping)), 1)

    def test_snake_loses_only_after_entering_a_pit(self) -> None:
        cells = blank_cells()
        mapping = row_col()
        session = start_target_hunt(
            TargetHuntParams(seed=2),
            cells,
            mapping,
            "A1",
            0.0,
        )
        self.assertFalse(tick_target_hunt(session, None, 100.0).lost)
        session.cells["A1"]["value"] = -1
        self.assertTrue(tick_target_hunt(session, "A1", 100.1).lost)


if __name__ == "__main__":
    unittest.main()
