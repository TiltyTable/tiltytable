"""Registry boundary for ball-tracked dynamic arcade modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .food_frenzy import (
    FoodFrenzySession,
    params_from_dict as frenzy_params,
    start_food_frenzy,
    tick_food_frenzy,
)
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
    effect: str | None = None


def start_mode(
    mode: str,
    params: dict[str, Any],
    *,
    seed: int,
    now: float,
    cells: dict[str, dict[str, Any]],
    row_col: dict[str, tuple[int, int]],
    ball_cell: str | None,
) -> HexFallSession | TargetHuntSession | FoodFrenzySession:
    if mode == "hex_fall":
        return start_hex_fall(hex_params(params, seed), now, cells)
    if mode == "target_hunt":
        start = ball_cell or next(
            (key for key, cell in cells.items() if int(cell.get("value", 0)) == 0),
            "A1",
        )
        return start_target_hunt(hunt_params(params, seed), cells, row_col, start, now)
    if mode == "food_frenzy":
        return start_food_frenzy(
            frenzy_params(params, seed),
            cells,
            row_col,
            ball_cell,
            now,
        )
    raise ValueError(f"unsupported runtime mode: {mode}")


def tick_mode(
    mode: str,
    session: HexFallSession | TargetHuntSession | FoodFrenzySession,
    params: dict[str, Any],
    *,
    seed: int,
    ball_cell: str | None,
    now: float,
    row_col: dict[str, tuple[int, int]],
    tracking_confidence: float | None = None,
    observation_frame: int | None = None,
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
                "tilesTouched": result.tiles_touched,
                "heating": result.ball_cell_heating,
            },
            won=result.survived,
            lost=result.ball_on_lava,
            score=result.score,
        )
    if mode == "target_hunt":
        assert isinstance(session, TargetHuntSession)
        result = tick_target_hunt(
            session,
            ball_cell,
            now,
            observation_frame=observation_frame,
            tracking_confidence=tracking_confidence,
        )
        return ModeTick(
            hardware_updates=result.hardware_updates,
            public_state={
                "targetCell": result.target_cell,
                "targetsReached": result.targets_reached,
            },
            lost=result.lost,
            score=result.score,
        )
    if mode == "food_frenzy":
        assert isinstance(session, FoodFrenzySession)
        result = tick_food_frenzy(
            session,
            ball_cell,
            now,
            observation_frame=observation_frame,
        )
        return ModeTick(
            hardware_updates=result.hardware_updates,
            public_state={
                "remainingSeconds": result.remaining_seconds,
                "targetCells": list(result.target_cells),
                "round": result.round_number,
                "foodsCollected": result.foods_collected,
                "celebrating": result.celebrating,
            },
            lost=result.lost,
            score=result.score,
            effect=result.effect,
        )
    raise ValueError(f"unsupported runtime mode: {mode}")
