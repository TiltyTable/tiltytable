"""Stable boundaries for post-V1 integrations.

V1 deliberately does not open the Kinect, Stewart serial port, or roller-ball
HID. V2/V3 adapters implement these protocols without changing the game engine
or cabinet UI state model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BallObservation:
    cell: str | None = None
    confidence: float = 0.0
    age_s: float | None = None
    pose_fresh: bool = False
    frame_seq: int | None = None
    processing_ms: float | None = None
    capture_to_observation_ms: float | None = None


@dataclass(frozen=True)
class TiltStatus:
    enabled: bool = False
    active: bool = False
    error: str = ""
    confirm_presses: int = 0
    back_presses: int = 0
    navigation_up: int = 0
    navigation_down: int = 0


class BallTrackingAdapter(Protocol):
    is_live: bool
    label: str

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def observation(self) -> BallObservation: ...


class TiltControlAdapter(Protocol):
    label: str

    def start(self) -> None: ...
    def set_active(self, active: bool) -> None: ...
    def status(self) -> TiltStatus: ...
    def stop(self) -> None: ...
