#!/usr/bin/env python3
"""Roller-ball position control for experimental free-heave Stewart firmware."""

from __future__ import annotations

import argparse
import math
import os
import select
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import serial

from analysis.stewart_exp_kinematics import NoSolutionError, optimize_heave
from analysis.tilt_kinematics import Geometry
from stewart_exp_probe import ExpLink, calibrate

EVENT_ROOT = Path("/dev/input")
DEFAULT_MOUSE = EVENT_ROOT / "by-id/usb-13ba_Barcode_Reader-if01-event-mouse"
INPUT_EVENT = struct.Struct("llHHI")
EV_SYN = 0x00
EV_REL = 0x02
SYN_REPORT = 0x00
REL_X = 0x00
REL_Y = 0x01


def signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


@dataclass
class TrackballVectorAccumulator:
    frame_dx: int = 0
    frame_dy: int = 0
    pending_dx: int = 0
    pending_dy: int = 0
    window_started_at: float | None = None

    def feed(
        self, event_type: int, code: int, value: int, now: float
    ) -> None:
        if event_type == EV_REL:
            if code == REL_X:
                self.frame_dx += value
            elif code == REL_Y:
                self.frame_dy += value
            return
        if event_type == EV_SYN and code == SYN_REPORT:
            if self.frame_dx or self.frame_dy:
                self.pending_dx += self.frame_dx
                self.pending_dy += self.frame_dy
                if self.window_started_at is None:
                    self.window_started_at = now
            self.frame_dx = 0
            self.frame_dy = 0

    def pop_ready(self, now: float, window_seconds: float) -> tuple[int, int] | None:
        if self.window_started_at is None:
            return None
        if now - self.window_started_at < window_seconds:
            return None
        result = self.pending_dx, self.pending_dy
        self.pending_dx = 0
        self.pending_dy = 0
        self.window_started_at = None
        return result


def find_roller_ball() -> Path | None:
    if DEFAULT_MOUSE.exists():
        return DEFAULT_MOUSE.resolve()
    by_id = EVENT_ROOT / "by-id"
    if by_id.exists():
        for path in sorted(by_id.iterdir()):
            if "mouse" in path.name.lower():
                return path.resolve()
    return None


def clamp_vector(roll: float, pitch: float, radius: float) -> tuple[float, float]:
    magnitude = math.hypot(roll, pitch)
    if magnitude <= radius or magnitude == 0.0:
        return roll, pitch
    scale = radius / magnitude
    return roll * scale, pitch * scale


