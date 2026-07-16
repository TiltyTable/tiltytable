"""Ball position adapters for arcade survival mode (V2 Kinect + dev fallbacks)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .integrations import BallObservation


def row_col_to_cell_key(row: int, col: int) -> str:
    if not (0 <= row < 12 and 0 <= col < 12):
        raise ValueError(f"cell out of range: ({row}, {col})")
    return f"{chr(ord('A') + col)}{row + 1}"


class ManualBallAdapter:
    """Dev / keyboard override — operator sets the virtual ball cell."""

    is_live = False
    label = "Simulation"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cell: str | None = None

    def start(self) -> None:
        return

    def stop(self) -> None:
        with self._lock:
            self._cell = None

    def set_cell(self, cell: str | None) -> None:
        with self._lock:
            self._cell = cell.upper() if cell else None

    def observation(self) -> BallObservation:
        with self._lock:
            cell = self._cell
        return BallObservation(
            cell=cell,
            confidence=1.0 if cell else 0.0,
            pose_fresh=bool(cell),
        )

    # Compatibility for standalone callers while the game uses observation().
    def current_cell(self) -> str | None:
        return self.observation().cell

    def tracking_confidence(self) -> float:
        return self.observation().confidence


class HttpKinectBallAdapter:
    """Poll Kinect web control ``/api/state`` for ball grid cell."""

    is_live = True
    label = "Azure Kinect"

    def __init__(self, state_url: str, timeout_s: float = 0.35) -> None:
        self.state_url = state_url.rstrip("/")
        if not self.state_url.endswith("/api/state"):
            self.state_url = f"{self.state_url}/api/state"
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._cell: str | None = None
        self._confidence = 0.0

    def start(self) -> None:
        return

    def stop(self) -> None:
        with self._lock:
            self._cell = None
            self._confidence = 0.0

    def current_cell(self) -> str | None:
        return self.observation().cell

    def tracking_confidence(self) -> float:
        return self.observation().confidence

    def observation(self) -> BallObservation:
        self._poll()
        with self._lock:
            return BallObservation(
                cell=self._cell,
                confidence=self._confidence,
                pose_fresh=self._confidence >= 0.7,
            )

    def _poll(self) -> None:
        try:
            with urllib.request.urlopen(self.state_url, timeout=self.timeout_s) as resp:
                payload: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
            return

        ball = payload.get("ball") or {}
        cell = ball.get("cell")
        detected = bool(ball.get("detected"))
        with self._lock:
            if detected and isinstance(cell, dict):
                row = cell.get("row")
                col = cell.get("col")
                if isinstance(row, int) and isinstance(col, int):
                    try:
                        self._cell = row_col_to_cell_key(row, col)
                        self._confidence = 0.9
                        return
                    except ValueError:
                        pass
            self._cell = None
            self._confidence = 0.0


class InProcessKinectBallAdapter:
    """Own the headless Kinect tracker inside the arcade process."""

    is_live = True
    label = "Azure Kinect"

    def __init__(self, config_path: Path, *, hub: Any | None = None) -> None:
        self.config_path = Path(config_path)
        self._lock = threading.Lock()
        self._hub = hub
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            hub = self._hub
            if hub is None:
                from kinect_web_control import (
                    KinectFrameHub,
                    configure_table_geometry,
                    parse_args,
                )

                args = parse_args(["--config", str(self.config_path)])
                args.color_resolution = "off"
                args.aligned_depth = False
                args.depth_engine_display = ""
                args.ball_tracking = True
                configure_table_geometry(
                    marker_height_mm=args.marker_height_mm,
                    marker_world_points=args.marker_world_points,
                    max_marker_radius_mm=args.max_marker_radius_mm,
                )
                hub = KinectFrameHub(args, headless=True)
                self._hub = hub
            hub.start()
            self._started = True

    def stop(self) -> None:
        with self._lock:
            hub = self._hub
            started = self._started
            self._started = False
        if started and hub is not None:
            hub.stop()

    def observation(self) -> BallObservation:
        with self._lock:
            hub = self._hub
            started = self._started
        if not started or hub is None:
            return BallObservation()

        state = hub.get_ball_state()
        cell_data = state.get("cell")
        if not state.get("detected") or not isinstance(cell_data, dict):
            return BallObservation(age_s=state.get("pose_age_s"))
        try:
            cell = row_col_to_cell_key(int(cell_data["row"]), int(cell_data["col"]))
        except (KeyError, TypeError, ValueError):
            return BallObservation(age_s=state.get("pose_age_s"))

        pose_fresh = bool(state.get("table_tracking")) and not bool(
            state.get("pose_stale")
        )
        return BallObservation(
            cell=cell,
            confidence=0.9 if pose_fresh else 0.4,
            age_s=state.get("pose_age_s"),
            pose_fresh=pose_fresh,
        )

    def current_cell(self) -> str | None:
        return self.observation().cell

    def tracking_confidence(self) -> float:
        return self.observation().confidence
