from __future__ import annotations

import json
import io
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from arcade.engine import GameEngine, GameState
from arcade.hardware import (
    HardwareError,
    ModuleGridHardware,
    SimulatedTableHardware,
    load_module_start_delay_ms,
)
from arcade.ball_adapters import InProcessKinectBallAdapter, ManualBallAdapter
from arcade.integrations import BallObservation, TiltStatus
from arcade.levels import load_levels
from arcade.server import create_app, load_game_tick_ms
from arcade.storage import ScoreStore
from game_runner import Table, load_table_configs


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FaultHardware(SimulatedTableHardware):
    def initialize(self) -> None:
        raise RuntimeError("controller missing")


class LoadingHardware(SimulatedTableHardware):
    """Simulates a level load that stays busy until cancelled."""

    def load_level(self, map_path: Path, start_cell: str, end_cell: str) -> None:
        self.level = map_path.name
        self.busy = True
        self.playing = False


class TrackingHardware(SimulatedTableHardware):
    def __init__(self) -> None:
        super().__init__()
        self.updates: list[dict] = []

    def apply_cell_updates(self, updates: list[dict]) -> None:
        self.updates.extend(updates)


class LiveBallAdapter(ManualBallAdapter):
    is_live = True
    label = "Azure Kinect"


class FakeTiltAdapter:
    label = "Stewart + roller ball"

    def __init__(self) -> None:
        self.started = False
        self.active = False
        self.requests: list[bool] = []
        self.confirm_presses = 0
        self.back_presses = 0
        self.navigation_up = 0
        self.navigation_down = 0

    def start(self) -> None:
        self.started = True

    def set_active(self, active: bool) -> None:
        self.active = active
        self.requests.append(active)

    def status(self) -> TiltStatus:
        return TiltStatus(
            enabled=self.started,
            active=self.active,
            confirm_presses=self.confirm_presses,
            back_presses=self.back_presses,
            navigation_up=self.navigation_up,
            navigation_down=self.navigation_down,
        )

    def stop(self) -> None:
        self.started = False
        self.active = False


class ArcadeSurvivalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.hardware = TrackingHardware()
        self.ball = ManualBallAdapter()
        self.store = ScoreStore(Path(self.temp_dir.name) / "scores.json")
        self.catalog = load_levels()
        self.engine = GameEngine(
            self.catalog,
            self.hardware,
            self.store,
            self.clock,
            ball_adapter=self.ball,
        )
        self.engine.setup()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def begin_lava_survival(self) -> None:
        self.engine.show_level_select()
        self.engine.select_practice_level("lava-survival")
        self.engine.continue_action()
        self.engine.tick()
        self.engine.confirm_placement()
        self.ball.set_cell("F6")
        self.assertEqual(self.engine.state, GameState.PLAYING)

    def test_survival_win_after_countdown(self) -> None:
        self.begin_lava_survival()
        self.ball.set_cell(None)
        for _ in range(40):
            self.clock.advance(1.0)
            self.engine.tick()
        self.assertEqual(self.engine.state, GameState.LEVEL_CLEAR)
        result = self.engine.last_level_result
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 0)

    def test_survival_lava_fail_restarts(self) -> None:
        self.begin_lava_survival()
        for _ in range(60):
            self.clock.advance(0.12)
            self.engine.tick()
            if self.engine.state == GameState.SURVIVAL_FAIL:
                break
        self.assertEqual(self.engine.state, GameState.SURVIVAL_FAIL)
        self.engine.continue_action()
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLACEMENT)

    def begin_dynamic_level(self, level_id: str) -> None:
        self.engine.show_level_select()
        self.engine.select_practice_level(level_id)
        self.engine.continue_action()
        self.engine.tick()
        self.ball.set_cell("F6")
        self.engine.confirm_placement()
        self.assertEqual(self.engine.state, GameState.PLAYING)

    def test_hex_scores_touched_tiles_and_keeps_timer(self) -> None:
        self.begin_dynamic_level("hex-a-fall")
        self.clock.advance(0.11)
        self.engine.tick()
        state = self.engine.public_state()
        self.assertTrue(state["timer"]["running"])
        self.assertEqual(state["timer"]["remainingSeconds"], 45)
        self.assertEqual(state["modeState"]["tilesTouched"], 1)
        self.assertEqual(state["score"], 1)
        self.assertNotIn("#680056", {cell["color"] for cell in state["mapCells"]})

    def test_hex_survives_when_timer_expires(self) -> None:
        self.begin_dynamic_level("hex-a-fall")
        self.ball.set_cell(None)
        self.clock.advance(45.0)
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.LEVEL_CLEAR)
        self.assertEqual(self.engine.last_level_result.score, 0)

    def test_snake_scores_food_and_has_no_timer(self) -> None:
        self.begin_dynamic_level("snake")
        self.clock.advance(0.11)
        self.engine.tick()
        target = self.engine.public_state()["modeState"]["targetCell"]
        self.assertIsNotNone(target)
        self.ball.set_cell(target)
        self.clock.advance(0.11)
        self.engine.tick()
        self.clock.advance(0.21)
        self.engine.tick()
        state = self.engine.public_state()
        self.assertFalse(state["timer"]["running"])
        self.assertEqual(state["score"], 1)
        self.assertEqual(sum(cell["value"] == 1 for cell in state["mapCells"]), 1)
        self.assertEqual(sum(cell["value"] == -1 for cell in state["mapCells"]), 1)

    def test_new_lava_cell_is_selected_on_next_tracking_frame(self) -> None:
        self.begin_lava_survival()
        self.engine.tick()
        self.hardware.updates.clear()

        self.ball.set_cell("G6")
        self.clock.advance(0.01)
        self.engine.tick()

        selected = [
            update for update in self.hardware.updates
            if update["key"] == "G6" and update["color"] == "#F49400"
        ]
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0]["leds_only"])

    def test_public_state_includes_ball_when_adapter_present(self) -> None:
        self.ball.set_cell("G7")
        state = self.engine.public_state()
        self.assertIn("ball", state)
        self.assertEqual(state["ball"]["cell"], "G7")
        self.assertEqual(state["ball"]["row"], 6)
        self.assertEqual(state["ball"]["col"], 6)
        self.assertEqual(state["ball"]["confidence"], 1.0)
        self.assertFalse(state["integrations"]["tracking"]["enabled"])

    def test_catalog_contains_only_three_dynamic_modes(self) -> None:
        self.assertEqual(
            [level.id for level in self.catalog.levels],
            ["lava-survival", "hex-a-fall", "snake"],
        )
        self.assertEqual(
            {level.mode for level in self.catalog.levels},
            {"survival_lava", "hex_fall", "target_hunt"},
        )


class IntegratedTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.hardware = TrackingHardware()
        self.ball = LiveBallAdapter()
        self.store = ScoreStore(Path(self.temp_dir.name) / "scores.json")
        self.engine = GameEngine(
            load_levels(),
            self.hardware,
            self.store,
            self.clock,
            ball_adapter=self.ball,
        )
        self.engine.setup()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def begin_lava_placement(self) -> None:
        self.engine.show_level_select()
        self.engine.select_practice_level("lava-survival")
        self.engine.continue_action()
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLACEMENT)

    def test_placement_reports_tracked_ball_on_start(self) -> None:
        self.begin_lava_placement()
        self.ball.set_cell(self.engine.current_level.start_cell)
        state = self.engine.public_state()
        self.assertTrue(state["placementReady"])
        self.assertTrue(state["integrations"]["tracking"]["enabled"])

    def test_public_state_does_not_advance_game_state(self) -> None:
        self.engine.show_level_select()
        self.engine.select_practice_level("lava-survival")
        self.engine.continue_action()
        self.assertEqual(self.engine.state, GameState.LEVEL_LOADING)
        self.engine.public_state()
        self.assertEqual(self.engine.state, GameState.LEVEL_LOADING)
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLACEMENT)

    def test_ball_frame_latency_is_recorded_on_game_ingest(self) -> None:
        class TimedBallAdapter(ManualBallAdapter):
            def observation(self) -> BallObservation:
                return BallObservation(
                    cell="A1",
                    confidence=0.9,
                    pose_fresh=True,
                    frame_seq=23,
                    processing_ms=5.0,
                    capture_to_observation_ms=14.0,
                )

        self.engine.ball_adapter = TimedBallAdapter()
        self.begin_lava_placement()
        self.engine.confirm_placement()
        self.engine.tick()
        latency = self.engine.public_state()["ball"]["latency"]
        self.assertEqual(latency["frameSeq"], 23)
        self.assertEqual(latency["sensorToTrackerMs"], 5.0)
        self.assertEqual(latency["trackerToGameMs"], 9.0)
        self.assertEqual(latency["captureToGameMs"], 14.0)
        self.assertEqual(latency["averageCaptureToGameMs"], 14.0)
        self.assertEqual(latency["p95CaptureToGameMs"], 14.0)

    def test_headless_adapter_publishes_latest_hub_observation(self) -> None:
        class FakeHub:
            def __init__(self) -> None:
                self.started = False
                self.stopped = False
                self.frame_waits = 0

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

            def wait_for_ball_frame(self, last_seq: int, timeout: float) -> int:
                self.frame_waits += 1
                return 17

            @staticmethod
            def get_ball_state() -> dict:
                return {
                    "detected": True,
                    "cell": {"row": 4, "col": 2},
                    "table_tracking": True,
                    "pose_stale": False,
                    "pose_age_s": 0.1,
                    "frame_seq": 17,
                    "processing_ms": 6.5,
                    "capture_to_observation_ms": 11.0,
                }

        hub = FakeHub()
        adapter = InProcessKinectBallAdapter(Path("unused.json"), hub=hub)
        adapter.start()
        self.assertTrue(adapter.wait_for_frame(0.01))
        self.assertEqual(hub.frame_waits, 1)
        observation = adapter.observation()
        self.assertTrue(hub.started)
        self.assertEqual(observation.cell, "C5")
        self.assertEqual(observation.confidence, 0.9)
        self.assertTrue(observation.pose_fresh)
        self.assertEqual(observation.frame_seq, 17)
        self.assertEqual(observation.processing_ms, 6.5)
        self.assertEqual(observation.capture_to_observation_ms, 11.0)
        adapter.stop()
        self.assertTrue(hub.stopped)

    def test_game_tick_interval_loads_from_arcade_config(self) -> None:
        self.assertEqual(load_game_tick_ms(), 10)

    def test_invalid_explicit_game_tick_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            create_app(start_ticker=False, game_tick_ms=0)


class ArcadeEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.hardware = SimulatedTableHardware()
        self.store = ScoreStore(Path(self.temp_dir.name) / "scores.json")
        self.catalog = load_levels()
        self.engine = GameEngine(self.catalog, self.hardware, self.store, self.clock)
        self.engine.setup()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def begin_lava(self) -> None:
        self.engine.show_level_select()
        self.engine.select_practice_level("lava-survival")
        self.engine.continue_action()
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLACEMENT)
        self.engine.confirm_placement()
        self.assertEqual(self.engine.state, GameState.PLAYING)

    def test_setup_opens_level_selector(self) -> None:
        self.assertEqual(self.engine.state, GameState.LEVEL_SELECT)

    def test_abandon_level_select_stays_on_selector(self) -> None:
        self.engine.show_level_select()
        self.engine.abandon()
        self.assertEqual(self.engine.state, GameState.LEVEL_SELECT)

    def test_abandon_during_level_loading_clears_hardware_busy(self) -> None:
        hardware = LoadingHardware()
        engine = GameEngine(self.catalog, hardware, self.store, self.clock)
        engine.setup()
        engine.show_level_select()
        engine.select_practice_level("lava-survival")
        engine.continue_action()
        self.assertEqual(engine.state, GameState.LEVEL_LOADING)
        self.assertTrue(hardware.snapshot()["busy"])
        engine.abandon()
        self.assertEqual(engine.state, GameState.ABANDONED)
        self.assertFalse(hardware.snapshot()["busy"])

    def test_manual_restart_exposes_restarting_state(self) -> None:
        self.begin_lava()
        self.engine.restart()
        self.assertEqual(self.engine.state, GameState.RESTARTING)
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLACEMENT)

    def test_public_state_survives_repeated_refreshes(self) -> None:
        self.begin_lava()
        first = self.engine.public_state()
        second = self.engine.public_state()
        self.assertEqual(first["state"], "playing")
        self.assertEqual(second["state"], "playing")
        self.assertEqual(first["initials"], second["initials"])

    def test_public_state_omits_ball_without_adapter(self) -> None:
        state = self.engine.public_state()
        self.assertNotIn("ball", state)

    def test_setup_failure_enters_hardware_fault(self) -> None:
        engine = GameEngine(load_levels(), FaultHardware(), self.store, self.clock)
        engine.setup()
        self.assertEqual(engine.state, GameState.HARDWARE_FAULT)
        self.assertIn("controller missing", engine.error)


