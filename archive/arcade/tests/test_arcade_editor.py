from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arcade.hex_fall import HexFallParams, start_hex_fall, tick_hex_fall
from arcade.level_packages import (
    PackageValidationError,
    blank_package,
    compile_package,
    install_package,
    package_from_manifest,
    validate_package,
)
from arcade.levels import MANIFEST_PATH, load_levels, load_map
from arcade.server import create_app
from arcade.survival_lava import survival_score
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


class LevelPackageTests(unittest.TestCase):
    def test_shipped_dynamic_modes_have_no_magenta_finish_or_timer_params(self) -> None:
        catalog = load_levels()
        dynamic = {level.id: level for level in catalog.levels if level.number >= 7}
        self.assertEqual(
            {level.mode for level in dynamic.values()},
            {"survival_lava", "hex_fall", "target_hunt"},
        )
        for level in dynamic.values():
            colors = {str(cell["color"]).upper() for cell in load_map(level).values()}
            self.assertNotIn("#680056", colors)
            self.assertNotIn("#FF00AA", colors)
            self.assertNotIn("survivalSeconds", level.mode_params or {})
            self.assertNotIn("startingSeconds", level.mode_params or {})

    def test_blank_package_is_valid_and_round_trips_compile(self) -> None:
        package = blank_package("target_hunt")
        self.assertEqual(validate_package(package), [])
        entry, cells = compile_package(package, "maps/new-level.json")
        self.assertEqual(entry["mode"], "target_hunt")
        self.assertEqual(len(cells), 144)

    def test_existing_survival_level_exports(self) -> None:
        package = package_from_manifest("level-7")
        self.assertEqual(package["mode"], "survival_lava")
        self.assertEqual(len(package["cells"]), 144)
        self.assertEqual(validate_package(package), [])

    def test_schema_reports_missing_cells_and_mode_params(self) -> None:
        package = blank_package("hex_fall")
        package["cells"].pop("A1")
        package["modeParams"].pop("pointsPerTile")
        errors = validate_package(package, raise_on_error=False)
        self.assertTrue(any("A1" in error for error in errors))
        self.assertTrue(any("pointsPerTile" in error for error in errors))
        with self.assertRaises(PackageValidationError):
            validate_package(package)

    def test_install_package_writes_manifest_and_map(self) -> None:
        package = blank_package("hex_fall")
        package["meta"]["id"] = "hex-test"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "levels.json"
            manifest.write_text('{"gauntletLevelIds":[],"lore":{},"levels":[]}')
            manifest_out, map_out = install_package(
                package, manifest_path=manifest, maps_dir=root / "maps"
            )
            self.assertTrue(manifest_out.exists())
            self.assertEqual(len(json.loads(map_out.read_text())), 144)
            saved = json.loads(manifest.read_text())["levels"][0]
            self.assertEqual(saved["mode"], "hex_fall")

    def test_installed_mode_params_load_in_game_catalog(self) -> None:
        package = package_from_manifest("level-7")
        package["meta"]["id"] = "lava-package-test"
        package["meta"]["number"] = 8
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "levels.json"
            manifest.write_text(MANIFEST_PATH.read_text())
            install_package(package, manifest_path=manifest, maps_dir=root / "maps")
            catalog = load_levels(manifest)
            loaded = next(level for level in catalog.levels if level.id == "lava-package-test")
            self.assertIsNone(loaded.survival_seconds)
            self.assertEqual(loaded.mode_params["dwellSeconds"], 1.5)


class TargetHuntTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cells = blank_package()["cells"]
        self.row_col = row_col()

    def test_reachable_cells_respects_walls(self) -> None:
        for row in range(1, 13):
            self.cells[f"F{row}"]["value"] = 1
        reached = reachable_cells("A1", self.cells, self.row_col)
        self.assertNotIn("L12", reached)

    def test_target_claim_adds_obstacles_and_preserves_route(self) -> None:
        params = TargetHuntParams(
            target_confirm_seconds=0.1, seed=7, spawn_pit_count=1, spawn_wall_count=1
        )
        session = start_target_hunt(params, self.cells, self.row_col, "A1", 0.0)
        target = session.target_cell
        self.assertIsNotNone(target)
        tick_target_hunt(session, target, 0.0)
        result = tick_target_hunt(session, target, 0.2)
        self.assertEqual(result.targets_reached, 1)
        self.assertGreater(len(reachable_cells(target, session.cells, self.row_col)), 1)
        self.assertEqual(
            sum(1 for cell in session.cells.values() if cell["value"] != 0), 2
        )

    def test_snake_loses_only_on_pit(self) -> None:
        params = TargetHuntParams(seed=2)
        session = start_target_hunt(params, self.cells, self.row_col, "A1", 0.0)
        self.assertFalse(tick_target_hunt(session, None, 100.0).lost)
        session.cells["A1"]["value"] = -1
        result = tick_target_hunt(session, "A1", 100.1)
        self.assertTrue(result.lost)

    def test_target_scores_one_point_without_time_bonus(self) -> None:
        params = TargetHuntParams(
            target_confirm_seconds=0.1,
            points_per_target=1,
            seed=4,
        )
        session = start_target_hunt(params, self.cells, self.row_col, "A1", 0.0)
        target = session.target_cell
        tick_target_hunt(session, target, 0.0)
        result = tick_target_hunt(session, target, 0.2)
        self.assertEqual(result.score, 1)

    def test_tiny_island_uses_the_remaining_reachable_food(self) -> None:
        for key in self.cells:
            self.cells[key]["value"] = 1
        self.cells["A1"]["value"] = 0
        self.cells["A2"]["value"] = 0
        session = start_target_hunt(
            TargetHuntParams(minimum_reachable_cells=2),
            self.cells,
            self.row_col,
            "A1",
            0.0,
        )
        self.assertEqual(session.target_cell, "A2")
        self.assertFalse(tick_target_hunt(session, "A1", 0.1).lost)


class HexFallTests(unittest.TestCase):
    def test_each_new_tile_scores_once_and_changes_color(self) -> None:
        mapping = row_col()
        params = HexFallParams(
            collapse_every_seconds=5,
            collapse_count=1,
            points_per_tile=1,
        )
        session = start_hex_fall(params, 0.0)
        tick_hex_fall(session, params, "A1", 0.0, mapping)
        result = tick_hex_fall(session, params, "B1", 1.0, mapping)
        self.assertEqual(result.tiles_touched, 2)
        self.assertEqual(result.score, 2)
        self.assertTrue(any(update["key"] == "B1" for update in result.hardware_updates))
        self.assertFalse(result.ball_cell_heating)

    def test_periodic_collapse_is_seeded(self) -> None:
        mapping = row_col()
        params = HexFallParams(
            collapse_every_seconds=1,
            collapse_count=2,
            seed=11,
        )
        first = start_hex_fall(params, 0.0)
        second = start_hex_fall(params, 0.0)
        tick_hex_fall(first, params, "A1", 0.0, mapping)
        tick_hex_fall(second, params, "A1", 0.0, mapping)
        a = tick_hex_fall(first, params, "A1", 1.1, mapping).hardware_updates
        b = tick_hex_fall(second, params, "A1", 1.1, mapping).hardware_updates
        self.assertEqual([u["key"] for u in a], [u["key"] for u in b])
        self.assertNotIn("A1", [u["key"] for u in a])
        self.assertTrue(all(update["value"] == 0 for update in a))
        sunk = tick_hex_fall(first, params, "A1", 2.2, mapping).hardware_updates
        self.assertTrue(any(update["value"] == -1 for update in sunk))
        active = {
            key
            for key in mapping
            if first.lava.cells.get(key) is None
            or first.lava.cells[key].phase != "sunk"
        }
        from arcade.hex_fall import _connected_from

        self.assertEqual(_connected_from("A1", active, mapping), active)

    def test_score_only_rewards_unique_tiles(self) -> None:
        mapping = row_col()
        params = HexFallParams(
            collapse_every_seconds=5,
            collapse_count=1,
            points_per_tile=1,
            seed=3,
        )
        session = start_hex_fall(params, 0.0)
        first = tick_hex_fall(session, params, "A1", 0.0, mapping)
        self.assertEqual(first.score, 1)
        collected = tick_hex_fall(session, params, "B1", 0.25, mapping)
        self.assertEqual(collected.tiles_touched, 2)
        self.assertEqual(collected.score, 2)
        later = tick_hex_fall(session, params, "A1", 2.1, mapping)
        self.assertEqual(later.score, 2)


class LavaScoreTests(unittest.TestCase):
    def test_score_is_unique_tiles_times_points_less_restarts(self) -> None:
        self.assertEqual(
            survival_score(
                visited_count=12,
                remaining_seconds=999,
                restarts=1,
                points_per_tile=25,
            ),
            200,
        )


class EditorRouteTests(unittest.TestCase):
    def test_editor_assets_are_served(self) -> None:
        app = create_app(start_ticker=False)
        client = app.test_client()
        for path in ("/editor", "/editor/app.js", "/editor/logic.js"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()


if __name__ == "__main__":
    unittest.main()
