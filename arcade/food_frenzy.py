"""Escalating timed food rounds with a full-board celebration between rounds."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

FOOD_COLOR = "#001FFF"
FLOOR_COLOR = "#567DBB"


@dataclass(frozen=True)
class FoodFrenzyParams:
    round_seconds: float = 30.0
    target_confirm_frames: int = 2
    blink_seconds: float = 0.25
    celebration_seconds: float = 1.0
    points_per_food: int = 1
    seed: int = 1


@dataclass
class FoodFrenzySession:
    params: FoodFrenzyParams
    cells: dict[str, dict[str, Any]]
    row_col: dict[str, tuple[int, int]]
    rng: random.Random
    target_cells: set[str]
    remaining_seconds: float
    last_tick_at: float
    round_number: int = 1
    foods_collected: int = 0
    confirm_cell: str | None = None
    confirm_frames: int = 0
    last_observation_frame: int | None = None
    celebrating_until: float | None = None
    target_blink_on: bool = True
    last_blink_at: float = 0.0
    updates: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class FoodFrenzyTickResult:
    hardware_updates: list[dict[str, Any]]
    target_cells: tuple[str, ...]
    round_number: int
    foods_collected: int
    remaining_seconds: float
    score: int
    celebrating: bool
    lost: bool
    effect: str | None = None


def _entry(
    key: str,
    row_col: dict[str, tuple[int, int]],
    color: str,
) -> dict[str, Any]:
    row, col = row_col[key]
    return {
        "key": key,
        "row": row,
        "col": col,
        "value": 0,
        "color": color,
        "rgb": (0, 0, 0),
        "leds_only": True,
    }


def _spawn_food(
    session: FoodFrenzySession,
    ball_cell: str | None,
    now: float,
) -> None:
    candidates = [
        key
        for key, cell in session.cells.items()
        if int(cell.get("value", 0)) == 0 and key != ball_cell
    ]
    session.rng.shuffle(candidates)
    count = min(session.round_number, len(candidates))
    session.target_cells = set(candidates[:count])
    session.target_blink_on = True
    session.last_blink_at = now
    session.updates.extend(
        _entry(key, session.row_col, FOOD_COLOR)
        for key in sorted(session.target_cells)
    )


def start_food_frenzy(
    params: FoodFrenzyParams,
    cells: dict[str, dict[str, Any]],
    row_col: dict[str, tuple[int, int]],
    ball_cell: str | None,
    now: float,
) -> FoodFrenzySession:
    session = FoodFrenzySession(
        params=params,
        cells={key: dict(value) for key, value in cells.items()},
        row_col=row_col,
        rng=random.Random(params.seed),
        target_cells=set(),
        remaining_seconds=params.round_seconds,
        last_tick_at=now,
    )
    _spawn_food(session, ball_cell, now)
    return session


def tick_food_frenzy(
    session: FoodFrenzySession,
    ball_cell: str | None,
    now: float,
    observation_frame: int | None = None,
) -> FoodFrenzyTickResult:
    elapsed = max(0.0, now - session.last_tick_at)
    session.last_tick_at = now
    updates = list(session.updates)
    session.updates.clear()

    if session.celebrating_until is not None:
        if now >= session.celebrating_until:
            session.celebrating_until = None
            session.round_number += 1
            session.remaining_seconds = session.params.round_seconds
            _spawn_food(session, ball_cell, now)
            updates.extend(session.updates)
            session.updates.clear()
        return FoodFrenzyTickResult(
            hardware_updates=updates,
            target_cells=tuple(sorted(session.target_cells)),
            round_number=session.round_number,
            foods_collected=session.foods_collected,
            remaining_seconds=session.remaining_seconds,
            score=session.foods_collected * session.params.points_per_food,
            celebrating=session.celebrating_until is not None,
            lost=False,
        )

    session.remaining_seconds = max(0.0, session.remaining_seconds - elapsed)
    lost = session.remaining_seconds <= 0.0
    effect: str | None = None

    if (
        session.target_cells
        and now - session.last_blink_at >= session.params.blink_seconds
    ):
        session.target_blink_on = not session.target_blink_on
        session.last_blink_at = now
        blink_color = FOOD_COLOR if session.target_blink_on else "#000000"
        updates.extend(
            _entry(key, session.row_col, blink_color)
            for key in sorted(session.target_cells)
        )

    is_new_observation = (
        observation_frame is None
        or observation_frame != session.last_observation_frame
    )
    if observation_frame is not None:
        session.last_observation_frame = observation_frame

    if not lost and ball_cell in session.target_cells:
        if session.confirm_cell != ball_cell:
            session.confirm_cell = ball_cell
            session.confirm_frames = 0
        if is_new_observation:
            session.confirm_frames += 1
        if session.confirm_frames >= session.params.target_confirm_frames:
            assert ball_cell is not None
            session.target_cells.remove(ball_cell)
            session.foods_collected += 1
            updates.append(_entry(ball_cell, session.row_col, FLOOR_COLOR))
            session.confirm_cell = None
            session.confirm_frames = 0
            if not session.target_cells:
                session.celebrating_until = (
                    now + session.params.celebration_seconds
                )
                effect = "flash_all"
    else:
        session.confirm_cell = None
        session.confirm_frames = 0

    return FoodFrenzyTickResult(
        hardware_updates=updates,
        target_cells=tuple(sorted(session.target_cells)),
        round_number=session.round_number,
        foods_collected=session.foods_collected,
        remaining_seconds=session.remaining_seconds,
        score=session.foods_collected * session.params.points_per_food,
        celebrating=session.celebrating_until is not None,
        lost=lost,
        effect=effect,
    )


def params_from_dict(raw: dict[str, Any], seed: int = 1) -> FoodFrenzyParams:
    return FoodFrenzyParams(
        round_seconds=float(raw.get("roundSeconds", 30)),
        target_confirm_frames=max(1, int(raw.get("targetConfirmFrames", 2))),
        blink_seconds=float(raw.get("blinkSeconds", 0.25)),
        celebration_seconds=float(raw.get("celebrationSeconds", 1)),
        points_per_food=int(raw.get("pointsPerFood", 1)),
        seed=seed,
    )