class ScoreStoreTests(unittest.TestCase):
    def test_partial_runs_rank_below_more_completed_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = ScoreStore(Path(temp) / "scores.json")
            store.add(
                {
                    "initials": "ONE",
                    "score": 9999,
                    "levelsCleared": 1,
                    "elapsedMs": 1000,
                }
            )
            store.add(
                {
                    "initials": "TWO",
                    "score": 100,
                    "levelsCleared": 2,
                    "elapsedMs": 99999,
                }
            )
            self.assertEqual([row["initials"] for row in store.all()], ["TWO", "ONE"])
            raw = json.loads((Path(temp) / "scores.json").read_text())
            self.assertEqual(raw["version"], 1)


class ModuleGridHardwareTests(unittest.TestCase):
    def test_led_only_update_skips_servo_path(self) -> None:
        class FakeTable:
            def __init__(self) -> None:
                self.calls: list[tuple[list[dict], bool]] = []

            def apply_cells(self, updates: list[dict], leds_only: bool = False) -> None:
                self.calls.append((updates, leds_only))

        hardware = ModuleGridHardware(dry_run=True)
        table = FakeTable()
        hardware.table = table  # type: ignore[assignment]
        hardware.playing = True
        update = {
            "key": "F6", "row": 5, "col": 5, "value": 0,
            "color": "#F49400", "leds_only": True,
        }

        hardware.apply_cell_updates([update])

        self.assertEqual(table.calls, [([update], True)])

    def test_module_start_delay_is_loaded_and_applied_between_boards(self) -> None:
        delay_s = load_module_start_delay_ms() / 1000.0
        self.assertGreater(delay_s, 0.0)

        class FakeLink:
            dry_run = False

            def __init__(self) -> None:
                self.boards: list[str] = []

            def send(self, _command: str) -> None:
                return

            def select_board(self, address: str) -> None:
                self.boards.append(address)

        link = FakeLink()
        table = Table(
            link,
            {"cells": {}, "strips": {}},
            {
                "cells": {
                    "0,0": {"address": "0x40", "channel": 0},
                    "0,1": {"address": "0x41", "channel": 0},
                }
            },
            {
                "0x40": {"servos": {"0": {"neutral": 1500}}},
                "0x41": {"servos": {"0": {"neutral": 1500}}},
            },
            module_start_delay_s=delay_s,
        )
        cells = [
            {"key": "A1", "row": 0, "col": 0, "value": 0, "color": "#000000"},
            {"key": "B1", "row": 0, "col": 1, "value": 0, "color": "#000000"},
        ]
        with redirect_stdout(io.StringIO()), patch("game_runner.time.sleep") as sleep:
            table.apply_cells(cells)

        self.assertEqual(link.boards, ["0x40", "0x41"])
        sleep.assert_any_call(delay_s)

    def test_current_calibration_has_complete_144_cell_coverage(self) -> None:
        led, servo_grid, servo_configs = load_table_configs()
        ModuleGridHardware._validate_calibration(led, servo_grid, servo_configs)

    def test_incomplete_calibration_is_rejected_before_serial_open(self) -> None:
        led, servo_grid, servo_configs = load_table_configs()
        led = {**led, "cells": dict(led["cells"])}
        led["cells"].pop("0,0")
        with self.assertRaises(HardwareError):
            ModuleGridHardware._validate_calibration(led, servo_grid, servo_configs)

    def test_existing_module_adapter_runs_full_level_in_dry_run(self) -> None:
        hardware = ModuleGridHardware(dry_run=True)
        level = load_levels().levels[2]
        with redirect_stdout(io.StringIO()):
            hardware.initialize()
            hardware.load_level(level.map_path, level.start_cell, level.end_cell)
            deadline = time.monotonic() + 2
            while hardware.snapshot()["busy"] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(hardware.snapshot()["busy"])
            self.assertEqual(hardware.snapshot()["error"], "")
            hardware.begin_play()
            self.assertTrue(hardware.snapshot()["playing"])
            hardware.pause()
            hardware.shutdown()

    def test_cancel_load_clears_busy_during_in_flight_load(self) -> None:
        hardware = ModuleGridHardware(dry_run=True)
        level = load_levels().levels[2]
        with redirect_stdout(io.StringIO()):
            hardware.initialize()
            hardware.load_level(level.map_path, level.start_cell, level.end_cell)
            self.assertTrue(hardware.snapshot()["busy"])
            hardware.cancel_load()
            self.assertFalse(hardware.snapshot()["busy"])
            deadline = time.monotonic() + 2
            while hardware.snapshot()["busy"] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(hardware.snapshot()["busy"])
            hardware.shutdown()


