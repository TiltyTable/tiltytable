#!/usr/bin/env python3
"""Roller-ball → Stewart tilt control (one-command workflow).

Intended use
------------
1. Run this script (serial open resets the Uno — calibration runs every time).
2. Interactive cal: jog each crank to vertical by eye (default).
3. Roll the arcade ball — the table pitches and rolls.

Opening /dev/arduino-stewart resets the Uno (CDC-ACM DTR). After a clean
``--hold-on-exit`` shutdown, firmware restores the persisted position and this
script resumes without calibration. Otherwise it calibrates, enables, moves
to mid-stroke heave (default 20 mm), and maps ball motion to roll/pitch.

Examples
--------
  # Preferred once udev rule is installed (no sudo):
  .venv/bin/python3 roller_ball.py

  # Until then (HID needs root on stock Jetson):
  sudo .venv/bin/python3 roller_ball.py
"""

from __future__ import annotations

import argparse
import math
import os
import re
import select
import struct
import sys
import time
from pathlib import Path

# Repo-root imports when launched via sudo / absolute path.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import serial

from stewart_serial import open_stewart_serial, wait_if_reset
from stewart_calibrate import run_interactive_calibration, run_legacy_calibration

EVENT_ROOT = Path("/dev/input")
DEFAULT_MOUSE = EVENT_ROOT / "by-id/usb-13ba_Barcode_Reader-if01-event-mouse"
DEFAULT_PORT = "/dev/arduino-stewart"

EV_SYN = 0x00
EV_REL = 0x02
INPUT_EVENT = struct.Struct("llHHI")

# Guaranteed all-direction envelope at heave=20 for the as-built geometry.
# Keep a small margin inside the modeled 4.8° limit.
MAX_TILT_DEG = 4.6
CALIBRATE_HEAVE_MM = 30.0  # max heave = cranks straight up (calibrate pose)
# Mid-stroke operating height: maximizes roll/pitch workspace with BASE=119.
# At heave 30, IK tilt envelope ≈ 0°. At heave 12 (rod-end floor) ≈ 0.75°.
# Around heave 20, envelope ≈ ±5.5° (matches firmware MAX_ROLL/PITCH).
OPERATING_HEAVE_MM = 20.0
# Don't spam identical poses; firmware already interpolates.
POSE_EPS_DEG = 0.02
DEFAULT_SCALE = 0.04
DEFAULT_SMOOTH = 1.0


def signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp_tilt_vector(roll: float, pitch: float, max_tilt: float) -> tuple[float, float]:
    """Clamp roll/pitch to a circular all-direction tilt envelope."""
    magnitude = math.hypot(roll, pitch)
    if magnitude <= max_tilt or magnitude == 0.0:
        return roll, pitch
    scale = max_tilt / magnitude
    return roll * scale, pitch * scale


def apply_trackball_counts(
    roll: float,
    pitch: float,
    dx: int,
    dy: int,
    *,
    scale: float,
    roll_sign: float,
    pitch_sign: float,
    max_tilt: float,
) -> tuple[float, float]:
    """Apply relative ball motion to an absolute, radially bounded tilt target."""
    pitch += dx * scale * pitch_sign
    roll += -dy * scale * roll_sign
    return clamp_tilt_vector(roll, pitch, max_tilt)


def smooth_tilt(
    current_roll: float,
    current_pitch: float,
    target_roll: float,
    target_pitch: float,
    alpha: float,
    max_tilt: float,
) -> tuple[float, float]:
    """Blend toward a position target, preserving the circular envelope."""
    roll = current_roll + (target_roll - current_roll) * alpha
    pitch = current_pitch + (target_pitch - current_pitch) * alpha
    return clamp_tilt_vector(roll, pitch, max_tilt)


def parse_status_pose(status: str) -> tuple[float, float, float] | None:
    values: list[float] = []
    for name in ("roll", "pitch", "heave"):
        match = re.search(rf"(?:^|\s){name}\s+([-0-9.]+)", status)
        if match is None:
            return None
        values.append(float(match.group(1)))
    return values[0], values[1], values[2]


def find_roller_ball() -> Path | None:
    if DEFAULT_MOUSE.exists():
        return DEFAULT_MOUSE.resolve()
    by_id = EVENT_ROOT / "by-id"
    if by_id.exists():
        for link in sorted(by_id.iterdir()):
            if "mouse" in link.name.lower():
                return link.resolve()
    return None


