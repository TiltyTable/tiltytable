"""Hex-A-Fall: explore the floor while random cells blink and fall away."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .survival_lava import (
    FLOOR_COLOR,
    LAVA_COLOR,
    PHASE_SUNK,
    VISITED_COLOR,
    CellSurvivalState,
    SurvivalLavaSession,
    SurvivalParams,
)


@dataclass(frozen=True)
class HexFallParams:
    survival_seconds: float = 45.0
    pit_confirm_seconds: float = 0.5
    collapse_every_seconds: float = 3.0
    collapse_count: int = 1
    collapse_warn_seconds: float = 1.0
    points_per_tile: int = 1
    seed: int = 1


@dataclass
class HexFallSession:
    lava: SurvivalLavaSession
    rng: random.Random
    next_collapse_at: float | None
    pit_cell: str | None = None
    pit_since: float | None = None
    pending_collapses: dict[str, tuple[float, float]] = field(default_factory=dict)
    blocked_cells: set[str] = field(default_factory=set)
    touched_cells: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class HexFallTickResult:
    hardware_updates: list[dict[str, Any]]
    tiles_touched: int
    score: int
    ball_on_lava: bool
    survived: bool
    elapsed_seconds: float
    remaining_seconds: float
    ball_cell_heating: bool = False


def _neighbors(
    key: str, row_col_for_key: dict[str, tuple[int, int]]
) -> list[str]:
    inverse = {position: cell for cell, position in row_col_for_key.items()}
    row, col = row_col_for_key[key]
    return [
        inverse[position]
        for position in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1))
        if position in inverse
    ]


def _active_cells(
    session: HexFallSession, row_col_for_key: dict[str, tuple[int, int]]
) -> set[str]:
    return {
        key
        for key in row_col_for_key
        if session.lava.cells.get(key, CellSurvivalState()).phase != PHASE_SUNK
        and key not in session.pending_collapses
        and key not in session.blocked_cells
    }


def _connected_from(
    start: str, active: set[str], row_col_for_key: dict[str, tuple[int, int]]
) -> set[str]:
    if start not in active:
        return set()
    seen, stack = {start}, [start]
    while stack:
        current = stack.pop()
        for neighbor in _neighbors(current, row_col_for_key):
            if neighbor in active and neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return seen


def safe_collapse_candidates(
    session: HexFallSession,
    ball_cell: str | None,
    row_col_for_key: dict[str, tuple[int, int]],
) -> list[str]:
    """Tiles removable without disconnecting any remaining floor from the ball."""
    if ball_cell is None:
        return []
    active = _active_cells(session, row_col_for_key)
    candidates: list[str] = []
    for candidate in sorted(active - {ball_cell}):
        remaining = active - {candidate}
        if _connected_from(ball_cell, remaining, row_col_for_key) == remaining:
            candidates.append(candidate)
    return candidates


def start_hex_fall(
    params: HexFallParams,
    now: float,
    cells: dict[str, dict[str, Any]] | None = None,
) -> HexFallSession:
    lava = SurvivalLavaSession(
        params=SurvivalParams(
            survival_seconds=params.survival_seconds,
            dwell_seconds=0,
            warn_seconds=0,
            points_per_tile=1,
            floor_color=FLOOR_COLOR,
            settle_seconds=0.0,
            pit_confirm_seconds=params.pit_confirm_seconds,
        ),
        started_at=now,
    )
    next_collapse = (
        now + params.collapse_every_seconds
        if params.collapse_every_seconds > 0 and params.collapse_count > 0
        else None
    )
    return HexFallSession(
        lava=lava,
        rng=random.Random(params.seed),
        next_collapse_at=next_collapse,
        blocked_cells={
            key for key, cell in (cells or {}).items() if int(cell.get("value", 0)) != 0
        },
    )


def tick_hex_fall(
    session: HexFallSession,
    params: HexFallParams,
    ball_cell: str | None,
    now: float,
    row_col_for_key: dict[str, tuple[int, int]],
    tracking_confidence: float | None = None,
) -> HexFallTickResult:
    elapsed = max(0.0, now - session.lava.started_at)
    remaining = max(0.0, params.survival_seconds - elapsed)
    updates: list[dict[str, Any]] = []
    for key, (warn_at, sink_at) in list(session.pending_collapses.items()):
        row, col = row_col_for_key[key]
        if now >= sink_at:
            session.lava.cells[key] = CellSurvivalState(phase=PHASE_SUNK, sunk_at=now)
            updates.append(
                {"key": key, "row": row, "col": col, "value": -1, "color": LAVA_COLOR, "rgb": (0, 0, 0)}
            )
            del session.pending_collapses[key]
        else:
            blink_on = int((now - warn_at) * 6) % 2 == 0
            updates.append(
                {
                    "key": key, "row": row, "col": col, "value": 0,
                    "color": LAVA_COLOR if blink_on else "#000000",
                    "rgb": (0, 0, 0), "leds_only": True,
                }
            )
    if session.next_collapse_at is not None and now >= session.next_collapse_at:
        for _ in range(params.collapse_count):
            available = safe_collapse_candidates(session, ball_cell, row_col_for_key)
            if not available:
                break
            key = session.rng.choice(available)
            session.pending_collapses[key] = (
                now,
                now + params.collapse_warn_seconds,
            )
            row, col = row_col_for_key[key]
            updates.append(
                {
                    "key": key, "row": row, "col": col, "value": 0,
                    "color": LAVA_COLOR, "rgb": (0, 0, 0), "leds_only": True,
                }
            )
        session.next_collapse_at += params.collapse_every_seconds
    active = _active_cells(session, row_col_for_key)
    if ball_cell in active and ball_cell not in session.touched_cells:
        session.touched_cells.add(ball_cell)
        row, col = row_col_for_key[ball_cell]
        updates.append(
            {
                "key": ball_cell,
                "row": row,
                "col": col,
                "value": 0,
                "color": VISITED_COLOR,
                "rgb": (0, 0, 0),
                "leds_only": True,
            }
        )
    on_sunk = (
        ball_cell is not None
        and session.lava.cells.get(ball_cell, CellSurvivalState()).phase == PHASE_SUNK
        and (tracking_confidence is None or tracking_confidence >= 0.7)
    )
    if on_sunk:
        if session.pit_cell != ball_cell:
            session.pit_cell = ball_cell
            session.pit_since = now
    else:
        session.pit_cell = None
        session.pit_since = None
    ball_on_lava = bool(
        session.pit_since is not None
        and now - session.pit_since >= params.pit_confirm_seconds
    )
    score = len(session.touched_cells) * params.points_per_tile
    return HexFallTickResult(
        hardware_updates=updates,
        tiles_touched=len(session.touched_cells),
        score=score,
        ball_on_lava=ball_on_lava,
        survived=remaining <= 0 and not ball_on_lava,
        elapsed_seconds=elapsed,
        remaining_seconds=remaining,
        ball_cell_heating=False,
    )


def params_from_dict(raw: dict[str, Any], seed: int = 1) -> HexFallParams:
    return HexFallParams(
        survival_seconds=float(raw.get("survivalSeconds", 45)),
        pit_confirm_seconds=float(raw.get("pitConfirmSeconds", 0.5)),
        collapse_every_seconds=float(raw.get("collapseEverySeconds", 3)),
        collapse_count=int(raw.get("collapseCount", 1)),
        collapse_warn_seconds=float(raw.get("collapseWarnSeconds", 1)),
        points_per_tile=int(raw.get("pointsPerTile", 1)),
        seed=seed,
    )
