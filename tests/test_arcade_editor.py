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
from arcade.server import create_app
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
        package["modeParams"].pop("survivalSeconds")
        errors = validate_package(package, raise_on_error=False)
        self.assertTrue(any("A1" in error for error in errors))
        self.assertTrue(any("survivalSeconds" in error for error in errors))
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

    def test_target_timer_loss(self) -> None:
        params = TargetHuntParams(starting_seconds=1, seed=2)
        session = start_target_hunt(params, self.cells, self.row_col, "A1", 0.0)
        result = tick_target_hunt(session, None, 1.1)
        self.assertTrue(result.lost)


class HexFallTests(unittest.TestCase):
    def test_touch_does_not_sink_trail(self) -> None:
        mapping = row_col()
        params = HexFallParams(
            survival_seconds=20,
            collapse_every_seconds=5,
            collapse_count=1,
        )
        session = start_hex_fall(params, 0.0)
        tick_hex_fall(session, params, "A1", 0.0, mapping)
        result = tick_hex_fall(session, params, "B1", 1.0, mapping)
        self.assertEqual(result.hardware_updates, [])
        self.assertFalse(result.ball_cell_heating)

    def test_periodic_collapse_is_seeded(self) -> None:
        mapping = row_col()
        params = HexFallParams(
            survival_seconds=20,
            collapse_every_seconds=1,
            collapse_count=2,
            seed=11,
        )
        first = start_hex_fall(params, 0.0)
        second = start_hex_fall(params, 0.0)
        a = tick_hex_fall(first, params, "A1", 1.1, mapping).hardware_updates
        b = tick_hex_fall(second, params, "A1", 1.1, mapping).hardware_updates
        self.assertEqual([u["key"] for u in a], [u["key"] for u in b])
        self.assertNotIn("A1", [u["key"] for u in a])


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