class ArcadeApiTests(unittest.TestCase):
    def test_ball_endpoint_uses_live_snapshot_without_full_game_state(self) -> None:
        ball = ManualBallAdapter()
        ball.set_cell("G6")
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(
                hardware=SimulatedTableHardware(),
                score_path=Path(temp) / "scores.json",
                start_ticker=False,
                ball_adapter=ball,
            )
            engine = app.config["GAME_ENGINE"]
            with patch.object(
                engine,
                "public_state",
                side_effect=AssertionError("full state should not be read"),
            ):
                response = app.test_client().get("/api/ball")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["ball"]["cell"], "G6")
            self.assertEqual(payload["ball"]["row"], 5)
            self.assertEqual(payload["ball"]["col"], 6)

    def test_tilt_lifecycle_tracks_placement_and_play(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tilt = FakeTiltAdapter()
            app = create_app(
                hardware=SimulatedTableHardware(),
                score_path=Path(temp) / "scores.json",
                start_ticker=False,
                tilt_adapter=tilt,
            )
            engine = app.config["GAME_ENGINE"]
            engine.setup()
            engine.show_level_select()
            engine.select_practice_level("lava-survival")
            engine.continue_action()
            engine.tick()
            self.assertTrue(engine.tilt_requested())
            client = app.test_client()
            tilt.confirm_presses = 2
            tilt.back_presses = 1
            tilt.navigation_up = 3
            tilt.navigation_down = 4
            state = client.get("/api/state").get_json()["game"]
            self.assertEqual(state["integrations"]["tilt"]["confirmPresses"], 2)
            self.assertEqual(state["integrations"]["tilt"]["backPresses"], 1)
            self.assertEqual(state["integrations"]["tilt"]["navigationUp"], 3)
            self.assertEqual(state["integrations"]["tilt"]["navigationDown"], 4)
            client.post("/api/action", json={"action": "confirm-placement"})
            self.assertTrue(tilt.active)
            client.post("/api/action", json={"action": "abandon"})
            self.assertFalse(tilt.active)
            app.config["ARCADE_SHUTDOWN"]()
            self.assertFalse(tilt.started)

    def test_setup_opens_game_selector(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(
                hardware=SimulatedTableHardware(),
                score_path=Path(temp) / "scores.json",
                start_ticker=False,
            )
            client = app.test_client()
            response = client.post("/api/action", json={"action": "setup"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["game"]["state"], "level_select")
            response = client.post(
                "/api/action",
                json={"action": "select-level", "levelId": "hex-a-fall"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["game"]["level"]["id"], "hex-a-fall")
            self.assertEqual(response.get_json()["game"]["state"], "rules")

    def test_campaign_actions_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(
                hardware=SimulatedTableHardware(),
                score_path=Path(temp) / "scores.json",
                start_ticker=False,
            )
            client = app.test_client()
            response = client.post(
                "/api/action", json={"action": "start-gauntlet"}
            )
            self.assertEqual(response.status_code, 400)

    def test_leaderboard_export_is_downloadable_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(
                hardware=SimulatedTableHardware(),
                score_path=Path(temp) / "scores.json",
                start_ticker=False,
            )
            client = app.test_client()
            response = client.get("/api/leaderboard/export")
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertEqual(response.get_json()["version"], 1)


if __name__ == "__main__":
    unittest.main()
