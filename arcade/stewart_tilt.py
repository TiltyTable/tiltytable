"""In-process 90 Hz roller-ball control for the arcade Stewart platform."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

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

DEFAULT_ARCADE_CONFIG = Path(__file__).with_name("config.json")


def load_navigation_counts_per_step(
    config_path: Path = DEFAULT_ARCADE_CONFIG,
) -> int:
    with Path(config_path).open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    value = config.get("trackball", {}).get("navigation_counts_per_step")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            "arcade trackball.navigation_counts_per_step must be a positive integer"
        )
    return value


def load_button_debounce_ms(
    config_path: Path = DEFAULT_ARCADE_CONFIG,
) -> int:
    with Path(config_path).open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    value = config.get("trackball", {}).get("button_debounce_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            "arcade trackball.button_debounce_ms must be a non-negative integer"
        )
    return value


def debounce_button_presses(
    presses: int,
    now: float,
    last_accepted_at: float | None,
    delay_s: float,
) -> tuple[int, float | None]:
    """Accept at most one press inside a button's debounce window."""
    if presses <= 0:
        return 0, last_accepted_at
    if delay_s <= 0.0:
        return presses, now
    if last_accepted_at is not None and now - last_accepted_at < delay_s:
        return 0, last_accepted_at
    return 1, now


def navigation_steps(
    delta_y: int,
    remainder: int,
    counts_per_step: int,
) -> tuple[int, int, int]:
    """Convert vertical trackball counts into `(up, down, remainder)` steps."""
    total = remainder + delta_y
    signed_steps = int(total / counts_per_step)
    remainder = total - signed_steps * counts_per_step
    return max(0, -signed_steps), max(0, signed_steps), remainder


class StewartTiltService:
    """Read the cabinet roller ball continuously and tilt only during a level."""

    label = "Stewart + roller ball"

    def __init__(
        self,
        *,
        socket_path: Path = DEFAULT_SOCKET,
        device_path: Path | None = None,
        arcade_config_path: Path = DEFAULT_ARCADE_CONFIG,
        controller: object | None = None,
        trackball: object | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.device_path = Path(device_path) if device_path is not None else None
        self.navigation_counts_per_step = load_navigation_counts_per_step(
            arcade_config_path
        )
        self.button_debounce_s = load_button_debounce_ms(arcade_config_path) / 1000.0
        self.controller = controller
        self.trackball = trackball

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._requested_active = False
        self._active = False
        self._enabled = False
        self._error = ""
        self._confirm_presses = 0
        self._back_presses = 0
        self._navigation_up = 0
        self._navigation_down = 0
        self._navigation_remainder = 0
        self._last_confirm_at: float | None = None
        self._last_back_at: float | None = None
        self._args = None
        self._desired_roll = 0.0
        self._desired_pitch = 0.0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return

        try:
            if self.controller is None or self.trackball is None:
                args = build_parser().parse_args([])
                args.socket = self.socket_path
                device = self.device_path or find_trackball()
                if device is None:
                    raise RuntimeError("roller ball input device was not found")
                self.controller = StewartPlatformController(args)
                self.trackball = TrackballDevice(device)
                self._args = args
            else:
                args = build_parser().parse_args([])
                self._args = args

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
                confirm_presses=self._confirm_presses,
                back_presses=self._back_presses,
                navigation_up=self._navigation_up,
                navigation_down=self._navigation_down,
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
                pop_buttons = getattr(self.trackball, "pop_buttons", None)
                left_presses, right_presses = (
                    pop_buttons() if pop_buttons is not None else (0, 0)
                )
                left_presses, self._last_back_at = debounce_button_presses(
                    left_presses,
                    now,
                    self._last_back_at,
                    self.button_debounce_s,
                )
                right_presses, self._last_confirm_at = debounce_button_presses(
                    right_presses,
                    now,
                    self._last_confirm_at,
                    self.button_debounce_s,
                )
                with self._lock:
                    self._back_presses += left_presses
                    self._confirm_presses += right_presses
                    requested = self._requested_active
                    active = self._active

                if not requested and not active:
                    up, down, self._navigation_remainder = navigation_steps(
                        -dx,
                        self._navigation_remainder,
                        self.navigation_counts_per_step,
                    )
                    if up or down:
                        with self._lock:
                            self._navigation_up += up
                            self._navigation_down += down
                else:
                    self._navigation_remainder = 0

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
                    self._desired_roll,
                    self._desired_pitch,
                )
                self._desired_roll = absolute_roll
                self._desired_pitch = absolute_pitch
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
        if self.controller.current is None:
            raise RuntimeError("Stewart controller has no current pose")
        self.controller.move_to(0.0, 0.0)
        self._desired_roll = 0.0
        self._desired_pitch = 0.0
        with self._lock:
            self._active = True
