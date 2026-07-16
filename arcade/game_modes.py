"""Registry boundary for ball-tracked dynamic arcade modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .hex_fall import HexFallSession, params_from_dict as hex_params, start_hex_fall, tick_hex_fall
from .target_hunt import (
    TargetHuntSession,
    params_from_dict as hunt_params,
    start_target_hunt,
    tick_target_hunt,
)


@dataclass(frozen=True)
class ModeTick:
    hardware_updates: list[dict[str, Any]]
    public_state: dict[str, Any]
    won: bool = False
    lost: bool = False
    score: int = 0


def start_mode(
    mode: str,
    params: dict[str, Any],
    *,
    seed: int,
    now: float,
    cells: dict[str, dict[str, Any]],
    row_col: dict[str, tuple[int, int]],
    ball_cell: str | None,
) -> HexFallSession | TargetHuntSession:
    if mode == "hex_fall":
        return start_hex_fall(hex_params(params, seed), now)
    if mode == "target_hunt":
        start = ball_cell or next(
            (key for key, cell in cells.items() if int(cell.get("value", 0)) == 0),
            "A1",
        )
        return start_target_hunt(hunt_params(params, seed), cells, row_col, start, now)
    raise ValueError(f"unsupported runtime mode: {mode}")


def tick_mode(
    mode: str,
    session: HexFallSession | TargetHuntSession,
    params: dict[str, Any],
    *,
    seed: int,
    ball_cell: str | None,
    now: float,
    row_col: dict[str, tuple[int, int]],
    tracking_confidence: float | None = None,
) -> ModeTick:
    if mode == "hex_fall":
        assert isinstance(session, HexFallSession)
        result = tick_hex_fall(
            session,
            hex_params(params, seed),
            ball_cell,
            now,
            row_col,
            tracking_confidence,
        )
        return ModeTick(
            hardware_updates=result.hardware_updates,
            public_state={
                "remainingSeconds": result.remaining_seconds,
                "tilesVisited": result.visited_count,
                "heating": result.ball_cell_heating,
            },
            won=result.survived,
            lost=result.ball_on_lava,
            score=result.visited_count,
        )
    if mode == "target_hunt":
        assert isinstance(session, TargetHuntSession)
        result = tick_target_hunt(session, ball_cell, now)
        return ModeTick(
            hardware_updates=result.hardware_updates,
            public_state={
                "remainingSeconds": result.remaining_seconds,
                "targetCell": result.target_cell,
                "targetsReached": result.targets_reached,
            },
            lost=result.lost,
            score=result.score,
        )
    raise ValueError(f"unsupported runtime mode: {mode}")
