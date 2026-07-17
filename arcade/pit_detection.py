"""Shared delayed pit confirmation for every tracked arcade mode."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MIN_CONFIDENCE = 0.7
DEFAULT_DROPOUT_GRACE_SECONDS = 0.15


@dataclass
class PitDetector:
    cell: str | None = None
    since: float | None = None
    last_seen_at: float | None = None

    def reset(self) -> None:
        self.cell = None
        self.since = None
        self.last_seen_at = None

    def update(
        self,
        *,
        ball_cell: str | None,
        is_pit: bool,
        now: float,
        tracking_confidence: float | None,
        confirm_seconds: float,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        dropout_grace_seconds: float = DEFAULT_DROPOUT_GRACE_SECONDS,
    ) -> bool:
        confident_pit = bool(
            ball_cell
            and is_pit
            and (
                tracking_confidence is None
                or tracking_confidence >= min_confidence
            )
        )
        if confident_pit:
            if self.cell != ball_cell:
                self.cell = ball_cell
                self.since = now
            self.last_seen_at = now
        elif (
            ball_cell is None
            and self.cell is not None
            and self.last_seen_at is not None
            and now - self.last_seen_at <= dropout_grace_seconds
        ):
            pass
        else:
            self.reset()

        return bool(
            self.since is not None
            and now - self.since >= confirm_seconds
        )