def step_toward(
    roll: float,
    pitch: float,
    target_roll: float,
    target_pitch: float,
    max_step_deg: float,
) -> tuple[float, float]:
    delta_roll = target_roll - roll
    delta_pitch = target_pitch - pitch
    distance = math.hypot(delta_roll, delta_pitch)
    if distance <= max_step_deg or distance == 0.0:
        return target_roll, target_pitch
    scale = max_step_deg / distance
    return roll + delta_roll * scale, pitch + delta_pitch * scale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", nargs="?")
    parser.add_argument("--port", default="/dev/arduino-stewart")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--max-tilt", type=float, default=10.0)
    parser.add_argument("--scale", type=float, default=0.04)
    parser.add_argument("--rate-hz", type=float, default=30.0)
    parser.add_argument("--target-step", type=float, default=0.5)
    parser.add_argument("--heave-min", type=float, default=-15.0)
    parser.add_argument("--heave-max", type=float, default=30.0)
    parser.add_argument("--heave-step", type=float, default=0.25)
    parser.add_argument("--max-heave-step", type=float, default=0.5)
    parser.add_argument("--max-following-error", type=float, default=2.0)
    parser.add_argument("--deadband", type=int, default=1)
    parser.add_argument(
        "--vector-window-ms",
        type=float,
        default=8.0,
        help="aggregate complete SYN_REPORT vectors for this many milliseconds",
    )
    parser.add_argument("--roll-sign", type=float, choices=(-1.0, 1.0), default=1.0)
    parser.add_argument("--pitch-sign", type=float, choices=(-1.0, 1.0), default=-1.0)
    parser.add_argument("--yes", "-y", action="store_true")
    args = parser.parse_args()

    if min(
        args.max_tilt,
        args.scale,
        args.rate_hz,
        args.target_step,
        args.heave_step,
        args.max_heave_step,
        args.max_following_error,
        args.vector_window_ms,
    ) <= 0:
        parser.error("tilt, scales, rates, and step limits must be positive")

    device = Path(args.device) if args.device else find_roller_ball()
    if device is None:
        print("Roller ball not found.", file=sys.stderr)
        return 2
    try:
        mouse_fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except PermissionError as exc:
        print(f"Cannot read {device}: {exc}", file=sys.stderr)
        return 2

    print("EXPERIMENTAL ROLLER BALL — FREE HEAVE / DUAL BRANCH")
    print(f"device={device} max_tilt={args.max_tilt:g}°")
    print("Keep the loaded table protected against DTR reset or power loss.")
    if not args.yes:
        confirmation = input("Type START to connect/calibrate/arm: ")
        if confirmation != "START":
            os.close(mouse_fd)
            return 2

    link = ExpLink(args.port, args.baud)
    geometry = Geometry()
    try:
        link.open()
        status = link.status()
        if not status.calibrated:
            status = calibrate(link)
        current = status.as_pose()
        desired_roll = current.roll_deg
        desired_pitch = current.pitch_deg
        vectors = TrackballVectorAccumulator()
        interval = 1.0 / args.rate_hz
        last_update = time.monotonic()

        link.require_ok("ARM CONFIRM", "OK ARM")
        print(
            "Live: Y→roll, X→pitch; stopping the ball holds position. "
            "Ctrl-C aborts and holds."
        )

        while True:
            timeout = max(0.0, interval - (time.monotonic() - last_update))
            readable, _, _ = select.select([mouse_fd], [], [], timeout)
            if mouse_fd in readable:
                try:
                    data = os.read(mouse_fd, INPUT_EVENT.size * 64)
                except BlockingIOError:
                    data = b""
                for offset in range(
                    0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size
                ):
                    _, _, event_type, code, raw_value = INPUT_EVENT.unpack_from(
                        data, offset
                    )
                    value = signed32(raw_value)
                    vectors.feed(event_type, code, value, time.monotonic())

            now = time.monotonic()
            if now - last_update < interval:
                continue
            last_update = now

            vector = vectors.pop_ready(now, args.vector_window_ms / 1000.0)
            if vector is not None:
                pending_dx, pending_dy = vector
            else:
                pending_dx = pending_dy = 0

            if abs(pending_dx) > args.deadband or abs(pending_dy) > args.deadband:
                magnitude = math.hypot(pending_dx, pending_dy)
                angle = math.atan2(-pending_dy, pending_dx)
                vector_dx = magnitude * math.cos(angle)
                vector_dy = -magnitude * math.sin(angle)
                desired_pitch += vector_dx * args.scale * args.pitch_sign
                desired_roll += -vector_dy * args.scale * args.roll_sign
                desired_roll, desired_pitch = clamp_vector(
                    desired_roll, desired_pitch, args.max_tilt
                )

            next_roll, next_pitch = step_toward(
                current.roll_deg,
                current.pitch_deg,
                desired_roll,
                desired_pitch,
                args.target_step,
            )
            if (
                abs(next_roll - current.roll_deg) < 1e-8
                and abs(next_pitch - current.pitch_deg) < 1e-8
            ):
                continue

            solution = optimize_heave(
                geometry=geometry,
                roll_deg=next_roll,
                pitch_deg=next_pitch,
                previous=current,
                heave_min_mm=args.heave_min,
                heave_max_mm=args.heave_max,
                heave_step_mm=args.heave_step,
                max_heave_step_mm=args.max_heave_step,
            )
            if solution is None or solution.max_crank_delta_deg > 12.0:
                print(
                    "\nNo continuous IK step; retaining last valid target.",
                    file=sys.stderr,
                )
                desired_roll, desired_pitch = current.roll_deg, current.pitch_deg
                continue

            link.target(solution)
            link.wait_following(solution.steps, args.max_following_error)
            current = solution
            print(
                f"\rr={current.roll_deg:+5.2f}° p={current.pitch_deg:+5.2f}° "
                f"h={current.heave_mm:+6.2f} mm",
                end="",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\nStopping; holding current position.")
        try:
            link.require_ok("ABORT", "OK ABORT")
        except Exception as exc:
            print(f"WARNING: abort failed: {exc}", file=sys.stderr)
        return 0
    except (RuntimeError, serial.SerialException, TimeoutError, NoSolutionError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        try:
            link.require_ok("ABORT", "OK ABORT")
        except Exception:
            pass
        return 1
    finally:
        if link.ser is not None:
            try:
                link.require_ok("HOLD", "OK HOLD")
            except Exception as exc:
                print(f"WARNING: final hold failed: {exc}", file=sys.stderr)
            link.close()
        os.close(mouse_fd)


if __name__ == "__main__":
    raise SystemExit(main())
