from __future__ import annotations

import unittest

from arcade.hex_fall import (
    HexFallParams,
    HexFallStage,
    collapse_stage,
    start_hex_fall,
    tick_hex_fall,
)
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
    def test_catalog_contains_the_five_arcade_modes(self) -> None:
        catalog = load_levels()
        self.assertEqual(
            [(level.id, level.mode) for level in catalog.levels],
            [
                ("lava-survival", "survival_lava"),
                ("hex-a-fall", "hex_fall"),
                ("snake", "target_hunt"),
                ("food-frenzy", "food_frenzy"),
                ("maze", "maze"),
            ],
        )
        for level in catalog.levels:
            self.assertEqual(level.start_cell, "A1")
            colors = {str(cell["color"]).upper() for cell in load_map(level).values()}
            if level.mode != "maze":
                self.assertNotIn("#680056", colors)
                self.assertNotIn("#FF00AA", colors)
        by_id = {level.id: level for level in catalog.levels}
        self.assertFalse(by_id["hex-a-fall"].selectable)
        self.assertTrue(all(
            level.selectable
            for level in catalog.levels
            if level.id != "hex-a-fall"
        ))

        food = by_id["food-frenzy"]
        food_cells = load_map(food)
        self.assertEqual(
            {
                str(cell["color"]).upper()
                for key, cell in food_cells.items()
                if key != food.start_cell
            },
            {"#F49400"},
        )

    def test_maze_map_supports_edited_dynamic_gates_and_finish(self) -> None:
        maze = next(level for level in load_levels().levels if level.mode == "maze")
        cells = load_map(maze)
        self.assertTrue(maze.has_finish)
        self.assertFalse(maze.is_timed)
        self.assertEqual(maze.countdown_seconds, 0)
        self.assertNotIn("timeLimitSeconds", maze.public_dict())
        self.assertEqual(cells["A1"]["color"], "#00FFFF")
        self.assertEqual(cells["L12"]["color"], "#680056")
        dynamic = [cell["dynamic"] for cell in cells.values() if cell.get("dynamic")]
        self.assertTrue(dynamic)
        self.assertTrue(all(item.get("type", "cycle") in ("cycle", "delayed_trap") for item in dynamic))


class HexFallTests(unittest.TestCase):
    def test_unique_floor_touches_score_once(self) -> None:
        mapping = row_col()
        params = HexFallParams(
            survival_seconds=45,
            collapse_stages=(HexFallStage(0, 5, 1),),
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
            collapse_stages=(HexFallStage(0, 1, 1),),
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
            collapse_stages=(HexFallStage(0, 10, 1),),
            points_per_tile=1,
        )
        session = start_hex_fall(params, 0.0)
        tick_hex_fall(session, params, "A1", 0.0, mapping)
        result = tick_hex_fall(session, params, "B1", 2.0, mapping)
        self.assertTrue(result.survived)
        self.assertEqual(result.remaining_seconds, 0)
        self.assertEqual(result.score, 2)

    def test_collapse_stages_escalate_from_one_to_three_tiles(self) -> None:
        params = HexFallParams(
            collapse_stages=(
                HexFallStage(0, 2.0, 1),
                HexFallStage(15, 1.6, 2),
                HexFallStage(30, 1.2, 3),
            )
        )
        self.assertEqual(collapse_stage(params, 0).count, 1)
        self.assertEqual(collapse_stage(params, 15).count, 2)
        self.assertEqual(collapse_stage(params, 30).count, 3)


class SnakeTests(unittest.TestCase):
    def test_food_scores_and_adds_one_wall_and_one_pit(self) -> None:
        cells = blank_cells()
        mapping = row_col()
        params = TargetHuntParams(target_confirm_frames=2, seed=7)
        session = start_target_hunt(params, cells, mapping, "A1", 0.0)
        target = session.target_cell
        self.assertIsNotNone(target)
        first = tick_target_hunt(session, target, 0.0, observation_frame=10)
        self.assertEqual(first.score, 0)
        duplicate = tick_target_hunt(session, target, 0.01, observation_frame=10)
        self.assertEqual(duplicate.score, 0)
        result = tick_target_hunt(session, target, 0.04, observation_frame=11)
        self.assertEqual(result.score, 100)
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
        self.assertFalse(
            tick_target_hunt(
                session,
                "A1",
                100.1,
                tracking_confidence=0.9,
            ).lost
        )
        self.assertFalse(
            tick_target_hunt(
                session,
                "A1",
                102.09,
                tracking_confidence=0.9,
            ).lost
        )
        self.assertTrue(
            tick_target_hunt(
                session,
                "A1",
                102.11,
                tracking_confidence=0.9,
            ).lost
        )

    def test_snake_ignores_low_confidence_pit_overlap(self) -> None:
        cells = blank_cells()
        cells["A1"]["value"] = -1
        session = start_target_hunt(
            TargetHuntParams(seed=3),
            cells,
            row_col(),
            "B1",
            0.0,
        )
        for now in (0.0, 0.3, 0.6, 0.9):
            self.assertFalse(
                tick_target_hunt(
                    session,
                    "A1",
                    now,
                    tracking_confidence=0.5,
                ).lost
            )


if __name__ == "__main__":
    unittest.main()
