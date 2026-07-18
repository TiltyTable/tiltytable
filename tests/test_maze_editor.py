from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arcade.maze_editor import MazeValidationError, load_maze, save_maze, validate_maze_cells
from arcade.server import create_app
from arcade.levels import ROOT


SOURCE_MAP = ROOT / "maps" / "arcade-level-4.json"


class MazeEditorValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cells = json.loads(SOURCE_MAP.read_text(encoding="utf-8"))

    def test_shipped_maze_is_valid(self) -> None:
        self.assertEqual(len(validate_maze_cells(self.cells)), 144)

    def test_cycle_and_delayed_trap_are_normalized(self) -> None:
        self.cells["B2"]["dynamic"] = {
            "type": "cycle",
            "intervalSeconds": 0.5,
            "pattern": [
                {"value": 1, "color": "#4dff00"},
                {"value": -1, "color": "#ff0000"},
                {"value": 0, "color": "#f49400"},
            ],
        }
        self.cells["C2"]["dynamic"] = {
            "type": "delayed_trap",
            "armDelaySeconds": 0,
            "warnDurationSeconds": 2,
            "initialIntervalSeconds": 0.8,
            "minIntervalSeconds": 0.1,
            "trapColor": "#ff0000",
            "floorColor": "#f49400",
        }
        clean = validate_maze_cells(self.cells)
        self.assertEqual(len(clean["B2"]["dynamic"]["pattern"]), 3)
        self.assertEqual(clean["B2"]["dynamic"]["pattern"][0]["color"], "#4DFF00")
        self.assertEqual(clean["C2"]["dynamic"]["armDelaySeconds"], 0.0)

    def test_invalid_map_does_not_replace_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "maze.json"
            save_maze(path, self.cells)
            before = path.read_bytes()
            invalid = json.loads(json.dumps(self.cells))
            invalid["A1"]["value"] = 2
            with self.assertRaises(MazeValidationError):
                save_maze(path, invalid)
            self.assertEqual(path.read_bytes(), before)


class MazeEditorRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.map_path = Path(self.temp_dir.name) / "maze.json"
        self.map_path.write_text(SOURCE_MAP.read_text(encoding="utf-8"), encoding="utf-8")
        self.app = create_app(start_ticker=False, editor_map_path=self.map_path)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.app.config["ARCADE_SHUTDOWN"]()
        self.temp_dir.cleanup()

    def test_editor_assets_and_map_are_served(self) -> None:
        for path in ("/editor", "/editor/app.js", "/editor/logic.js", "/editor/styles.css"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()
        payload = self.client.get("/api/editor/maze").get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["cells"]), 144)

    def test_editor_saves_valid_cells_and_rejects_bad_cells(self) -> None:
        cells = load_maze(self.map_path)
        cells["B2"] = {"value": -1, "color": "#123456"}
        response = self.client.post("/api/editor/maze", json={"cells": cells})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(load_maze(self.map_path)["B2"]["value"], -1)

        cells["A1"]["value"] = 2
        response = self.client.post("/api/editor/maze", json={"cells": cells})
        self.assertEqual(response.status_code, 400)
        self.assertIn("A1.value", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
