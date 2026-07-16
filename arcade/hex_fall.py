"""Hex-A-Fall: touched floor telegraphs, then disappears permanently."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from .survival_lava import (
    FLOOR_COLOR,
    LAVA_COLOR,
    PHASE_SUNK,
    CellSurvivalState,
    SurvivalLavaSession,
    SurvivalParams,
    SurvivalTickResult,
    tick_survival_lava,
)


@dataclass(frozen=True)
class HexFallParams:
    survival_seconds: float = 45.0
    touch_grace_seconds: float = 0.35
    warn_seconds: float = 1.25
    pit_confirm_seconds: float = 0.5
    collapse_every_seconds: float = 0.0
    collapse_count: int = 0
    seed: int = 1


@dataclass
class HexFallSession:
    lava: SurvivalLavaSession
    rng: random.Random
    next_collapse_at: float | None


def start_hex_fall(params: HexFallParams, now: float) -> HexFallSession:
    lava = SurvivalLavaSession(
        params=SurvivalParams(
            survival_seconds=params.survival_seconds,
            dwell_seconds=params.touch_grace_seconds,
            warn_seconds=params.warn_seconds,
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
    return HexFallSession(lava=lava, rng=random.Random(params.seed), next_collapse_at=next_collapse)


def tick_hex_fall(
    session: HexFallSession,
    params: HexFallParams,
    ball_cell: str | None,
    now: float,
    row_col_for_key: dict[str, tuple[int, int]],
    tracking_confidence: float | None = None,
) -> SurvivalTickResult:
    result = tick_survival_lava(
        session.lava,
        ball_cell,
        now,
        row_col_for_key,
        tracking_confidence,
    )
    updates = list(result.hardware_updates)
    if session.next_collapse_at is not None and now >= session.next_collapse_at:
        available = [
            key
            for key in row_col_for_key
            if key != ball_cell
            and session.lava.cells.get(key, CellSurvivalState()).phase != PHASE_SUNK
        ]
        session.rng.shuffle(available)
        for key in available[: params.collapse_count]:
            session.lava.cells[key] = CellSurvivalState(phase=PHASE_SUNK, sunk_at=now)
            row, col = row_col_for_key[key]
            updates.append(
                {"key": key, "row": row, "col": col, "value": -1, "color": LAVA_COLOR, "rgb": (0, 0, 0)}
            )
        session.next_collapse_at += params.collapse_every_seconds
    return SurvivalTickResult(
        hardware_updates=updates,
        visited_count=result.visited_count,
        ball_on_lava=result.ball_on_lava,
        survived=result.survived,
        elapsed_seconds=result.elapsed_seconds,
        remaining_seconds=result.remaining_seconds,
        ball_cell_heating=result.ball_cell_heating,
    )


def params_from_dict(raw: dict[str, Any], seed: int = 1) -> HexFallParams:
    return HexFallParams(
        survival_seconds=float(raw.get("survivalSeconds", 45)),
        touch_grace_seconds=float(raw.get("touchGraceSeconds", 0.35)),
        warn_seconds=float(raw.get("warnSeconds", 1.25)),
        pit_confirm_seconds=float(raw.get("pitConfirmSeconds", 0.5)),
        collapse_every_seconds=float(raw.get("collapseEverySeconds", 0)),
        collapse_count=int(raw.get("collapseCount", 0)),
        seed=seed,
    )
