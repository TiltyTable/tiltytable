#!/usr/bin/env python3
"""Headless Stewart ball-balancing control app — no web frontend.

Combines closed-loop ball balancing (Kinect ball + table pose tracking, PID)
with manual tilt from a mouse or gamepad. Manual input always drives the
table; a discrete "assist" level (0/25/50/75%) blends in how strongly the
ball-balance PID correction is added on top of the manual command:

    commanded = manual_tilt + assist_level * pid_correction(ball_error)

Mouse mode: relative/velocity-style accumulate-and-clamp tilt, same pattern
as roller_ball.py. Assist level is fixed at startup via --assist (no button
to cycle it on a mouse).

Gamepad mode: the left stick maps DIRECTLY and proportionally to roll/pitch
(no smoothing/accumulation). One button recenters heave to the midpoint
between the firmware's min/max heave; another cycles the assist level.

Examples
--------
    python3 stewart_ball_control.py --input mouse --assist 0.25
    python3 stewart_ball_control.py --input gamepad --port /dev/arduino-stewart
    python3 gamepad_input.py --list-buttons   # identify your pad's button codes first
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import threading
import time
from pathlib import Path

import serial

from stewart_serial import open_stewart_serial, wait_if_reset
from ball_balancer import PIDController
from ball_tracker import BallDetector, BallTracker
from table_pose import TablePoseTracker, TABLE_LONG_SIDE_MM, TABLE_SHORT_SIDE_MM
from live_capture_viewer import DEPTH_MODES, FPS_VALUES, DEPTH_ENGINE_DISPLAY, set_display, get_depth
from gamepad_input import GamepadReader, find_gamepad_device

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from roller_ball import Stewart  # noqa: E402  (needs sys.path fixup above)


# ---------------------------------------------------------------------------
# Firmware constants (mirrored from arduino/uim5756pm_stewart/uim5756pm_stewart.ino)
# ---------------------------------------------------------------------------

MAX_ROLL_DEG = 5.0
MAX_PITCH_DEG = 5.0
MIN_HEAVE_MM = 12.0
MAX_HEAVE_MM = 30.0
CALIBRATE_HEAVE_MM = MAX_HEAVE_MM
NEUTRAL_HEAVE_MM = (MIN_HEAVE_MM + MAX_HEAVE_MM) / 2.0  # 21.0

DEFAULT_PORT = "/dev/arduino-stewart"
ASSIST_LEVELS = (0.0, 0.25, 0.5, 0.75)
POSE_EPS_DEG = 0.02
POSE_EPS_MM = 0.05

EVENT_ROOT = Path("/dev/input")
PROC_INPUT_DEVICES = Path("/proc/bus/input/devices")
EV_SYN = 0x00
EV_REL = 0x02
INPUT_EVENT = struct.Struct("llHHI")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


# ---------------------------------------------------------------------------
# Mouse device discovery (small stdlib helper, duplicated from
# capture_usb_mouse.py's pattern rather than importing that script — it has
# its own argparse main() and is meant to be run standalone).
# ---------------------------------------------------------------------------

def _parse_proc_input_devices() -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    if not PROC_INPUT_DEVICES.exists():
        return devices
    current: dict[str, str] = {}
    for line in PROC_INPUT_DEVICES.read_text(errors="replace").splitlines():
        if not line.strip():
            if current:
                devices.append(current)
                current = {}
            continue
        prefix, _, value = line.partition(":")
        value = value.strip()
        if prefix == "N":
            current["name"] = value.removeprefix('Name="').removesuffix('"')
        elif prefix == "H":
            current["handlers"] = value.removeprefix("Handlers=").strip()
    if current:
        devices.append(current)
    return devices


def find_mouse_device() -> str | None:
    by_id = EVENT_ROOT / "by-id"
    if by_id.exists():
        mouse_links = sorted(path for path in by_id.iterdir() if "mouse" in path.name.lower())
        if mouse_links:
            return str(mouse_links[0])
    for device in _parse_proc_input_devices():
        name = device.get("name", "").lower()
        if "mouse" not in name:
            continue
        for part in device.get("handlers", "").split():
            if part.startswith("event"):
                return str(EVENT_ROOT / part)
    return None


# ---------------------------------------------------------------------------
# Camera thread — ball detection + table pose tracking (simplified
# KinectFrameHub: no MJPEG/HTTP consumer, so a plain Lock + snapshot dict
# suffices instead of a Condition).
# ---------------------------------------------------------------------------

class CameraThread:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.k4a = None
        self.tracker = None
        self.table_pose = TablePoseTracker()
        self.status = "starting"
        self.error = ""

        self._ball_world = None      # (x, y, z) table-frame mm, or None
        self._ball_tracking = False
        self._table_tracking = False
        self._pose_stale = True
        self._pose_frame_counter = 0

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self.k4a is not None and self.k4a.is_running:
            self.k4a.stop()
        if self.tracker is not None:
            self.tracker.close()

    def _run(self) -> None:
        from pyk4a import (
            CalibrationType,
            ColorResolution,
            Config,
            ImageFormat,
            K4AException,
            K4ATimeoutException,
            PyK4A,
            connected_device_count,
        )
        import cv2

        args = self.args
        if args.depth_engine_display:
            set_display(args.depth_engine_display, "depth engine", quiet=not args.verbose)

        try:
            device_count = connected_device_count()
            if device_count <= args.device_id:
                self.status, self.error = "error", (
                    f"No Azure Kinect device at index {args.device_id}; found {device_count} device(s)."
                )
                return

            config = Config(
                color_resolution=ColorResolution.OFF,
                color_format=ImageFormat.COLOR_BGRA32,
                depth_mode=DEPTH_MODES[args.depth_mode],
                camera_fps=FPS_VALUES[args.fps],
                synchronized_images_only=False,
            )
            self.k4a = PyK4A(config=config, device_id=args.device_id)
            self.k4a.start()
            self.status = "running"

            mat = self.k4a.calibration.get_camera_matrix(CalibrationType.DEPTH)
            fx, fy, ppx, ppy = float(mat[0, 0]), float(mat[1, 1]), float(mat[0, 2]), float(mat[1, 2])

            detector = BallDetector.from_k4a_calibration(
                self.k4a.calibration,
                ball_radius_min_mm=args.ball_radius_min,
                ball_radius_max_mm=args.ball_radius_max,
            )
            tracker = BallTracker.from_k4a_calibration(self.k4a.calibration)
            self.tracker = tracker

            while not self.stop_event.is_set():
                try:
                    capture = self.k4a.get_capture(timeout=args.timeout_ms)
                except K4ATimeoutException:
                    self.status, self.error = "timeout", "Timed out waiting for a Kinect frame."
                    continue

                ir_frame = capture.ir
                depth_mm = get_depth(capture, aligned_depth=False)
                if ir_frame is None or depth_mm is None:
                    continue

                detection = detector.detect(ir_frame, capture.depth)
                pos, _smoothed = tracker.update(detection)

                self._pose_frame_counter += 1
                if self._pose_frame_counter >= args.pose_update_every_n_frames:
                    self._pose_frame_counter = 0
                    self.table_pose.update(
                        ir_frame, capture.depth, fx, fy, ppx, ppy,
                        marker_ir_min_counts=args.marker_ir_min_counts,
                    )

                ball_world = None
                pose_stale = True
                if pos is not None and self.table_pose.is_tracking:
                    world, stale, _age_s = self.table_pose.apply(pos)
                    ball_world = world
                    pose_stale = stale

                with self.lock:
                    self._ball_world = ball_world
                    self._ball_tracking = pos is not None
                    self._table_tracking = self.table_pose.is_tracking
                    self._pose_stale = pose_stale
                    self.status = "running"
                    self.error = ""

        except (K4AException, RuntimeError, ValueError, cv2.error) as exc:
            self.status, self.error = "error", str(exc)
        finally:
            if self.k4a is not None and self.k4a.is_running:
                self.k4a.stop()

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "ball_world": self._ball_world,
                "ball_tracking": self._ball_tracking,
                "table_tracking": self._table_tracking,
                "pose_stale": self._pose_stale,
            }


# ---------------------------------------------------------------------------
# Manual input — mouse (accumulate + EMA, roller_ball.py pattern) or gamepad
# (direct proportional stick mapping). Polled synchronously in the main loop.
# ---------------------------------------------------------------------------

class MouseManualInput:
    def __init__(self, args):
        self.args = args
        self.device = args.input_device or find_mouse_device()
        if not self.device:
            raise RuntimeError("No mouse-like event device found. Run: python3 capture_usb_mouse.py --list")
        self.fd: int | None = None
        self.roll_cmd = 0.0
        self.pitch_cmd = 0.0
        self.roll_out = 0.0
        self.pitch_out = 0.0
        self.pending_dx = 0
        self.pending_dy = 0

    @property
    def assist_level(self) -> float:
        return self.args.assist

    def open(self) -> None:
        self.fd = os.open(self.device, os.O_RDONLY | os.O_NONBLOCK)
        print(f"Mouse: {self.device}")

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def poll(self, dt: float) -> None:
        assert self.fd is not None
        try:
            data = os.read(self.fd, INPUT_EVENT.size * 64)
        except BlockingIOError:
            data = b""
        for offset in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
            _sec, _usec, etype, code, value = INPUT_EVENT.unpack_from(data, offset)
            if etype != EV_REL:
                continue
            delta = signed32(value)
            if code == 0x00:  # REL_X
                self.pending_dx += delta
            elif code == 0x01:  # REL_Y
                self.pending_dy += delta

        args = self.args
        if abs(self.pending_dx) > args.deadband or abs(self.pending_dy) > args.deadband:
            self.pitch_cmd += self.pending_dx * args.scale * args.pitch_sign
            self.roll_cmd += -self.pending_dy * args.scale * args.roll_sign
        self.pending_dx = 0
        self.pending_dy = 0

        self.roll_cmd = clamp(self.roll_cmd, -args.max_tilt, args.max_tilt)
        self.pitch_cmd = clamp(self.pitch_cmd, -args.max_tilt, args.max_tilt)
        self.roll_out += (self.roll_cmd - self.roll_out) * args.smooth
        self.pitch_out += (self.pitch_cmd - self.pitch_out) * args.smooth

    @property
    def roll_deg(self) -> float:
        return self.roll_out

    @property
    def pitch_deg(self) -> float:
        return self.pitch_out

    def consume_heave_reset(self) -> bool:
        return False  # no reset control available on a mouse


class GamepadManualInput:
    def __init__(self, args):
        self.args = args
        device = args.input_device or find_gamepad_device()
        if not device:
            raise RuntimeError("No gamepad-like event device found. Run: python3 gamepad_input.py --list")
        self.reader = GamepadReader(device)
        self._assist_index = 0
        self._heave_reset_pending = False
        self._roll_deg = 0.0
        self._pitch_deg = 0.0

    @property
    def assist_level(self) -> float:
        return ASSIST_LEVELS[self._assist_index]

    def open(self) -> None:
        self.reader.open()
        print(f"Gamepad: {self.reader.device_path}")

    def close(self) -> None:
        self.reader.close()

    def poll(self, dt: float) -> None:
        args = self.args
        try:
            self.reader.poll()
        except OSError as exc:
            raise RuntimeError(f"gamepad read failed (unplugged?): {exc}") from exc

        stick_x = self.reader.axis_value.get(args.axis_x_code, 0.0)
        stick_y = self.reader.axis_value.get(args.axis_y_code, 0.0)
        self._pitch_deg = stick_x * args.max_tilt * args.pitch_sign
        self._roll_deg = -stick_y * args.max_tilt * args.roll_sign

        for code in self.reader.consume_edges():
            if code == args.reset_button_code:
                self._heave_reset_pending = True
            elif code == args.assist_button_code:
                self._assist_index = (self._assist_index + 1) % len(ASSIST_LEVELS)

    @property
    def roll_deg(self) -> float:
        return self._roll_deg

    @property
    def pitch_deg(self) -> float:
        return self._pitch_deg

    def consume_heave_reset(self) -> bool:
        pending = self._heave_reset_pending
        self._heave_reset_pending = False
        return pending


def build_manual_input(args):
    if args.input == "mouse":
        return MouseManualInput(args)
    return GamepadManualInput(args)


# ---------------------------------------------------------------------------
# Main control loop
# ---------------------------------------------------------------------------

def status_line(ball_ok, table_ok, assist, cmd_roll, cmd_pitch, heave_cmd, err) -> str:
    ball = "TRK" if ball_ok else "LOST"
    table = "TRK" if table_ok else "LOST"
    line = (
        f"ball={ball} table={table} assist={assist * 100:.0f}%  "
        f"roll={cmd_roll:+6.2f}° pitch={cmd_pitch:+6.2f}° heave={heave_cmd:5.1f}mm"
    )
    if err is not None:
        line += f"  err=({err[0]:+.0f},{err[1]:+.0f})mm"
    return line


def run(args) -> int:
    camera = CameraThread(args)
    stewart = Stewart(args.port, args.baud, verbose=args.verbose)
    manual_input = build_manual_input(args)

    setpoint = (args.setpoint_x, args.setpoint_y)
    pid_roll = PIDController(
        kp=args.kp_roll, ki=args.ki_roll, kd=args.kd_roll,
        output_limit=MAX_ROLL_DEG, integral_limit=MAX_ROLL_DEG * 20,
    )
    pid_pitch = PIDController(
        kp=args.kp_pitch, ki=args.ki_pitch, kd=args.kd_pitch,
        output_limit=MAX_PITCH_DEG, integral_limit=MAX_PITCH_DEG * 20,
    )

    heave_cmd = args.heave
    interval = 1.0 / args.rate_hz
    last_sent_roll = last_sent_pitch = last_sent_heave = None
    last_tick = time.monotonic()
    last_status = 0.0

    try:
        camera.start()

        if not args.dry_run:
            stewart.open()
            stewart.bring_up(args.heave)
        else:
            print(f"[dry-run] would calibrate/enable/bring up to heave={args.heave:g}mm")

        manual_input.open()
        print("Running. Ctrl-C to stop.\n")

        while True:
            now = time.monotonic()
            sleep_s = max(0.0, last_tick + interval - now)
            if sleep_s > 0:
                time.sleep(sleep_s)
            now = time.monotonic()
            dt = max(now - last_tick, 1e-3)
            last_tick = now

            manual_input.poll(dt)

            snap = camera.snapshot()
            ball_ok = snap["ball_tracking"] and snap["table_tracking"] and not snap["pose_stale"]
            err = None
            if ball_ok:
                bx, by, _bz = snap["ball_world"]
                err = (bx - setpoint[0], by - setpoint[1])
                corr_roll = args.roll_sign * pid_roll.update(err[0], dt)
                corr_pitch = args.pitch_sign * pid_pitch.update(err[1], dt)
            else:
                pid_roll.reset()
                pid_pitch.reset()
                corr_roll = corr_pitch = 0.0

            assist = manual_input.assist_level
            cmd_roll = clamp(manual_input.roll_deg + assist * corr_roll, -MAX_ROLL_DEG, MAX_ROLL_DEG)
            cmd_pitch = clamp(manual_input.pitch_deg + assist * corr_pitch, -MAX_PITCH_DEG, MAX_PITCH_DEG)

            if manual_input.consume_heave_reset():
                heave_cmd = NEUTRAL_HEAVE_MM

            moved = (
                last_sent_roll is None
                or abs(cmd_roll - last_sent_roll) >= POSE_EPS_DEG
                or abs(cmd_pitch - last_sent_pitch) >= POSE_EPS_DEG
                or abs(heave_cmd - last_sent_heave) >= POSE_EPS_MM
            )
            if moved:
                if args.dry_run:
                    print(f"\n[dry-run] pose {cmd_roll:.3f} {cmd_pitch:.3f} {heave_cmd:.3f}")
                else:
                    stewart.pose(cmd_roll, cmd_pitch, heave_cmd)
                last_sent_roll, last_sent_pitch, last_sent_heave = cmd_roll, cmd_pitch, heave_cmd

            if now - last_status >= args.status_interval:
                print(f"\r{status_line(ball_ok, snap['table_tracking'], assist, cmd_roll, cmd_pitch, heave_cmd, err)}   ",
                      end="", flush=True)
                last_status = now

    except KeyboardInterrupt:
        print("\n\nStopping...")
    except (RuntimeError, serial.SerialException) as exc:
        print(f"\n\ncontrol error: {exc}", file=sys.stderr)
    finally:
        print("\nLeveling and disabling...")
        if not args.dry_run:
            try:
                stewart.pose(0.0, 0.0, NEUTRAL_HEAVE_MM)
                time.sleep(0.3)
            except Exception:
                pass
            stewart.disable()
            stewart.close()
        manual_input.close()
        camera.stop()
        print("Done.")
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless Stewart ball-balancing control (Kinect + mouse/gamepad, no web frontend).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    stewart_grp = parser.add_argument_group("Stewart hardware")
    stewart_grp.add_argument("--port", default=DEFAULT_PORT)
    stewart_grp.add_argument("--baud", type=int, default=115200)
    stewart_grp.add_argument("--heave", type=float, default=NEUTRAL_HEAVE_MM,
                              help=f"Operating heave mm, must be in [{MIN_HEAVE_MM:g}, {MAX_HEAVE_MM:g}]")
    stewart_grp.add_argument("--max-tilt", type=float, default=MAX_ROLL_DEG,
                              help="Clamp ceiling (deg) applied to manual accumulation/mapping before PID blend")
    stewart_grp.add_argument("--rate-hz", type=float, default=30.0, help="Control loop rate")
    stewart_grp.add_argument("--yes", "-y", action="store_true", help="Skip cranks-up Enter confirmation")
    stewart_grp.add_argument("--verbose", "-v", action="store_true", help="Print raw Arduino replies")

    kinect_grp = parser.add_argument_group("Kinect / camera")
    kinect_grp.add_argument("--device-id", type=int, default=0)
    kinect_grp.add_argument("--depth-mode", choices=sorted(DEPTH_MODES), default="nfov_unbinned")
    kinect_grp.add_argument("--fps", choices=sorted(FPS_VALUES, key=int), default="30")
    kinect_grp.add_argument("--timeout-ms", type=int, default=1000)
    kinect_grp.add_argument("--depth-engine-display", default=DEPTH_ENGINE_DISPLAY,
                             help="DISPLAY value the Kinect depth engine needs, even over SSH")
    kinect_grp.add_argument("--ball-radius-min", type=float, default=20.0, metavar="MM")
    kinect_grp.add_argument("--ball-radius-max", type=float, default=40.0, metavar="MM")
    kinect_grp.add_argument("--pose-update-every-n-frames", type=int, default=3, metavar="N")
    kinect_grp.add_argument("--marker-ir-min-counts", type=float, default=1000.0, metavar="COUNTS")

    pid_grp = parser.add_argument_group("Ball-balance PID / setpoint (table-frame mm)")
    pid_grp.add_argument("--setpoint-x", type=float, default=TABLE_LONG_SIDE_MM / 2.0)
    pid_grp.add_argument("--setpoint-y", type=float, default=TABLE_SHORT_SIDE_MM / 2.0)
    pid_grp.add_argument("--kp-roll", type=float, default=0.04)
    pid_grp.add_argument("--ki-roll", type=float, default=0.0)
    pid_grp.add_argument("--kd-roll", type=float, default=0.2)
    pid_grp.add_argument("--kp-pitch", type=float, default=0.04)
    pid_grp.add_argument("--ki-pitch", type=float, default=0.0)
    pid_grp.add_argument("--kd-pitch", type=float, default=0.2)
    pid_grp.add_argument("--roll-sign", type=float, choices=(-1.0, 1.0), default=1.0)
    pid_grp.add_argument("--pitch-sign", type=float, choices=(-1.0, 1.0), default=1.0)

    input_grp = parser.add_argument_group("Manual input")
    input_grp.add_argument("--input", choices=("mouse", "gamepad"), required=True)
    input_grp.add_argument("--input-device", default=None, help="Override auto-detected /dev/input/eventN")
    input_grp.add_argument("--assist", type=float, default=0.0,
                            help="Assist level for mouse mode (0-1); gamepad mode cycles ASSIST_LEVELS instead")
    input_grp.add_argument("--scale", type=float, default=0.04, help="[mouse] deg per HID count")
    input_grp.add_argument("--smooth", type=float, default=0.35, help="[mouse] EMA blend factor (0-1)")
    input_grp.add_argument("--deadband", type=int, default=1, help="[mouse] ignore accumulated counts at/below this")
    input_grp.add_argument("--axis-x-code", type=lambda s: int(s, 0), default=0x00, help="[gamepad] ABS code -> pitch")
    input_grp.add_argument("--axis-y-code", type=lambda s: int(s, 0), default=0x01, help="[gamepad] ABS code -> roll")
    input_grp.add_argument("--reset-button-code", type=lambda s: int(s, 0), default=0x13c,
                            help="[gamepad] BTN code that recenters heave (default BTN_MODE)")
    input_grp.add_argument("--assist-button-code", type=lambda s: int(s, 0), default=0x13b,
                            help="[gamepad] BTN code that cycles assist level (default BTN_START)")

    misc = parser.add_argument_group("Misc")
    misc.add_argument("--dry-run", action="store_true", help="Skip opening Stewart serial; log poses to stdout")
    misc.add_argument("--status-interval", type=float, default=0.25)

    args = parser.parse_args(argv)

    if not MIN_HEAVE_MM <= args.heave <= MAX_HEAVE_MM:
        parser.error(f"--heave must be in [{MIN_HEAVE_MM:g}, {MAX_HEAVE_MM:g}]")
    if args.rate_hz <= 0:
        parser.error("--rate-hz must be greater than zero")
    if not 0.0 <= args.assist <= 1.0:
        parser.error("--assist must be in [0, 1]")
    if args.pose_update_every_n_frames <= 0:
        parser.error("--pose-update-every-n-frames must be greater than zero")
    if args.marker_ir_min_counts < 0:
        parser.error("--marker-ir-min-counts cannot be negative")
    if args.input == "gamepad" and args.reset_button_code == args.assist_button_code:
        parser.error("--reset-button-code and --assist-button-code must differ")
    if not 0.0 < args.smooth <= 1.0:
        parser.error("--smooth must be in (0, 1]")
    if args.max_tilt <= 0:
        parser.error("--max-tilt must be greater than zero")

    return args


def main() -> int:
    args = parse_args()
    try:
        return run(args)
    except PermissionError as exc:
        print(exc, file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