class Stewart:
    def __init__(self, port: str, baud: int = 115200, verbose: bool = False) -> None:
        self.port = port
        self.baud = baud
        self.verbose = verbose
        self.ser = None

    def open(self) -> None:
        print(f"Opening {self.port} …")
        self.ser = open_stewart_serial(self.port, self.baud, timeout=0.3)
        reset = wait_if_reset(self.ser, wait_s=2.2)
        if not reset:
            # Banner may have been missed; ACM open still usually resets.
            time.sleep(2.2)
        print("  board ready (serial open resets Uno — will recalibrate)")

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def _exchange(self, command: str, wait_s: float = 0.8) -> str:
        assert self.ser is not None
        self.ser.reset_input_buffer()
        self.ser.write((command.rstrip() + "\n").encode("ascii"))
        self.ser.flush()
        end = time.time() + wait_s
        chunks: list[bytes] = []
        while time.time() < end:
            ready, _, _ = select.select([self.ser.fileno()], [], [], 0.05)
            if not ready:
                continue
            data = self.ser.read(512)
            if not data:
                break
            chunks.append(data)
            end = time.time() + 0.15
        text = b"".join(chunks).decode("utf-8", "replace").strip()
        if self.verbose and text:
            for line in text.splitlines():
                print(f"  < {line}")
        return text

    def _drain_input(self) -> str:
        """Read firmware chatter so the ACM TX path does not stall."""
        assert self.ser is not None
        chunks: list[bytes] = []
        try:
            while self.ser.in_waiting:
                chunks.append(self.ser.read(self.ser.in_waiting))
        except Exception:
            pass
        return b"".join(chunks).decode("utf-8", "replace").strip()

    def _fire(self, command: str) -> str:
        assert self.ser is not None
        previous_reply = self._drain_input()
        payload = (command.rstrip() + "\n").encode("ascii")
        try:
            self.ser.write(payload)
            self.ser.flush()
        except serial.SerialTimeoutException:
            # Recover once: flush and retry.
            try:
                self.ser.reset_output_buffer()
                self.ser.reset_input_buffer()
            except Exception:
                pass
            time.sleep(0.05)
            self.ser.write(payload)
            self.ser.flush()
        return previous_reply

    def wait_idle(self, timeout_s: float = 45.0) -> None:
        """Block until firmware reports moving 0 (or timeout)."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            status = self._exchange("status", wait_s=0.4)
            if "moving 0" in status:
                return
            time.sleep(0.2)
        print("  (warning: move still active after timeout — continuing)")

    def bring_up(
        self,
        heave_mm: float,
        *,
        legacy_cal: bool = False,
    ) -> tuple[float, float, float]:
        """Restore a saved live pose, or calibrate and move to operating heave."""
        status = self._exchange("status", wait_s=0.8)
        restored_pose = (
            parse_status_pose(status)
            if "calibrated 1" in status and "restored 1" in status
            else None
        )

        if restored_pose is not None:
            print(
                "  restored prior live position "
                f"(roll={restored_pose[0]:+.2f}°, pitch={restored_pose[1]:+.2f}°, "
                f"heave={restored_pose[2]:g} mm)"
            )
        else:
            if legacy_cal:
                print("Calibrating (legacy — all cranks must already be straight up) …")
                ok = run_legacy_calibration(self.ser, skip_prompt=True)  # type: ignore[arg-type]
            else:
                ok = run_interactive_calibration(self.ser, skip_intro=True)  # type: ignore[arg-type]
            if not ok:
                raise RuntimeError(
                    "Calibration failed after serial open. "
                    "Re-run stewart_calibrate.py or check firmware."
                )

        print("Enabling motors …")
        en = self._exchange("enable", wait_s=0.8)
        if "ERR" in en:
            raise RuntimeError(f"enable failed: {en!r}")
        print("  enabled OK")

        if restored_pose is not None:
            self.wait_idle()
            print("  ready for roller ball (no calibration move)\n")
            return restored_pose

        # Max heave has almost no tilt workspace; drop to mid-stroke first.
        print(f"Moving to operating heave={heave_mm:g} mm (mid-stroke for tilt) …")
        pose = self._exchange(f"pose 0 0 {heave_mm:.3f}", wait_s=1.0)
        if "ERR" in pose:
            raise RuntimeError(f"pose to operating heave failed: {pose!r}")
        self.wait_idle()
        print("  ready for roller ball\n")
        return 0.0, 0.0, heave_mm

    def pose(self, roll: float, pitch: float, heave: float) -> str:
        """Send a non-blocking pose and return feedback for the prior pose."""
        return self._fire(f"pose {roll:.3f} {pitch:.3f} {heave:.3f}")

    def disable(self) -> None:
        try:
            self._drain_input()
            self._exchange("disable", wait_s=0.5)
        except Exception:
            pass

    def hold(self) -> None:
        """Stop at the current position and leave all motor drivers enabled."""
        self._drain_input()
        reply = self._exchange("hold", wait_s=0.5)
        if "ERR" in reply:
            raise RuntimeError(f"hold failed: {reply!r}")
        saved = self._exchange("persist", wait_s=0.8)
        if "OK persist saved" not in saved:
            raise RuntimeError(f"persist failed: {saved!r}")


def open_mouse(path: Path) -> int:
    try:
        return os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot read {path} (permission denied).\n"
            "Install the udev rule once:\n"
            "  sudo cp udev/99-tiltytable-rollerball.rules /etc/udev/rules.d/\n"
            "  sudo udevadm control --reload-rules && sudo udevadm trigger\n"
            "  # unplug/replug the ball\n"
            "Or run with:\n"
            f"  sudo {_ROOT}/.venv/bin/python3 {_ROOT}/roller_ball.py"
        ) from exc


def confirm_setup(skip: bool, *, legacy_cal: bool) -> None:
    print()
    print("=" * 60)
    print("  ROLLER BALL → STEWART TILT")
    print("=" * 60)
    print()
    print("Before continuing:")
    print("  1. Power the UIM motors.")
    print("  2. Clear the table / keep hands clear of the mechanism.")
    if legacy_cal:
        print("  3. Manually set ALL THREE cranks STRAIGHT UP (max heave).")
    else:
        print("  3. Full-screen cal TUI: jog each crank vertical (↑↓←→), Enter to confirm.")
    print()
    if skip:
        print("(--yes: skipping confirmation)")
        return
    try:
        input("Press Enter to start calibration (Ctrl-C to abort) … ")
    except EOFError:
        print("No TTY — re-run with --yes.", file=sys.stderr)
        raise SystemExit(2)


def run(args: argparse.Namespace) -> int:
    mouse = Path(args.device) if args.device else find_roller_ball()
    if mouse is None:
        print("No roller-ball HID device found. Plug it in and check:", file=sys.stderr)
        print("  python3 capture_usb_mouse.py --list", file=sys.stderr)
        return 2

    confirm_setup(args.yes, legacy_cal=args.legacy_cal)

    heave = float(args.heave)
    max_tilt = float(args.max_tilt)
    scale = float(args.scale)  # degrees per HID count
    rate_hz = float(args.rate_hz)
    alpha = float(args.smooth)  # EMA toward commanded tilt (1 = no smooth)

    stewart = Stewart(args.port, args.baud, verbose=args.verbose)
    mouse_fd = open_mouse(mouse)
    print(f"Roller ball: {mouse}")

    roll_cmd = 0.0
    pitch_cmd = 0.0
    roll_out = 0.0
    pitch_out = 0.0
    last_sent_roll = None
    last_sent_pitch = None
    pending_pose: tuple[float, float] | None = None
    last_accepted_pose = (0.0, 0.0)
    pending_dx = 0
    pending_dy = 0
    interval = 1.0 / rate_hz
    last_send = 0.0
    last_status = 0.0

    try:
        stewart.open()
        roll_cmd, pitch_cmd, heave = stewart.bring_up(
            heave, legacy_cal=args.legacy_cal
        )
        roll_out, pitch_out = roll_cmd, pitch_cmd
        last_accepted_pose = (roll_cmd, pitch_cmd)

        exit_behavior = (
            "holds the current pose" if args.hold_on_exit else "centers and disables motors"
        )
        print(f"Roll the ball to tilt. Ctrl-C {exit_behavior}.")
        print(
            f"  map: Y→roll  X→pitch  |  gain={scale:g}°/count  "
            f"radial limit={max_tilt:g}°  heave={heave:g} mm  rate={rate_hz:g} Hz"
        )
        print()

        while True:
            timeout = max(0.0, interval - (time.monotonic() - last_send))
            readable, _, _ = select.select([mouse_fd], [], [], timeout)

            if mouse_fd in readable:
                try:
                    data = os.read(mouse_fd, INPUT_EVENT.size * 64)
                except BlockingIOError:
                    data = b""
                for offset in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
                    _sec, _usec, etype, code, value = INPUT_EVENT.unpack_from(data, offset)
                    if etype != EV_REL:
                        continue
                    delta = signed32(value)
                    if code == 0x00:  # REL_X
                        pending_dx += delta
                    elif code == 0x01:  # REL_Y
                        pending_dy += delta

            now = time.monotonic()
            if now - last_send < interval:
                continue

            if abs(pending_dx) > args.deadband or abs(pending_dy) > args.deadband:
                roll_cmd, pitch_cmd = apply_trackball_counts(
                    roll_cmd,
                    pitch_cmd,
                    pending_dx,
                    pending_dy,
                    scale=scale,
                    roll_sign=args.roll_sign,
                    pitch_sign=args.pitch_sign,
                    max_tilt=max_tilt,
                )
                pending_dx = 0
                pending_dy = 0

            # Exponential smooth toward command (quieter, less jerky).
            roll_out, pitch_out = smooth_tilt(
                roll_out,
                pitch_out,
                roll_cmd,
                pitch_cmd,
                alpha,
                max_tilt,
            )

            moved = (
                last_sent_roll is None
                or abs(roll_out - last_sent_roll) >= POSE_EPS_DEG
                or abs(pitch_out - last_sent_pitch) >= POSE_EPS_DEG
            )
            if moved:
                try:
                    feedback = stewart.pose(roll_out, pitch_out, heave)
                    if "ERR pose" in feedback:
                        print(f"\n  firmware rejected prior pose: {feedback}")
                        roll_cmd, pitch_cmd = last_accepted_pose
                        roll_out, pitch_out = last_accepted_pose
                    elif "OK pose" in feedback and pending_pose is not None:
                        last_accepted_pose = pending_pose
                    pending_pose = (roll_out, pitch_out)
                    last_sent_roll = roll_out
                    last_sent_pitch = pitch_out
                except serial.SerialException as exc:
                    print(f"\n  serial write issue ({exc}); retrying …")
                    time.sleep(0.1)
            last_send = now

            if now - last_status >= 0.25:
                print(
                    f"\r  roll={roll_out:+5.2f}°  pitch={pitch_out:+5.2f}°  heave={heave:g} mm   ",
                    end="",
                    flush=True,
                )
                last_status = now

    except KeyboardInterrupt:
        print("\n\nStopping …")
    except serial.SerialException as exc:
        print(f"\n\nSerial error: {exc}")
    finally:
        if args.hold_on_exit:
            print("\nHolding current position; motors remain energized …")
            try:
                stewart.hold()
            except Exception as exc:
                print(f"WARNING: hold command failed: {exc}")
        else:
            print("\nDisabling motors …")
            try:
                stewart.pose(0.0, 0.0, heave)
                time.sleep(0.3)
            except Exception:
                pass
            stewart.disable()
        stewart.close()
        os.close(mouse_fd)
        print("Done.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Arcade roller ball → Stewart tilt (calibrate + enable + absolute pose).",
    )
    parser.add_argument("device", nargs="?", help="HID event path (default: auto-detect roller ball)")
    parser.add_argument("--port", default=DEFAULT_PORT, help="Stewart serial port")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--heave",
        type=float,
        default=OPERATING_HEAVE_MM,
        help=(
            f"Operating heave mm after calibrate "
            f"(default {OPERATING_HEAVE_MM:g} = mid-stroke; "
            f"calibrate pose is {CALIBRATE_HEAVE_MM:g})"
        ),
    )
    parser.add_argument(
        "--max-tilt",
        type=float,
        default=MAX_TILT_DEG,
        help="Clamp total roll/pitch vector magnitude in degrees",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=DEFAULT_SCALE,
        help="Degrees of tilt per HID count",
    )
    parser.add_argument("--rate-hz", type=float, default=30.0, help="Pose command rate")
    parser.add_argument(
        "--smooth",
        type=float,
        default=DEFAULT_SMOOTH,
        help="EMA blend toward target each frame (0–1; lower = smoother/slower)",
    )
    parser.add_argument("--deadband", type=int, default=1, help="Ignore tiny HID counts")
    parser.add_argument("--roll-sign", type=float, choices=(-1.0, 1.0), default=1.0)
    parser.add_argument("--pitch-sign", type=float, choices=(-1.0, 1.0), default=-1.0)
    parser.add_argument("--yes", "-y", action="store_true", help="Skip startup confirmation")
    parser.add_argument(
        "--legacy-cal",
        action="store_true",
        help="Legacy calibrate (all cranks manually straight up before enable)",
    )
    parser.add_argument(
        "--hold-on-exit",
        action="store_true",
        help=(
            "Stop at the current pose and leave motors energized on exit; "
            "opening the Uno serial port again will still reset and disable it"
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print raw Arduino replies")
    args = parser.parse_args()

    if args.heave < 12.0 or args.heave > CALIBRATE_HEAVE_MM:
        parser.error(f"--heave must be in [12, {CALIBRATE_HEAVE_MM:g}] for this geometry")
    if not 0.0 < args.smooth <= 1.0:
        parser.error("--smooth must be in (0, 1]")
    if (
        args.rate_hz <= 0
        or args.scale <= 0
        or args.max_tilt <= 0
    ):
        parser.error("--rate-hz, --scale, and --max-tilt must be > 0")

    try:
        return run(args)
    except PermissionError as exc:
        print(exc, file=sys.stderr)
        return 1
    except (RuntimeError, serial.SerialException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
