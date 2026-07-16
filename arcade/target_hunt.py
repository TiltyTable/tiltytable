"""Escalating target hunt ("Snake"): collect targets as walls/pits accumulate."""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any

TARGET_COLOR = "#001FFF"
FLOOR_COLOR = "#567DBB"
PIT_COLOR = "#FF0000"
WALL_COLOR = "#4DFF00"


@dataclass(frozen=True)
class TargetHuntParams:
    starting_seconds: float = 20.0
    target_bonus_seconds: float = 5.0
    target_confirm_seconds: float = 0.3
    points_per_target: int = 100
    spawn_pit_count: int = 1
    spawn_wall_count: int = 1
    seed: int = 1


@dataclass
class TargetHuntSession:
    params: TargetHuntParams
    cells: dict[str, dict[str, Any]]
    row_col: dict[str, tuple[int, int]]
    rng: random.Random
    target_cell: str | None
    remaining_seconds: float
    last_tick_at: float
    hits: int = 0
    pending_target_since: float | None = None
    updates: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TargetHuntTickResult:
    hardware_updates: list[dict[str, Any]]
    target_cell: str | None
    remaining_seconds: float
    targets_reached: int
    score: int
    lost: bool


def _neighbors(key: str, row_col: dict[str, tuple[int, int]]) -> list[str]:
    row, col = row_col[key]
    inverse = {position: cell for cell, position in row_col.items()}
    return [
        inverse[position]
        for position in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1))
        if position in inverse
    ]


def reachable_cells(
    start: str,
    cells: dict[str, dict[str, Any]],
    row_col: dict[str, tuple[int, int]],
) -> set[str]:
    if start not in cells or int(cells[start].get("value", 0)) != 0:
        return set()
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in _neighbors(current, row_col):
            if neighbor not in seen and int(cells[neighbor].get("value", 0)) == 0:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def _entry(
    key: str, row_col: dict[str, tuple[int, int]], value: int, color: str
) -> dict[str, Any]:
    row, col = row_col[key]
    return {"key": key, "row": row, "col": col, "value": value, "color": color, "rgb": (0, 0, 0)}


def _choose_target(session: TargetHuntSession, ball_cell: str) -> str | None:
    reachable = sorted(reachable_cells(ball_cell, session.cells, session.row_col) - {ball_cell})
    if not reachable:
        return None
    return session.rng.choice(reachable)


def _place_obstacles(
    session: TargetHuntSession,
    ball_cell: str,
    pit_count: int,
    wall_count: int,
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for value, color, count in ((-1, PIT_COLOR, pit_count), (1, WALL_COLOR, wall_count)):
        for _ in range(count):
            candidates = [
                key
                for key, cell in session.cells.items()
                if key not in (ball_cell, session.target_cell)
                and int(cell.get("value", 0)) == 0
            ]
            session.rng.shuffle(candidates)
            placed = False
            for candidate in candidates:
                original = session.cells[candidate]
                session.cells[candidate] = {**original, "value": value, "color": color}
                if len(reachable_cells(ball_cell, session.cells, session.row_col)) > 1:
                    updates.append(_entry(candidate, session.row_col, value, color))
                    placed = True
                    break
                session.cells[candidate] = original
            if not placed:
                break
    return updates


def start_target_hunt(
    params: TargetHuntParams,
    cells: dict[str, dict[str, Any]],
    row_col: dict[str, tuple[int, int]],
    ball_cell: str,
    now: float,
) -> TargetHuntSession:
    session = TargetHuntSession(
        params=params,
        cells={key: dict(value) for key, value in cells.items()},
        row_col=row_col,
        rng=random.Random(params.seed),
        target_cell=None,
        remaining_seconds=params.starting_seconds,
        last_tick_at=now,
    )
    session.target_cell = _choose_target(session, ball_cell)
    if session.target_cell:
        session.updates.append(_entry(session.target_cell, row_col, 0, TARGET_COLOR))
    return session


def tick_target_hunt(
    session: TargetHuntSession,
    ball_cell: str | None,
    now: float,
) -> TargetHuntTickResult:
    elapsed = max(0.0, now - session.last_tick_at)
    session.last_tick_at = now
    session.remaining_seconds = max(0.0, session.remaining_seconds - elapsed)
    updates = list(session.updates)
    session.updates.clear()

    if ball_cell and ball_cell == session.target_cell:
        if session.pending_target_since is None:
            session.pending_target_since = now
        elif now - session.pending_target_since >= session.params.target_confirm_seconds:
            previous = session.target_cell
            session.hits += 1
            session.remaining_seconds += session.params.target_bonus_seconds
            if previous:
                updates.append(_entry(previous, session.row_col, 0, FLOOR_COLOR))
            updates.extend(
                _place_obstacles(
                    session,
                    ball_cell,
                    session.params.spawn_pit_count,
                    session.params.spawn_wall_count,
                )
            )
            session.target_cell = _choose_target(session, ball_cell)
            if session.target_cell:
                updates.append(_entry(session.target_cell, session.row_col, 0, TARGET_COLOR))
            session.pending_target_since = None
    else:
        session.pending_target_since = None

    lost = session.remaining_seconds <= 0 or session.target_cell is None
    return TargetHuntTickResult(
        hardware_updates=updates,
        target_cell=session.target_cell,
        remaining_seconds=session.remaining_seconds,
        targets_reached=session.hits,
        score=session.hits * session.params.points_per_target + int(session.remaining_seconds),
        lost=lost,
    )


def params_from_dict(raw: dict[str, Any], seed: int = 1) -> TargetHuntParams:
    return TargetHuntParams(
        starting_seconds=float(raw.get("startingSeconds", 20)),
        target_bonus_seconds=float(raw.get("targetBonusSeconds", 5)),
        target_confirm_seconds=float(raw.get("targetConfirmSeconds", 0.3)),
        points_per_target=int(raw.get("pointsPerTarget", 100)),
        spawn_pit_count=int(raw.get("spawnPitCount", 1)),
        spawn_wall_count=int(raw.get("spawnWallCount", 1)),
        seed=seed,
    )
