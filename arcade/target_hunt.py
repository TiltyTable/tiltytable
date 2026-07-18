"""Snake: collect flashing food while one wall rises and one floor falls."""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .pit_detection import PIT_CONFIRM_SECONDS, PitDetector

TARGET_COLOR = "#001FFF"
FLOOR_COLOR = "#567DBB"
PIT_COLOR = "#FF0000"
WALL_COLOR = "#4DFF00"


@dataclass(frozen=True)
class TargetHuntParams:
    target_confirm_frames: int = 2
    points_per_target: int = 100
    spawn_pit_count: int = 1
    spawn_wall_count: int = 1
    minimum_reachable_cells: int = 2
    minimum_target_distance: int = 3
    blink_seconds: float = 0.25
    pit_confirm_seconds: float = PIT_CONFIRM_SECONDS
    seed: int = 1


@dataclass
class TargetHuntSession:
    params: TargetHuntParams
    cells: dict[str, dict[str, Any]]
    row_col: dict[str, tuple[int, int]]
    rng: random.Random
    target_cell: str | None
    hits: int = 0
    target_confirm_frames: int = 0
    last_observation_frame: int | None = None
    target_blink_on: bool = True
    last_blink_at: float = 0.0
    pit_detector: PitDetector = field(default_factory=PitDetector)
    updates: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TargetHuntTickResult:
    hardware_updates: list[dict[str, Any]]
    target_cell: str | None
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


def reachable_distances(
    start: str,
    cells: dict[str, dict[str, Any]],
    row_col: dict[str, tuple[int, int]],
) -> dict[str, int]:
    if start not in cells or int(cells[start].get("value", 0)) != 0:
        return {}
    distance = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in _neighbors(current, row_col):
            if neighbor not in distance and int(cells[neighbor].get("value", 0)) == 0:
                distance[neighbor] = distance[current] + 1
                queue.append(neighbor)
    return distance


def _entry(
    key: str,
    row_col: dict[str, tuple[int, int]],
    value: int,
    color: str,
    *,
    led_only: bool = False,
) -> dict[str, Any]:
    row, col = row_col[key]
    entry = {
        "key": key, "row": row, "col": col, "value": value,
        "color": color, "rgb": (0, 0, 0),
    }
    if led_only:
        entry["leds_only"] = True
    return entry


def _choose_target(session: TargetHuntSession, ball_cell: str) -> str | None:
    distances = reachable_distances(ball_cell, session.cells, session.row_col)
    preferred = sorted(
        key
        for key, distance in distances.items()
        if distance >= session.params.minimum_target_distance
    )
    candidates = preferred or sorted(
        key for key, distance in distances.items() if distance > 0
    )
    if not candidates:
        return None
    return session.rng.choice(candidates)


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
                if (
                    len(reachable_cells(ball_cell, session.cells, session.row_col))
                    >= session.params.minimum_reachable_cells
                ):
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
        last_blink_at=now,
    )
    session.target_cell = _choose_target(session, ball_cell)
    if session.target_cell:
        session.updates.append(
            _entry(session.target_cell, row_col, 0, TARGET_COLOR, led_only=True)
        )
    return session


def tick_target_hunt(
    session: TargetHuntSession,
    ball_cell: str | None,
    now: float,
    observation_frame: int | None = None,
    tracking_confidence: float | None = None,
) -> TargetHuntTickResult:
    updates = list(session.updates)
    session.updates.clear()

    on_pit = bool(
        ball_cell
        and ball_cell in session.cells
        and int(session.cells[ball_cell].get("value", 0)) == -1
    )
    lost = session.pit_detector.update(
        ball_cell=ball_cell,
        is_pit=on_pit,
        now=now,
        tracking_confidence=tracking_confidence,
        confirm_seconds=session.params.pit_confirm_seconds,
    )
    if (
        session.target_cell
        and now - session.last_blink_at >= session.params.blink_seconds
    ):
        session.target_blink_on = not session.target_blink_on
        session.last_blink_at = now
        updates.append(
            _entry(
                session.target_cell,
                session.row_col,
                0,
                TARGET_COLOR if session.target_blink_on else "#000000",
                led_only=True,
            )
        )

    is_new_observation = (
        observation_frame is None
        or observation_frame != session.last_observation_frame
    )
    if observation_frame is not None:
        session.last_observation_frame = observation_frame

    if not lost and ball_cell and ball_cell == session.target_cell:
        if is_new_observation:
            session.target_confirm_frames += 1
        if session.target_confirm_frames >= session.params.target_confirm_frames:
            previous = session.target_cell
            session.hits += 1
            if previous:
                updates.append(
                    _entry(previous, session.row_col, 0, FLOOR_COLOR, led_only=True)
                )
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
                updates.append(
                    _entry(
                        session.target_cell,
                        session.row_col,
                        0,
                        TARGET_COLOR,
                        led_only=True,
                    )
                )
                session.target_blink_on = True
                session.last_blink_at = now
            session.target_confirm_frames = 0
    else:
        session.target_confirm_frames = 0

    return TargetHuntTickResult(
        hardware_updates=updates,
        target_cell=session.target_cell,
        targets_reached=session.hits,
        score=session.hits * session.params.points_per_target,
        lost=lost,
    )


def params_from_dict(raw: dict[str, Any], seed: int = 1) -> TargetHuntParams:
    return TargetHuntParams(
        target_confirm_frames=max(1, int(raw.get("targetConfirmFrames", 2))),
        points_per_target=int(raw.get("pointsPerTarget", 100)),
        spawn_pit_count=int(raw.get("spawnPitCount", 1)),
        spawn_wall_count=int(raw.get("spawnWallCount", 1)),
        minimum_reachable_cells=int(raw.get("minimumReachableCells", 2)),
        minimum_target_distance=int(raw.get("minimumTargetDistance", 3)),
        blink_seconds=float(raw.get("blinkSeconds", 0.25)),
        pit_confirm_seconds=float(raw.get("pitConfirmSeconds", PIT_CONFIRM_SECONDS)),
        seed=seed,
    )
