"""Touch-triggered survival lava — per-cell independent state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LAVA_COLOR = "#FF0000"
# Palette ``floor`` hex — legacy map #567DBB nearest-neighbors to ``points`` (blue) on LEDs.
FLOOR_COLOR = "#C8D0D8"
# Ochre tint for stepped-on tiles (not cyan — that reads as start / points).
VISITED_COLOR = "#F49400"
WARN_OFF_COLOR = "#000000"
DEFAULT_SETTLE_SECONDS = 0.0
DEFAULT_PIT_CONFIRM_SECONDS = 0.5
DEFAULT_MIN_PIT_CONFIDENCE = 0.7
# Brief Kinect None gaps while the ball is still on lava do not reset pit dwell.
PIT_DROPOUT_GRACE_SECONDS = 0.15
# Keep the warning animation at a readable cadence even though ball-cell
# selection now runs on every camera frame.
WARN_BLINK_INTERVAL_SECONDS = 0.12

PHASE_NEUTRAL = "neutral"
PHASE_TOUCHED = "touched_yellow"
PHASE_WARNING = "warning"
PHASE_SUNK = "sunk"


@dataclass(frozen=True)
class SurvivalParams:
    survival_seconds: float
    dwell_seconds: float  # arm delay after touch before warning blink
    warn_seconds: float
    points_per_tile: int
    floor_color: str = FLOOR_COLOR
    settle_seconds: float = DEFAULT_SETTLE_SECONDS
    pit_confirm_seconds: float = DEFAULT_PIT_CONFIRM_SECONDS
    min_pit_confidence: float = DEFAULT_MIN_PIT_CONFIDENCE


@dataclass
class CellSurvivalState:
    phase: str = PHASE_NEUTRAL
    touched_at: float | None = None
    warning_started_at: float | None = None
    warn_blink_on: bool = False
    warn_blink_index: int = -1
    sunk_at: float | None = None


@dataclass
class SurvivalLavaSession:
    params: SurvivalParams
    cells: dict[str, CellSurvivalState] = field(default_factory=dict)
    visited: set[str] = field(default_factory=set)
    current_ball_cell: str | None = None
    dwell_cell: str | None = None
    _pending_cell: str | None = None
    _pending_since: float | None = None
    _pit_cell: str | None = None
    _pit_since: float | None = None
    _pit_last_qualify: float | None = None
    started_at: float = 0.0

    def reset(self, started_at: float) -> None:
        self.cells.clear()
        self.visited.clear()
        self.current_ball_cell = None
        self.dwell_cell = None
        self._pending_cell = None
        self._pending_since = None
        self._pit_cell = None
        self._pit_since = None
        self._pit_last_qualify = None
        self.started_at = started_at


@dataclass
class SurvivalTickResult:
    hardware_updates: list[dict[str, Any]]
    visited_count: int
    ball_on_lava: bool
    survived: bool
    elapsed_seconds: float
    remaining_seconds: float
    ball_cell_heating: bool = False


def _cell_state(session: SurvivalLavaSession, key: str) -> CellSurvivalState:
    if key not in session.cells:
        session.cells[key] = CellSurvivalState()
    return session.cells[key]


def _entry(
    key: str,
    row: int,
    col: int,
    value: int,
    color: str,
    *,
    leds_only: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "row": row,
        "col": col,
        "value": value,
        "color": color,
        "rgb": (0, 0, 0),
        "leds_only": leds_only,
    }


def _update_dwell_cell(
    session: SurvivalLavaSession,
    raw_cell: str | None,
    now: float,
    row_col_for_key: dict[str, tuple[int, int]],
) -> tuple[str | None, bool]:
    """Require the same Kinect cell briefly before arming touch timers."""
    if raw_cell is not None and raw_cell not in row_col_for_key:
        raw_cell = None

    prev_dwell = session.dwell_cell
    settle = session.params.settle_seconds

    if raw_cell is None:
        return session.dwell_cell, False

    if settle <= 0:
        if raw_cell != session.dwell_cell:
            session.dwell_cell = raw_cell
            session.current_ball_cell = raw_cell
            session._pending_cell = raw_cell
            session._pending_since = now
            return session.dwell_cell, prev_dwell != raw_cell
        return session.dwell_cell, False

    if raw_cell == session.dwell_cell:
        session._pending_cell = raw_cell
        return session.dwell_cell, False

    if raw_cell == session._pending_cell:
        if (
            session._pending_since is not None
            and now - session._pending_since >= settle
        ):
            session.dwell_cell = raw_cell
            session.current_ball_cell = raw_cell
            return session.dwell_cell, prev_dwell != raw_cell
        return session.dwell_cell, False

    session._pending_cell = raw_cell
    session._pending_since = now
    return session.dwell_cell, False


def _ball_on_sunk_cell(
    session: SurvivalLavaSession,
    ball_cell: str | None,
    row_col_for_key: dict[str, tuple[int, int]],
) -> bool:
    if not ball_cell or ball_cell not in row_col_for_key:
        return False
    state = session.cells.get(ball_cell)
    return state is not None and state.phase == PHASE_SUNK


def _pit_confidence_ok(
    tracking_confidence: float | None,
    min_confidence: float,
) -> bool:
    if tracking_confidence is None:
        return True
    return tracking_confidence >= min_confidence


def _reset_pit_dwell(session: SurvivalLavaSession) -> None:
    session._pit_cell = None
    session._pit_since = None
    session._pit_last_qualify = None


def _update_pit_confirm(
    session: SurvivalLavaSession,
    ball_cell: str | None,
    now: float,
    row_col_for_key: dict[str, tuple[int, int]],
    tracking_confidence: float | None,
) -> bool:
    """Require sustained dwell on a sunk cell before confirming pit fall."""
    params = session.params
    qualifies = (
        _ball_on_sunk_cell(session, ball_cell, row_col_for_key)
        and _pit_confidence_ok(tracking_confidence, params.min_pit_confidence)
    )

    if qualifies:
        assert ball_cell is not None
        if session._pit_cell != ball_cell:
            session._pit_cell = ball_cell
            cell = session.cells.get(ball_cell)
            session._pit_since = (
                cell.sunk_at if cell is not None and cell.sunk_at is not None else now
            )
        session._pit_last_qualify = now
        if (
            session._pit_since is not None
            and now - session._pit_since >= params.pit_confirm_seconds
        ):
            return True
        return False

    if session._pit_cell is None:
        return False

    if ball_cell is not None and ball_cell != session._pit_cell:
        _reset_pit_dwell(session)
        return False

    if ball_cell is None and session._pit_last_qualify is not None:
        if now - session._pit_last_qualify <= PIT_DROPOUT_GRACE_SECONDS:
            if (
                session._pit_since is not None
                and now - session._pit_since >= params.pit_confirm_seconds
            ):
                return True
            return False

    _reset_pit_dwell(session)
    return False


def _touch_cell(
    session: SurvivalLavaSession,
    key: str,
    now: float,
    row_col_for_key: dict[str, tuple[int, int]],
    updates: list[dict[str, Any]],
) -> None:
    state = _cell_state(session, key)
    if state.phase != PHASE_NEUTRAL:
        return
    state.phase = PHASE_TOUCHED
    state.touched_at = now
    session.visited.add(key)
    row, col = row_col_for_key[key]
    # The tile is already physically flat. Re-pulsing its servo here adds
    # roughly 0.75s of avoidable board-select/settle latency to the yellow LED.
    updates.append(_entry(key, row, col, 0, VISITED_COLOR, leds_only=True))


def _advance_cell(
    session: SurvivalLavaSession,
    key: str,
    now: float,
    params: SurvivalParams,
    row_col_for_key: dict[str, tuple[int, int]],
    updates: list[dict[str, Any]],
) -> None:
    """Advance one cell's timer chain."""
    cell = session.cells.get(key)
    if cell is None or key not in row_col_for_key:
        return
    row, col = row_col_for_key[key]

    if cell.phase == PHASE_TOUCHED and cell.touched_at is not None:
        if now - cell.touched_at < params.dwell_seconds:
            return
        cell.phase = PHASE_WARNING
        cell.warning_started_at = cell.touched_at + params.dwell_seconds
        cell.warn_blink_on = True
        cell.warn_blink_index = -1

    if cell.phase != PHASE_WARNING or cell.warning_started_at is None:
        return

    if now - cell.warning_started_at >= params.warn_seconds:
        cell.phase = PHASE_SUNK
        cell.sunk_at = now
        updates.append(_entry(key, row, col, -1, LAVA_COLOR))
        if session.dwell_cell == key:
            session._pit_cell = key
            session._pit_since = now
        return

    blink_index = int(
        (now - cell.warning_started_at) / WARN_BLINK_INTERVAL_SECONDS
    )
    if blink_index == cell.warn_blink_index:
        return
    cell.warn_blink_index = blink_index
    cell.warn_blink_on = blink_index % 2 == 0
    color = LAVA_COLOR if cell.warn_blink_on else WARN_OFF_COLOR
    updates.append(_entry(key, row, col, 0, color, leds_only=True))


