"""In-process 90 Hz roller-ball control for the arcade Stewart platform."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from stewart_exp_tune import TuningResults, TuningSession
from stewart_platform_control_common import (
    DEFAULT_SOCKET,
    StewartPlatformController,
    TrackballDevice,
    find_trackball,
)
from stewart_platform_control_position import (
    apply_position_counts,
    build_parser,
    command_or_retain_last_valid,
)

from .integrations import TiltStatus


class StewartTiltService:
    """Read the cabinet roller ball continuously and tilt only during a level."""

    label = "Stewart + roller ball"

    def __init__(
        self,
        *,
        tuning_path: Path = Path("calibration/stewart_game_tuning.json"),
        socket_path: Path = DEFAULT_SOCKET,
        device_path: Path | None = None,
        controller: object | None = None,
        trackball: object | None = None,
    ) -> None:
        self.tuning_path = Path(tuning_path)
        self.socket_path = Path(socket_path)
        self.device_path = Path(device_path) if device_path is not None else None
        self.controller = controller
        self.trackball = trackball

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._requested_active = False
        self._active = False
        self._enabled = False
        self._error = ""
        self._args = None
        self._tuning: TuningResults | None = None
        self._origin_roll = 0.0
        self._origin_pitch = 0.0
        self._desired_roll = 0.0
        self._desired_pitch = 0.0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return

        try:
            if self.controller is None or self.trackball is None:
                tuning = TuningResults.load(self.tuning_path)
                args = build_parser().parse_args([])
                args.socket = self.socket_path
                args.step_offsets = tuning.differential_trim_steps()
                device = self.device_path or find_trackball()
                if device is None:
                    raise RuntimeError("roller ball input device was not found")
                self.controller = StewartPlatformController(args)
                self.trackball = TrackballDevice(device)
                self._args = args
                self._tuning = tuning
            else:
                args = build_parser().parse_args([])
                self._args = args
                self._tuning = TuningResults()

            self.trackball.open()
            self.controller.open(arm=False, calibrate_if_needed=False)
        except Exception:
            try:
                if self.controller is not None:
                    self.controller.hold_and_close()
            finally:
                if self.trackball is not None:
                    self.trackball.close()
            raise

        with self._lock:
            self._enabled = True
            self._error = ""
        self._thread = threading.Thread(
            target=self._run,
            name="arcade-stewart-tilt",
            daemon=True,
        )
        self._thread.start()

    def set_active(self, active: bool) -> None:
        with self._lock:
            self._requested_active = bool(active)

    def status(self) -> TiltStatus:
        with self._lock:
            return TiltStatus(
                enabled=self._enabled,
                active=self._active,
                error=self._error,
            )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        try:
            if self.controller is not None:
                self.controller.hold_and_close()
        finally:
            if self.trackball is not None:
                self.trackball.close()
        with self._lock:
            self._thread = None
            self._enabled = False
            self._active = False
            self._requested_active = False

    def _run(self) -> None:
        assert self._args is not None
        assert self.controller is not None
        assert self.trackball is not None
        interval = 1.0 / float(self._args.rate_hz)
        last_update = time.monotonic()
        try:
            while not self._stop.is_set():
                timeout = max(0.0, interval - (time.monotonic() - last_update))
                self.trackball.wait(timeout)
                now = time.monotonic()
                if now - last_update < interval:
                    continue
                last_update = now
                dx, dy = self.trackball.pop()
                with self._lock:
                    requested = self._requested_active
                    active = self._active

                if requested and not active:
                    self._enter_level()
                    active = True
                elif not requested and active:
                    self.controller.hold_and_rebase()
                    with self._lock:
                        self._active = False
                    continue

                if not active:
                    continue

                if abs(dx) > self._args.deadband or abs(dy) > self._args.deadband:
                    self._desired_roll, self._desired_pitch = apply_position_counts(
                        self._desired_roll,
                        self._desired_pitch,
                        dx,
                        dy,
                        degrees_per_count=self._args.degrees_per_count,
                        roll_sign=self._args.roll_sign,
                        pitch_sign=self._args.pitch_sign,
                        max_tilt_deg=self._args.max_tilt,
                    )

                absolute_roll, absolute_pitch, _ = command_or_retain_last_valid(
                    self.controller,
                    self._origin_roll + self._desired_roll,
                    self._origin_pitch + self._desired_pitch,
                )
                self._desired_roll = absolute_roll - self._origin_roll
                self._desired_pitch = absolute_pitch - self._origin_pitch
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
                self._active = False
                self._requested_active = False
            try:
                self.controller.hold_and_rebase()
            except Exception:
                pass

    def _enter_level(self) -> None:
        assert self.controller is not None
        assert self._tuning is not None
        self._origin_roll, self._origin_pitch = self._tuning.game_origin()
        if self._tuning.level_anchor_steps is not None:
            session = TuningSession(
                self.controller.link,
                self._tuning,
                self.tuning_path,
            )
            session.current = self.controller.current
            session.level()
            self.controller.current = session.current
            self.controller.armed = True
        else:
            self.controller.move_to(self._origin_roll, self._origin_pitch)
        self._desired_roll = 0.0
        self._desired_pitch = 0.0
        with self._lock:
            self._active = True
