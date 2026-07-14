"""Ball position adapters for arcade survival mode (V2 Kinect + dev fallbacks)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any


def row_col_to_cell_key(row: int, col: int) -> str:
    if not (0 <= row < 12 and 0 <= col < 12):
        raise ValueError(f"cell out of range: ({row}, {col})")
    return f"{chr(ord('A') + col)}{row + 1}"


class ManualBallAdapter:
    """Dev / keyboard override — operator sets the virtual ball cell."""

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

    def current_cell(self) -> str | None:
        with self._lock:
            return self._cell

    def tracking_confidence(self) -> float:
        return 1.0 if self.current_cell() else 0.0


class HttpKinectBallAdapter:
    """Poll Kinect web control ``/api/state`` for ball grid cell."""

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
        self._poll()
        with self._lock:
            return self._cell

    def tracking_confidence(self) -> float:
        self._poll()
        with self._lock:
            return self._confidence

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