def tick_survival_lava(
    session: SurvivalLavaSession,
    ball_cell: str | None,
    now: float,
    row_col_for_key: dict[str, tuple[int, int]],
    tracking_confidence: float | None = None,
) -> SurvivalTickResult:
    """Advance one survival tick. Returns hardware LED/servo updates."""
    params = session.params
    elapsed = max(0.0, now - session.started_at)
    remaining = max(0.0, params.survival_seconds - elapsed)
    updates: list[dict[str, Any]] = []
    ball_on_lava = False

    dwell_cell, promoted = _update_dwell_cell(session, ball_cell, now, row_col_for_key)
    if promoted and dwell_cell and dwell_cell in row_col_for_key:
        _touch_cell(session, dwell_cell, now, row_col_for_key, updates)

    for key in list(session.cells.keys()):
        _advance_cell(session, key, now, params, row_col_for_key, updates)

    ball_on_lava = _update_pit_confirm(
        session,
        ball_cell,
        now,
        row_col_for_key,
        tracking_confidence,
    )

    heating = any(cell.phase == PHASE_WARNING for cell in session.cells.values())

    survived = remaining <= 0.0 and not ball_on_lava
    return SurvivalTickResult(
        hardware_updates=updates,
        visited_count=len(session.visited),
        ball_on_lava=ball_on_lava,
        survived=survived,
        elapsed_seconds=elapsed,
        remaining_seconds=remaining,
        ball_cell_heating=heating,
    )


def survival_score(
    visited_count: int,
    remaining_seconds: int,
    restarts: int,
    points_per_tile: int,
) -> int:
    del remaining_seconds
    return max(0, visited_count * points_per_tile - restarts * 100)
