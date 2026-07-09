from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hardware.control_center.server import create_app


class ControlCenterApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base_path = Path(self.temp_dir.name)
        self.config_path = base_path / "servo_calibration.json"
        self.state_path = base_path / "runtime_state.json"
        self.app = create_app(config_path=self.config_path, runtime_state_path=self.state_path)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dashboard_loads_without_connected_hardware(self) -> None:
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 200)

        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIn("dashboard", payload)
        self.assertIn("host", payload["dashboard"])
        self.assertFalse(payload["dashboard"]["servo"]["connected"])
        self.assertEqual(payload["dashboard"]["servo"]["servo_count"], 4)
        self.assertIn("camera", payload["dashboard"])
        self.assertIn("device_source", payload["dashboard"]["camera"])

    def test_profile_update_persists_to_config_file(self) -> None:
        response = self.client.post(
            "/api/servo/profiles/0",
            json={
                "name": "tilt-left",
                "min_us": 610,
                "max_us": 2280,
                "home_deg": 83.5,
                "invert": True,
                "states_us": {
                    "wall": 2090,
                    "floor": 1510,
                    "hole": 920,
                },
            },
        )
        self.assertEqual(response.status_code, 200)

        payload = response.get_json()
        self.assertTrue(payload["ok"])
        channel_zero = payload["dashboard"]["servo"]["profiles"][0]
        self.assertEqual(channel_zero["name"], "tilt-left")
        self.assertEqual(channel_zero["states_us"]["hole"], 920)
        self.assertTrue(channel_zero["invert"])

        stored = json.loads(self.config_path.read_text(encoding="utf-8"))
        first_profile = stored["channels"][0]
        self.assertEqual(first_profile["name"], "tilt-left")
        self.assertEqual(first_profile["min_us"], 610)
        self.assertEqual(first_profile["max_us"], 2280)
        self.assertEqual(first_profile["home_deg"], 83.5)
        self.assertTrue(first_profile["invert"])
        self.assertEqual(first_profile["states_us"]["wall"], 2090)


if __name__ == "__main__":
    unittest.main()
