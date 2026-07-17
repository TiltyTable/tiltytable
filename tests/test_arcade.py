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

    def begin_level_seven(self) -> None:
        self.engine.show_level_select()
        self.engine.select_practice_level("level-7")
        self.engine.continue_action()
        self.engine.tick()
        self.engine.confirm_placement()
        self.ball.set_cell("F6")
        self.assertEqual(self.engine.state, GameState.PLAYING)

    def test_survival_win_after_countdown(self) -> None:
        self.begin_level_seven()
        self.ball.set_cell(None)
        for _ in range(40):
            self.clock.advance(1.0)
            self.engine.tick()
        self.assertEqual(self.engine.state, GameState.LEVEL_CLEAR)
        result = self.engine.last_level_result
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 0)

    def test_survival_lava_fail_restarts(self) -> None:
        self.begin_level_seven()
        for _ in range(60):
            self.clock.advance(0.12)
            self.engine.tick()
            if self.engine.state == GameState.SURVIVAL_FAIL:
                break
        self.assertEqual(self.engine.state, GameState.SURVIVAL_FAIL)
        self.engine.continue_action()
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLACEMENT)

    def test_public_state_includes_ball_when_adapter_present(self) -> None:
        self.ball.set_cell("G7")
        state = self.engine.public_state()
        self.assertIn("ball", state)
        self.assertEqual(state["ball"]["cell"], "G7")
        self.assertEqual(state["ball"]["row"], 6)
        self.assertEqual(state["ball"]["col"], 6)
        self.assertEqual(state["ball"]["confidence"], 1.0)
        self.assertFalse(state["integrations"]["tracking"]["enabled"])

    def test_gauntlet_ball_tracking_does_not_emit_survival_red(self) -> None:
        self.engine.start_gauntlet()
        self.engine.set_initials("AAA")
        self.engine.continue_action()
        self.engine.tick()
        self.engine.confirm_placement()
        self.ball.set_cell("F6")
        for _ in range(40):
            self.clock.advance(0.15)
            self.engine.tick()
        red_updates = [
            u for u in self.hardware.updates if u.get("color") == "#FF0000"
        ]
        self.assertEqual(red_updates, [])
        self.assertEqual(self.engine.state, GameState.PLAYING)


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

    def begin_level_one_placement(self) -> None:
        self.engine.start_gauntlet()
        self.engine.set_initials("AAA")
        self.engine.continue_action()
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLACEMENT)

    def test_placement_reports_tracked_ball_on_start(self) -> None:
        self.begin_level_one_placement()
        self.ball.set_cell(self.engine.current_level.start_cell)
        state = self.engine.public_state()
        self.assertTrue(state["placementReady"])
        self.assertTrue(state["integrations"]["tracking"]["enabled"])

    def test_reach_end_completes_after_short_stable_dwell(self) -> None:
        self.begin_level_one_placement()
        self.engine.confirm_placement()
        self.ball.set_cell(self.engine.current_level.end_cell)
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLAYING)
        self.clock.advance(0.26)
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.LEVEL_CLEAR)
        finish = self.engine.current_level.end_cell
        finish_updates = [
            update for update in self.hardware.updates if update["key"] == finish
        ]
        self.assertEqual(finish_updates[-1]["value"], -1)
        self.assertEqual(finish_updates[-1]["color"], "#FF00AA")
        finish_state = next(
            cell for cell in self.engine.map_cells if cell["key"] == finish
        )
        self.assertTrue(finish_state["sunk"])

    def test_public_state_does_not_advance_game_state(self) -> None:
        self.engine.start_gauntlet()
        self.engine.set_initials("AAA")
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
        self.begin_level_one_placement()
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

    def begin_level_one(self) -> None:
        self.engine.start_gauntlet()
        self.engine.set_initials("AAA")
        self.engine.continue_action()
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLACEMENT)
        self.engine.confirm_placement()
        self.assertEqual(self.engine.state, GameState.PLAYING)

    def test_level_score_uses_time_and_restart_formula(self) -> None:
        self.begin_level_one()
        self.clock.advance(10)
        self.engine.complete_level()
        result = self.engine.last_level_result
        self.assertIsNotNone(result)
        self.assertEqual(result.remaining_seconds, 35)
        self.assertEqual(result.score, 1350)

    def test_timeout_allows_unlimited_retry_with_penalty(self) -> None:
        self.begin_level_one()
        self.clock.advance(46)
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.TIME_UP)
        self.assertEqual(self.engine.current_restarts, 1)
        self.engine.continue_action()
        self.engine.tick()
        self.engine.confirm_placement()
        self.clock.advance(10)
        self.engine.complete_level()
        self.assertEqual(self.engine.last_level_result.score, 1250)

    def test_partial_gauntlet_creates_ranked_entry(self) -> None:
        self.begin_level_one()
        self.clock.advance(20)
        self.engine.complete_level()
        self.engine.continue_action()  # clear -> score
        self.engine.continue_action()  # score -> level 2 rules
        self.engine.abandon()
        self.assertEqual(self.engine.state, GameState.ABANDONED)
        self.engine.continue_action()
        self.assertEqual(self.engine.state, GameState.RUN_SUMMARY)
        rows = self.store.all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["initials"], "AAA")
        self.assertEqual(rows[0]["levelsCleared"], 1)
        self.assertFalse(rows[0]["complete"])

    def test_full_two_level_gauntlet_saves_complete_score(self) -> None:
        self.engine.start_gauntlet()
        self.engine.set_initials("WIN")
        for level_number in (1, 2):
            self.engine.continue_action()  # rules -> loading
            self.engine.tick()
            self.engine.confirm_placement()
            self.clock.advance(5)
            self.engine.complete_level()
            self.engine.continue_action()  # clear -> score
            self.engine.continue_action()  # next rules or summary
            if level_number < 2:
                self.assertEqual(self.engine.state, GameState.RULES)
        self.assertEqual(self.engine.state, GameState.RUN_SUMMARY)
        rows = self.store.all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["levelsCleared"], 2)
        self.assertTrue(rows[0]["complete"])
        self.assertEqual(rows[0]["gauntletLevelCount"], 2)

    def test_practice_never_writes_score(self) -> None:
        self.engine.show_level_select()
        self.engine.select_practice_level("level-2")
        self.engine.continue_action()
        self.engine.tick()
        self.engine.confirm_placement()
        self.clock.advance(5)
        self.engine.complete_level()
        self.engine.continue_action()
        self.engine.continue_action()
        self.assertEqual(self.engine.state, GameState.RUN_SUMMARY)
        self.assertEqual(self.store.all(), [])

    def test_abandon_level_select_returns_to_title(self) -> None:
        self.engine.show_level_select()
        self.engine.abandon()
        self.assertEqual(self.engine.state, GameState.ATTRACT)

    def test_abandon_during_level_loading_clears_hardware_busy(self) -> None:
        hardware = LoadingHardware()
        engine = GameEngine(self.catalog, hardware, self.store, self.clock)
        engine.setup()
        engine.start_gauntlet()
        engine.set_initials("AAA")
        engine.continue_action()
        self.assertEqual(engine.state, GameState.LEVEL_LOADING)
        self.assertTrue(hardware.snapshot()["busy"])
        engine.abandon()
        self.assertEqual(engine.state, GameState.ABANDONED)
        self.assertFalse(hardware.snapshot()["busy"])

    def test_manual_restart_exposes_restarting_state(self) -> None:
        self.begin_level_one()
        self.engine.restart()
        self.assertEqual(self.engine.state, GameState.RESTARTING)
        self.engine.tick()
        self.assertEqual(self.engine.state, GameState.PLACEMENT)

    def test_public_state_survives_repeated_refreshes(self) -> None:
        self.begin_level_one()
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
            engine.start_gauntlet()
            engine.set_initials("AAA")
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
            client.post("/api/action", json={"action": "complete"})
            self.assertFalse(tilt.active)
            app.config["ARCADE_SHUTDOWN"]()
            self.assertFalse(tilt.started)

    def test_full_setup_and_initials_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(
                hardware=SimulatedTableHardware(),
                score_path=Path(temp) / "scores.json",
                start_ticker=False,
            )
            client = app.test_client()
            response = client.post("/api/action", json={"action": "setup"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["game"]["state"], "attract")
            client.post("/api/action", json={"action": "start-gauntlet"})
            response = client.post(
                "/api/action", json={"action": "set-initials", "initials": "abc"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["game"]["initials"], "ABC")

    def test_invalid_initials_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(
                hardware=SimulatedTableHardware(),
                score_path=Path(temp) / "scores.json",
                start_ticker=False,
            )
            client = app.test_client()
            client.post("/api/action", json={"action": "setup"})
            client.post("/api/action", json={"action": "start-gauntlet"})
            response = client.post(
                "/api/action", json={"action": "set-initials", "initials": "AB1"}
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
