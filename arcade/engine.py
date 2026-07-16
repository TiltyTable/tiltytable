from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .hardware import BaseTableHardware, HardwareError
from .integrations import BallObservation, BallTrackingAdapter, TiltControlAdapter
from .game_modes import start_mode, tick_mode
from .levels import Level, LevelCatalog, load_map
from .storage import ScoreStore
from .survival_lava import (
    DEFAULT_PIT_CONFIRM_SECONDS,
    FLOOR_COLOR,
    SurvivalLavaSession,
    SurvivalParams,
    survival_score,
    tick_survival_lava,
)

TRACKING_CONFIDENCE_MIN = 0.7
END_CELL_DWELL_SECONDS = 0.25


class GameState(str, Enum):
    SETUP = "setup"
    ATTRACT = "attract"
    INITIALS = "initials"
    LEVEL_SELECT = "level_select"
    RULES = "rules"
    LEVEL_LOADING = "level_loading"
    RESTARTING = "restarting"
    PLACEMENT = "placement"
    PLAYING = "playing"
    TIME_UP = "time_up"
    SURVIVAL_FAIL = "survival_fail"
    LEVEL_CLEAR = "level_clear"
    LEVEL_SCORE = "level_score"
    RUN_SUMMARY = "run_summary"
    ABANDONED = "abandoned"
    LEADERBOARD = "leaderboard"
    HARDWARE_FAULT = "hardware_fault"


@dataclass
class LevelResult:
    level_id: str
    level_number: int
    score: int
    remaining_seconds: int
    elapsed_ms: int
    restarts: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "levelId": self.level_id,
            "levelNumber": self.level_number,
            "score": self.score,
            "remainingSeconds": self.remaining_seconds,
            "elapsedMs": self.elapsed_ms,
            "restarts": self.restarts,
        }


