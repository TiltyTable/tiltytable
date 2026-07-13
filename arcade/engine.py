from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .hardware import BaseTableHardware, HardwareError
from .levels import Level, load_map
from .storage import ScoreStore


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
        levels: list[Level],
        hardware: BaseTableHardware,
        scores: ScoreStore,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.levels = levels
        self.hardware = hardware
        self.scores = scores
        self.clock = clock
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

    @property
    def current_level(self) -> Level:
        return self.levels[self.level_index]

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
            self.level_index = 0
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
            elif state == GameState.LEVEL_CLEAR:
                self.state = GameState.LEVEL_SCORE
            elif state == GameState.LEVEL_SCORE:
                if self.mode == "practice":
                    self.state = GameState.RUN_SUMMARY
                elif self.level_index + 1 < len(self.levels):
                    self.level_index += 1
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
            self.state = GameState.PLAYING

    def restart(self) -> None:
        with self.lock:
            if self.state != GameState.PLAYING:
                return
            self._finish_attempt_elapsed()
            self.current_restarts += 1
            self.hardware.pause()
            self._prepare_level(restarting=True)

    def complete_level(self) -> None:
        with self.lock:
            if self.state != GameState.PLAYING:
                raise ValueError("A level is not currently running")
            remaining = max(0, math.floor(self._remaining_seconds()))
            elapsed_ms = self._finish_attempt_elapsed()
            score = max(0, 1000 + remaining * 10 - self.current_restarts * 100)
            result = LevelResult(
                level_id=self.current_level.id,
                level_number=self.current_level.number,
                score=score,
                remaining_seconds=remaining,
                elapsed_ms=elapsed_ms,
                restarts=self.current_restarts,
            )
            self.results.append(result)
            self.last_level_result = result
            self.hardware.pause()
            self.state = GameState.LEVEL_CLEAR

    def abandon(self) -> None:
        with self.lock:
            if self.state == GameState.LEVEL_SELECT:
                self.mode = None
                self.state = GameState.ATTRACT
                return
            if self.state in (
                GameState.PLAYING,
                GameState.PLACEMENT,
                GameState.LEVEL_LOADING,
                GameState.TIME_UP,
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
            if self.state == GameState.PLAYING and self._remaining_seconds() <= 0:
                self._finish_attempt_elapsed()
                self.current_restarts += 1
                self.hardware.pause()
                self.state = GameState.TIME_UP

    def public_state(self) -> dict[str, Any]:
        with self.lock:
            self.tick()
            level = self.current_level if self.mode else None
            remaining = (
                max(0, math.ceil(self._remaining_seconds()))
                if self.state == GameState.PLAYING
                else (level.time_limit_seconds if level else 0)
            )
            return {
                "state": self.state.value,
                "mode": self.mode,
                "initials": self.initials,
                "level": level.public_dict() if level else None,
                "levels": [item.public_dict() for item in self.levels],
                "timer": {
                    "remainingSeconds": remaining,
                    "running": self.state == GameState.PLAYING,
                },
                "score": sum(result.score for result in self.results),
                "levelsCleared": len(self.results),
                "restarts": self.current_restarts,
                "results": [result.public_dict() for result in self.results],
                "lastLevelResult": (
                    self.last_level_result.public_dict() if self.last_level_result else None
                ),
                "endedEarly": self.ended_early,
                "mapCells": self.map_cells,
                "leaderboard": self.scores.top(10),
                "hardware": self.hardware.snapshot(),
                "integrations": {
                    "tracking": {"enabled": False, "phase": "V2", "label": "Azure Kinect"},
                    "tilt": {"enabled": False, "phase": "V3", "label": "Stewart + roller ball"},
                },
                "error": self.error,
            }

    def _prepare_level(self, restarting: bool = False) -> None:
        level = self.current_level
        raw = load_map(level)
        self.map_cells = [
            {
                "key": key,
                "value": int(cell["value"]),
                "color": cell.get("color", "#FFFFFF"),
                "dynamic": bool(cell.get("dynamic")),
            }
            for key, cell in raw.items()
        ]
        for cell in self.map_cells:
            if cell["key"] == level.start_cell:
                cell["color"] = "#00E5FF"
            elif cell["key"] == level.end_cell:
                cell["color"] = "#FF00AA"
        self.state = GameState.RESTARTING if restarting else GameState.LEVEL_LOADING
        try:
            self.hardware.load_level(level.map_path, level.start_cell, level.end_cell)
        except Exception as exc:
            self._fault(exc)

    def _remaining_seconds(self) -> float:
        if self.attempt_started_at is None:
            return float(self.current_level.time_limit_seconds)
        elapsed = self.clock() - self.attempt_started_at
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