class GameEngine:
    def __init__(
        self,
        catalog: LevelCatalog,
        hardware: BaseTableHardware,
        scores: ScoreStore,
        clock: Callable[[], float] = time.monotonic,
        ball_adapter: BallTrackingAdapter | None = None,
        tilt_adapter: TiltControlAdapter | None = None,
    ) -> None:
        self.catalog = catalog
        self.levels = list(catalog.levels)
        self.gauntlet_indices = list(catalog.gauntlet_indices())
        self.hardware = hardware
        self.scores = scores
        self.clock = clock
        self.ball_adapter = ball_adapter
        self.tilt_adapter = tilt_adapter
        self.lock = threading.RLock()

        self.state = GameState.SETUP
        self.mode: str | None = None
        self.initials = ""
        self.level_index = 0
        self.results: list[LevelResult] = []
        self.current_restarts = 0
        self.run_elapsed_ms = 0
        self.attempt_started_at: float | None = None
        self.last_level_result: LevelResult | None = None
        self.ended_early = False
        self.score_saved = False
        self.error = ""
        self.map_cells: list[dict[str, Any]] = []
        self._row_col_by_key: dict[str, tuple[int, int]] = {}
        self._survival: SurvivalLavaSession | None = None
        self._survival_visited = 0
        self._survival_ball_cell: str | None = None
        self._survival_heating = False
        self._last_survival_tick = 0.0
        self._mode_session: Any | None = None
        self._mode_state: dict[str, Any] | None = None
        self._mode_score = 0
        self._end_cell_since: float | None = None

    @property
    def current_level(self) -> Level:
        return self.levels[self.level_index]

    def tilt_requested(self) -> bool:
        with self.lock:
            return self.state in (GameState.PLACEMENT, GameState.PLAYING)

    def setup(self) -> None:
        with self.lock:
            if self.state not in (GameState.SETUP, GameState.HARDWARE_FAULT):
                return
            self.error = ""
        try:
            self.hardware.initialize()
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.state = GameState.HARDWARE_FAULT
            return
        with self.lock:
            self.state = GameState.ATTRACT

    def start_gauntlet(self) -> None:
        with self.lock:
            self._reset_run()
            self.mode = "gauntlet"
            self.state = GameState.INITIALS

    def set_initials(self, initials: str) -> None:
        clean = initials.strip().upper()
        if len(clean) != 3 or not clean.isalpha():
            raise ValueError("Enter exactly three letters")
        with self.lock:
            if self.state != GameState.INITIALS:
                raise ValueError("Initials are not expected right now")
            self.initials = clean
            self.level_index = self.gauntlet_indices[0]
            self.state = GameState.RULES

    def show_level_select(self) -> None:
        with self.lock:
            self._reset_run()
            self.mode = "practice"
            self.state = GameState.LEVEL_SELECT

    def select_practice_level(self, level_id: str) -> None:
        with self.lock:
            if self.state != GameState.LEVEL_SELECT:
                raise ValueError("Level selection is not active")
            for index, level in enumerate(self.levels):
                if level.id == level_id:
                    self.level_index = index
                    self.state = GameState.RULES
                    return
        raise ValueError(f"Unknown level: {level_id}")

    def continue_action(self) -> None:
        with self.lock:
            state = self.state
            if state == GameState.RULES:
                self.current_restarts = 0
                self._prepare_level()
            elif state == GameState.TIME_UP:
                self._prepare_level(restarting=True)
            elif state == GameState.SURVIVAL_FAIL:
                self._prepare_level(restarting=True)
            elif state == GameState.LEVEL_CLEAR:
                self.state = GameState.LEVEL_SCORE
            elif state == GameState.LEVEL_SCORE:
                if self.mode == "practice":
                    self.state = GameState.RUN_SUMMARY
                elif self._advance_gauntlet():
                    self.current_restarts = 0
                    self.state = GameState.RULES
                else:
                    self._finalize_score(complete=True)
                    self.state = GameState.RUN_SUMMARY
            elif state == GameState.RUN_SUMMARY:
                self.state = (
                    GameState.LEVEL_SELECT
                    if self.mode == "practice"
                    else GameState.LEADERBOARD
                )
            elif state == GameState.ABANDONED:
                if self.mode == "gauntlet" and self.results:
                    self.state = GameState.RUN_SUMMARY
                elif self.mode == "practice":
                    self.state = GameState.LEVEL_SELECT
                else:
                    self.state = GameState.ATTRACT
            elif state == GameState.LEADERBOARD:
                self.state = GameState.ATTRACT

    def confirm_placement(self) -> None:
        with self.lock:
            if self.state != GameState.PLACEMENT:
                raise ValueError("The level is not waiting for ball placement")
            try:
                self.hardware.begin_play()
            except Exception as exc:
                self._fault(exc)
                return
            self.attempt_started_at = self.clock()
            self._end_cell_since = None
            self._last_survival_tick = 0.0
            if self.current_level.is_survival_lava:
                self._mode_session = None
                self._mode_state = None
                self._survival = SurvivalLavaSession(
                    params=SurvivalParams(
                        survival_seconds=float(self.current_level.survival_seconds or 0),
                        dwell_seconds=float(self.current_level.dwell_seconds or 0),
                        warn_seconds=float(self.current_level.warn_seconds or 0),
                        points_per_tile=int(self.current_level.points_per_tile or 0),
                        floor_color=FLOOR_COLOR,
                        settle_seconds=0.0,
                        pit_confirm_seconds=float(
                            self.current_level.pit_confirm_seconds
                            if self.current_level.pit_confirm_seconds is not None
                            else DEFAULT_PIT_CONFIRM_SECONDS
                        ),
                    ),
                    started_at=self.attempt_started_at,
                )
                self._survival_visited = 0
                self._survival_ball_cell = None
                self._survival_heating = False
                self._neutralize_survival_start_cell()
            elif self.current_level.mode in ("hex_fall", "target_hunt"):
                self._survival = None
                self._mode_session = start_mode(
                    self.current_level.mode,
                    dict(self.current_level.mode_params or {}),
                    seed=self.current_level.seed,
                    now=self.attempt_started_at,
                    cells={cell["key"]: dict(cell) for cell in self.map_cells},
                    row_col=self._row_col_by_key,
                    ball_cell=self._current_ball_cell(),
                )
                self._mode_state = {}
                self._mode_score = 0
            else:
                self._survival = None
                self._mode_session = None
                self._mode_state = None
            self.state = GameState.PLAYING

    def restart(self) -> None:
        with self.lock:
            if self.state != GameState.PLAYING:
                return
            self._finish_attempt_elapsed()
            self.current_restarts += 1
            self.hardware.pause()
            self._prepare_level(restarting=True)

    def set_ball_cell(self, cell: str | None) -> None:
        adapter = self.ball_adapter
        if adapter is None or not hasattr(adapter, "set_cell"):
            raise ValueError("Ball cell override is not available")
        adapter.set_cell(cell)  # type: ignore[attr-defined]

    def complete_level(self) -> None:
        with self.lock:
            if self.state != GameState.PLAYING:
                raise ValueError("A level is not currently running")
            level = self.current_level
            if level.mode in ("survival_lava", "hex_fall", "target_hunt"):
                raise ValueError("Dynamic mode chambers resolve automatically")
            remaining = max(0, math.floor(self._remaining_seconds()))
            elapsed_ms = self._finish_attempt_elapsed()
            score = max(0, 1000 + remaining * 10 - self.current_restarts * 100)
            result = LevelResult(
                level_id=level.id,
                level_number=level.number,
                score=score,
                remaining_seconds=remaining,
                elapsed_ms=elapsed_ms,
                restarts=self.current_restarts,
            )
            self.results.append(result)
            self.last_level_result = result
            self.hardware.pause()
            self.state = GameState.LEVEL_CLEAR

    def _complete_survival_win(self) -> None:
        level = self.current_level
        remaining = max(0, math.floor(self._remaining_seconds()))
        elapsed_ms = self._finish_attempt_elapsed()
        visited = self._survival_visited if self._survival else 0
        points = level.points_per_tile or 0
        score = survival_score(visited, remaining, self.current_restarts, points)
        result = LevelResult(
            level_id=level.id,
            level_number=level.number,
            score=score,
            remaining_seconds=remaining,
            elapsed_ms=elapsed_ms,
            restarts=self.current_restarts,
        )
        self.results.append(result)
        self.last_level_result = result
        self.hardware.pause()
        self._survival = None
        self.state = GameState.LEVEL_CLEAR

    def _complete_dynamic_mode_win(self) -> None:
        level = self.current_level
        remaining = max(0, math.floor(self._remaining_seconds()))
        elapsed_ms = self._finish_attempt_elapsed()
        result = LevelResult(
            level_id=level.id,
            level_number=level.number,
            score=max(0, self._mode_score),
            remaining_seconds=remaining,
            elapsed_ms=elapsed_ms,
            restarts=self.current_restarts,
        )
        self.results.append(result)
        self.last_level_result = result
        self.hardware.pause()
        self._mode_session = None
        self.state = GameState.LEVEL_CLEAR

    def abandon(self) -> None:
        with self.lock:
            if self.state == GameState.LEVEL_SELECT:
                self.mode = None
                self.state = GameState.ATTRACT
                return
            if self.state in (GameState.LEVEL_LOADING, GameState.RESTARTING):
                self.hardware.cancel_load()
            elif self.state in (
                GameState.PLAYING,
                GameState.PLACEMENT,
                GameState.TIME_UP,
                GameState.SURVIVAL_FAIL,
                GameState.RULES,
            ):
                if self.state == GameState.PLAYING:
                    self._finish_attempt_elapsed()
                self.hardware.pause()
            if self.mode == "gauntlet" and self.results:
                self.ended_early = True
                self._finalize_score(complete=False)
            self.state = GameState.ABANDONED

    def tick(self) -> None:
        with self.lock:
            hardware = self.hardware.snapshot()
            if hardware.get("error") and self.state != GameState.HARDWARE_FAULT:
                self.error = str(hardware["error"])
                self.state = GameState.HARDWARE_FAULT
                return
            if self.state in (GameState.LEVEL_LOADING, GameState.RESTARTING) and not hardware.get("busy"):
                self.state = GameState.PLACEMENT
            if self.state == GameState.PLAYING:
                level = self.current_level
                if level.is_survival_lava and self._survival is not None:
                    self._tick_survival_lava()
                elif level.mode in ("hex_fall", "target_hunt") and self._mode_session is not None:
                    self._tick_dynamic_mode()
                else:
                    self._tick_reach_end()

    def _tick_reach_end(self) -> None:
        now = self.clock()
        observation = self._ball_observation()
        if (
            observation.cell == self.current_level.end_cell
            and observation.confidence >= TRACKING_CONFIDENCE_MIN
        ):
            if self._end_cell_since is None:
                self._end_cell_since = now
            elif now - self._end_cell_since >= END_CELL_DWELL_SECONDS:
                self.complete_level()
                return
        else:
            self._end_cell_since = None

        if self._remaining_seconds() <= 0:
            self._finish_attempt_elapsed()
            self.current_restarts += 1
            self.hardware.pause()
            self.state = GameState.TIME_UP

    def _tick_survival_lava(self) -> None:
        level = self.current_level
        now = self.clock()
        if now - self._last_survival_tick < 0.12:
            return
        self._last_survival_tick = now
        if self._survival is None:
            return

        observation = self._ball_observation()
        ball_cell = observation.cell
        result = tick_survival_lava(
            self._survival,
            ball_cell,
            now,
            self._row_col_by_key,
            tracking_confidence=observation.confidence,
        )
        self._survival_visited = result.visited_count
        self._survival_ball_cell = ball_cell
        self._survival_heating = result.ball_cell_heating

        if result.hardware_updates:
            self.hardware.apply_cell_updates(result.hardware_updates)
            self._apply_map_cell_updates(result.hardware_updates)

        if result.ball_on_lava:
            self._finish_attempt_elapsed()
            self.current_restarts += 1
            self.hardware.pause()
            self._survival = None
            self.state = GameState.SURVIVAL_FAIL
            return

        if result.survived:
            self._complete_survival_win()

    def _tick_dynamic_mode(self) -> None:
        now = self.clock()
        if now - self._last_survival_tick < 0.1 or self._mode_session is None:
            return
        self._last_survival_tick = now
        level = self.current_level
        observation = self._ball_observation()
        result = tick_mode(
            str(level.mode),
            self._mode_session,
            dict(level.mode_params or {}),
            seed=level.seed,
            ball_cell=observation.cell,
            now=now,
            row_col=self._row_col_by_key,
            tracking_confidence=observation.confidence,
        )
        self._mode_state = result.public_state
        self._mode_score = result.score
        if result.hardware_updates:
            self.hardware.apply_cell_updates(result.hardware_updates)
            self._apply_map_cell_updates(result.hardware_updates)
        if result.lost:
            self._finish_attempt_elapsed()
            self.current_restarts += 1
            self.hardware.pause()
            self._mode_session = None
            self.state = GameState.SURVIVAL_FAIL
        elif result.won:
            self._complete_dynamic_mode_win()

    def _current_ball_cell(self) -> str | None:
        return self._ball_observation().cell

    def _ball_observation(self) -> BallObservation:
        if self.ball_adapter is None:
            return BallObservation()
        return self.ball_adapter.observation()

    def _ball_public_state(self) -> dict[str, Any] | None:
        if self.ball_adapter is None:
            return None
        observation = self._ball_observation()
        cell = observation.cell
        row: int | None = None
        col: int | None = None
        if cell:
            from game_runner import cell_key_to_row_col

            try:
                row, col = cell_key_to_row_col(cell)
            except ValueError:
                pass
        return {
            "cell": cell,
            "confidence": round(observation.confidence, 2),
            "row": row,
            "col": col,
            "ageSeconds": observation.age_s,
            "poseFresh": observation.pose_fresh,
        }

    def _neutralize_survival_start_cell(self) -> None:
        """Drop cyan placement tint on the start tile once survival play begins."""
        level = self.current_level
        start = level.start_cell
        if start not in self._row_col_by_key:
            return
        row, col = self._row_col_by_key[start]
        update = {
            "key": start,
            "row": row,
            "col": col,
            "value": 0,
            "color": FLOOR_COLOR,
            "rgb": (0, 0, 0),
        }
        self.hardware.apply_cell_updates([update])
        self._apply_map_cell_updates([update])

    def _apply_map_cell_updates(self, updates: list[dict[str, Any]]) -> None:
        by_key = {cell["key"]: cell for cell in self.map_cells}
        for update in updates:
            key = update["key"]
            if key not in by_key:
                continue
            by_key[key]["value"] = int(update["value"])
            by_key[key]["color"] = str(update.get("color", by_key[key]["color"]))
            if int(update["value"]) == -1:
                by_key[key]["sunk"] = True

    def public_state(self) -> dict[str, Any]:
        with self.lock:
            level = self.current_level if self.mode else None
            remaining = (
                max(0, math.ceil(self._remaining_seconds()))
                if self.state == GameState.PLAYING
                else (level.time_limit_seconds if level else 0)
            )
            if (
                self.state == GameState.PLAYING
                and self._mode_state
                and "remainingSeconds" in self._mode_state
            ):
                remaining = max(0, math.ceil(float(self._mode_state["remainingSeconds"])))
            survival_payload = None
            if level and level.is_survival_lava:
                survival_payload = {
                    "active": self.state == GameState.PLAYING,
                    "tilesVisited": self._survival_visited,
                    "ballCell": self._survival_ball_cell,
                    "heating": self._survival_heating,
                    "survivalSeconds": level.survival_seconds,
                    "dwellSeconds": level.dwell_seconds,
                    "warnSeconds": level.warn_seconds,
                    "pointsPerTile": level.points_per_tile,
                }
            ball_payload = self._ball_public_state()
            tracking_enabled = bool(
                self.ball_adapter is not None
                and getattr(self.ball_adapter, "is_live", False)
            )
            tracking_label = (
                getattr(self.ball_adapter, "label", "Ball tracking")
                if self.ball_adapter is not None
                else "Unavailable"
            )
            tracking_confidence = (
                float(ball_payload["confidence"]) if ball_payload is not None else 0.0
            )
            tilt_status = (
                self.tilt_adapter.status() if self.tilt_adapter is not None else None
            )
            placement_ready = bool(
                self.state == GameState.PLACEMENT
                and level is not None
                and ball_payload is not None
                and ball_payload["cell"] == level.start_cell
                and tracking_confidence >= TRACKING_CONFIDENCE_MIN
            )
            gauntlet_cleared = len(self.results) if self.mode == "gauntlet" else 0
            live_mode_score = 0
            if self.state == GameState.PLAYING and level:
                if level.is_survival_lava:
                    live_mode_score = self._survival_visited * int(
                        level.points_per_tile or 0
                    )
                elif level.mode in ("hex_fall", "target_hunt"):
                    live_mode_score = self._mode_score
            payload: dict[str, Any] = {
                "state": self.state.value,
                "mode": self.mode,
                "initials": self.initials,
                "level": level.public_dict() if level else None,
                "levels": [item.public_dict() for item in self.levels],
                "catalog": self.catalog.public_dict(),
                "gauntletLevelCount": self.catalog.gauntlet_level_count,
                "gauntletLevelsCleared": gauntlet_cleared,
                "timer": {
                    "remainingSeconds": remaining,
                    "running": self.state == GameState.PLAYING,
                },
                "survival": survival_payload,
                "modeState": self._mode_state,
                "score": sum(result.score for result in self.results) + live_mode_score,
                "currentModeScore": live_mode_score,
                "levelsCleared": len(self.results),
                "restarts": self.current_restarts,
                "placementReady": placement_ready,
                "results": [result.public_dict() for result in self.results],
                "lastLevelResult": (
                    self.last_level_result.public_dict() if self.last_level_result else None
                ),
                "endedEarly": self.ended_early,
                "mapCells": self.map_cells,
                "leaderboard": self.scores.top(10),
                "hardware": self.hardware.snapshot(),
                "integrations": {
                    "tracking": {
                        "enabled": tracking_enabled,
                        "label": tracking_label,
                        "confidence": round(tracking_confidence, 2),
                    },
                    "tilt": {
                        "enabled": bool(tilt_status and tilt_status.enabled),
                        "active": bool(tilt_status and tilt_status.active),
                        "label": (
                            getattr(self.tilt_adapter, "label", "Stewart + roller ball")
                            if self.tilt_adapter is not None
                            else "Stewart + roller ball"
                        ),
                    },
                },
                "error": self.error,
            }
            if ball_payload is not None:
                payload["ball"] = ball_payload
            return payload

    def _prepare_level(self, restarting: bool = False) -> None:
        level = self.current_level
        raw = load_map(level)
        self.map_cells = []
        self._row_col_by_key = {}
        from game_runner import cell_key_to_row_col

        for key, cell in raw.items():
            if not isinstance(cell, dict) or "value" not in cell:
                continue
            row, col = cell_key_to_row_col(key)
            self._row_col_by_key[key] = (row, col)
            dyn = cell.get("dynamic") or {}
            self.map_cells.append(
                {
                    "key": key,
                    "value": int(cell["value"]),
                    "color": cell.get("color", "#000000"),
                    "dynamic": bool(dyn),
                    "dynamicType": str(dyn.get("type", "cycle")) if dyn else None,
                    "blinkUntilPlay": bool(cell.get("blinkUntilPlay")),
                }
            )
        for cell in self.map_cells:
            if cell["key"] == level.start_cell:
                cell["color"] = "#00FFFF"
            elif cell["key"] == level.end_cell:
                cell["color"] = "#680056"
        self._mode_session = None
        self._mode_state = None
        self._mode_score = 0
        self._end_cell_since = None
        self.state = GameState.RESTARTING if restarting else GameState.LEVEL_LOADING
        try:
            self.hardware.load_level(level.map_path, level.start_cell, level.end_cell)
        except Exception as exc:
            self._fault(exc)

    def _remaining_seconds(self) -> float:
        if self.attempt_started_at is None:
            limit = self.current_level.time_limit_seconds
            if self.current_level.is_survival_lava:
                return float(self.current_level.survival_seconds or limit)
            return float(limit)
        elapsed = self.clock() - self.attempt_started_at
        if self.current_level.is_survival_lava:
            return float(self.current_level.survival_seconds or 0) - elapsed
        return self.current_level.time_limit_seconds - elapsed

    def _finish_attempt_elapsed(self) -> int:
        if self.attempt_started_at is None:
            return 0
        elapsed_ms = max(0, int((self.clock() - self.attempt_started_at) * 1000))
        self.run_elapsed_ms += elapsed_ms
        self.attempt_started_at = None
        return elapsed_ms

    def _finalize_score(self, complete: bool) -> None:
        if self.score_saved or self.mode != "gauntlet" or not self.results:
            return
        self.scores.add(
            {
                "initials": self.initials,
                "score": sum(result.score for result in self.results),
                "levelsCleared": len(self.results),
                "gauntletLevelCount": self.catalog.gauntlet_level_count,
                "elapsedMs": self.run_elapsed_ms,
                "restarts": sum(result.restarts for result in self.results),
                "complete": complete,
            }
        )
        self.score_saved = True

    def _fault(self, exc: Exception) -> None:
        self.error = str(exc)
        self.state = GameState.HARDWARE_FAULT
        try:
            self.hardware.pause()
        except Exception:
            pass

    def _gauntlet_position(self) -> int | None:
        try:
            return self.gauntlet_indices.index(self.level_index)
        except ValueError:
            return None

    def _advance_gauntlet(self) -> bool:
        position = self._gauntlet_position()
        if position is None:
            return False
        if position + 1 >= len(self.gauntlet_indices):
            return False
        self.level_index = self.gauntlet_indices[position + 1]
        return True

    def _reset_run(self) -> None:
        self.initials = ""
        self.level_index = 0
        self.results = []
        self.current_restarts = 0
        self.run_elapsed_ms = 0
        self.attempt_started_at = None
        self.last_level_result = None
        self.ended_early = False
        self.score_saved = False
        self.error = ""
        self.map_cells = []
        self._survival = None
        self._survival_visited = 0
        self._survival_ball_cell = None
        self._survival_heating = False
        self._mode_session = None
        self._mode_state = None
        self._mode_score = 0
        self._end_cell_since = None
