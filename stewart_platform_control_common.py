#!/usr/bin/env python3
"""Shared host control for the experimental Stewart step executor.

Both public controllers use the same Arduino firmware:
``arduino/uim5756_stewart_r4/uim5756_stewart_r4.ino``.  The firmware is
an absolute-step executor; this module owns IK, free-heave selection, trackball
event decoding, calibration, arming, and safe hold-on-exit behavior.
"""

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

from analysis.stewart_exp_kinematics import (
    NoSolutionError,
    PoseSolution,
    experimental_geometry,
    optimize_heave,
    plan_heave_transition,
    solve_pose_at_heave,
)
from stewart_exp_probe import ExpLink, calibrate
from stewart_supervisor_client import DEFAULT_SOCKET

EVENT_ROOT = Path("/dev/input")
DEFAULT_TRACKBALL = (
    EVENT_ROOT / "by-id/usb-13ba_Barcode_Reader-if01-event-mouse"
)
INPUT_EVENT = struct.Struct("llHHI")
EV_SYN = 0x00
EV_REL = 0x02
SYN_REPORT = 0x00
REL_X = 0x00
REL_Y = 0x01


def signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def clamp_vector(x: float, y: float, radius: float) -> tuple[float, float]:
    magnitude = math.hypot(x, y)
    if magnitude <= radius or magnitude == 0.0:
        return x, y
    scale = radius / magnitude
    return x * scale, y * scale


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


def decay_velocity(
    roll_velocity: float,
    pitch_velocity: float,
    dt: float,
    decay_time_s: float,
) -> tuple[float, float]:
    """Exponentially decay an angular-velocity vector toward zero."""
    factor = math.exp(-max(0.0, dt) / decay_time_s)
    return roll_velocity * factor, pitch_velocity * factor


def integrate_velocity(
    roll: float,
    pitch: float,
    roll_velocity: float,
    pitch_velocity: float,
    dt: float,
    max_tilt_deg: float,
) -> tuple[float, float, float, float]:
    """Integrate velocity and remove velocity pushing out of the tilt limit."""
    next_roll, next_pitch = clamp_vector(
        roll + roll_velocity * dt,
        pitch + pitch_velocity * dt,
        max_tilt_deg,
    )
    magnitude = math.hypot(next_roll, next_pitch)
    if magnitude >= max_tilt_deg - 1e-9 and magnitude > 0.0:
        unit_roll = next_roll / magnitude
        unit_pitch = next_pitch / magnitude
        outward = roll_velocity * unit_roll + pitch_velocity * unit_pitch
        if outward > 0.0:
            roll_velocity -= outward * unit_roll
            pitch_velocity -= outward * unit_pitch
    return next_roll, next_pitch, roll_velocity, pitch_velocity


def find_trackball() -> Path | None:
    if DEFAULT_TRACKBALL.exists():
        return DEFAULT_TRACKBALL.resolve()
    by_id = EVENT_ROOT / "by-id"
    if by_id.exists():
        for path in sorted(by_id.iterdir()):
            name = path.name.lower()
            if "event-mouse" in name or (
                "mouse" in name and "event" in name
            ):
                return path.resolve()
    return None


@dataclass
class TrackballAccumulator:
    """Accumulate REL_X/REL_Y and publish only complete SYN_REPORT frames."""

    frame_dx: int = 0
    frame_dy: int = 0
    pending_dx: int = 0
    pending_dy: int = 0

    def feed_bytes(self, data: bytes) -> None:
        for offset in range(
            0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size
        ):
            _, _, event_type, code, raw_value = INPUT_EVENT.unpack_from(
                data, offset
            )
            value = signed32(raw_value)
            if event_type == EV_REL:
                if code == REL_X:
                    self.frame_dx += value
                elif code == REL_Y:
                    self.frame_dy += value
            elif event_type == EV_SYN and code == SYN_REPORT:
                self.pending_dx += self.frame_dx
                self.pending_dy += self.frame_dy
                self.frame_dx = 0
                self.frame_dy = 0

    def pop(self) -> tuple[int, int]:
        result = self.pending_dx, self.pending_dy
        self.pending_dx = 0
        self.pending_dy = 0
        return result


class TrackballDevice:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None
        self.accumulator = TrackballAccumulator()

    def open(self) -> None:
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def wait(self, timeout: float) -> None:
        if self.fd is None:
            raise RuntimeError("trackball is not open")
        readable, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
        if self.fd in readable:
            try:
                data = os.read(self.fd, INPUT_EVENT.size * 64)
            except BlockingIOError:
                data = b""
            self.accumulator.feed_bytes(data)

    def pop(self) -> tuple[int, int]:
        return self.accumulator.pop()


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "device",
        nargs="?",
        help="Linux event-mouse device (default: auto-detect the trackball)",
    )
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--rate-hz", type=float, default=60.0)
    parser.add_argument("--max-tilt", type=float, default=10.0)
    parser.add_argument(
        "--platform-step",
        type=float,
        default=1.0,
        help="maximum roll/pitch target change per control update (degrees)",
    )
    parser.add_argument("--heave-min", type=float, default=-15.0)
    parser.add_argument("--heave-max", type=float, default=30.0)
    parser.add_argument("--heave-step", type=float, default=0.25)
    parser.add_argument("--max-heave-step", type=float, default=0.5)
    parser.add_argument(
        "--startup-heave",
        type=float,
        default=0.0,
        help="initial free-heave operating point used before live control",
    )
    parser.add_argument("--crank-speed", type=float, default=40.0)
    parser.add_argument("--crank-accel", type=float, default=120.0)
    parser.add_argument(
        "--step-offsets",
        type=int,
        nargs=3,
        default=(0, 0, 0),
        metavar=("S0", "S1", "S2"),
        help="explicit model-to-motor step corrections; defaults to zero",
    )
    parser.add_argument(
        "--zero-on-start",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "move roll/pitch to model zero before accepting trackball input; "
            "the default resumes the previously held pose"
        ),
    )
    parser.add_argument(
        "--yes",
        "-y",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="skip the startup motion confirmation",
    )


def validate_common_arguments(args: argparse.Namespace) -> None:
    positive = (
        args.rate_hz,
        args.max_tilt,
        args.platform_step,
        args.heave_step,
        args.max_heave_step,
        args.crank_speed,
        args.crank_accel,
    )
    if min(positive) <= 0.0:
        raise ValueError("rates, limits, and step sizes must be positive")
    if args.heave_min >= args.heave_max:
        raise ValueError("--heave-min must be less than --heave-max")
    if not args.heave_min <= args.startup_heave <= args.heave_max:
        raise ValueError("--startup-heave must be inside the heave range")
    if args.crank_speed > 90.0:
        raise ValueError("--crank-speed must be <= 90 deg/s")
    if args.crank_accel > 500.0:
        raise ValueError("--crank-accel must be <= 500 deg/s^2")


def resolve_trackball(args: argparse.Namespace) -> Path:
    device = Path(args.device) if args.device else find_trackball()
    if device is None:
        raise RuntimeError("trackball event device was not found")
    return device


class StewartPlatformController:
    """Stateful free-heave IK client for the common Arduino executor."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.geometry = experimental_geometry()
        self.step_offsets = tuple(int(value) for value in args.step_offsets)
        self.link = ExpLink(
            args.socket,
            mode="motion",
        )
        self.current: PoseSolution | None = None
        self.armed = False

    def open(
        self,
        *,
        arm: bool = True,
        calibrate_if_needed: bool = True,
    ) -> None:
        self.link.open()
        self.link.require_ok(
            f"PROFILE {self.args.crank_speed:.3f} "
            f"{self.args.crank_accel:.3f}",
            "OK PROFILE",
        )
        status = self.link.startup_status
        assert status is not None
        if not status.calibrated:
            if not calibrate_if_needed:
                raise RuntimeError(
                    "Stewart calibration is required before launching the arcade"
                )
            status = calibrate(self.link)
        self.armed = status.armed
        self.current = status.as_pose(self.step_offsets)
        if arm:
            self._ensure_armed()
        if arm and self.args.zero_on_start:
            self._prepare_operating_heave()
            self.move_to(0.0, 0.0)

    def _ensure_armed(self) -> None:
        if not self.armed:
            self.link.require_ok("ARM CONFIRM", "OK ARM")
            self.armed = True

    def _prepare_operating_heave(self) -> None:
        assert self.current is not None
        if abs(self.current.heave_mm - self.args.startup_heave) < 1e-9:
            return
        transition = plan_heave_transition(
            self.current,
            self.args.startup_heave,
            geometry=self.geometry,
            step_mm=self.args.heave_step,
        )
        for pose in transition:
            self.link.target(pose, self.step_offsets)
        self.link.wait_idle()
        self.current = transition[-1]

    def at_target(self, roll: float, pitch: float, tolerance: float = 1e-7) -> bool:
        if self.current is None:
            return False
        angles_match = (
            abs(self.current.roll_deg - roll) <= tolerance
            and abs(self.current.pitch_deg - pitch) <= tolerance
        )
        if not angles_match:
            return False
        if abs(roll) <= tolerance and abs(pitch) <= tolerance:
            return (
                abs(self.current.heave_mm - self.args.startup_heave)
                <= tolerance
            )
        return True

    def _canonical_zero_heave_step(self) -> PoseSolution | None:
        """Move an already-level zero pose toward its repeatable zero heave."""
        assert self.current is not None
        if abs(self.current.roll_deg) > 1e-7 or abs(self.current.pitch_deg) > 1e-7:
            return None
        distance = self.args.startup_heave - self.current.heave_mm
        if abs(distance) <= 1e-7:
            return None
        heave_delta = max(
            -self.args.max_heave_step,
            min(self.args.max_heave_step, distance),
        )
        return solve_pose_at_heave(
            self.geometry,
            0.0,
            0.0,
            self.current.heave_mm + heave_delta,
            self.current.crank_deg,
            estimate_torque=False,
        )

    def command_toward(self, roll: float, pitch: float) -> PoseSolution:
        if self.current is None:
            raise RuntimeError("Stewart controller is not open")
        canonical_zero = (
            abs(roll) <= 1e-7
            and abs(pitch) <= 1e-7
            and abs(self.current.roll_deg) <= 1e-7
            and abs(self.current.pitch_deg) <= 1e-7
        )
        next_roll, next_pitch = step_toward(
            self.current.roll_deg,
            self.current.pitch_deg,
            roll,
            pitch,
            self.args.platform_step,
        )
        solution = (
            self._canonical_zero_heave_step()
            if canonical_zero
            else optimize_heave(
                geometry=self.geometry,
                roll_deg=next_roll,
                pitch_deg=next_pitch,
                previous=self.current,
                heave_min_mm=self.args.heave_min,
                heave_max_mm=self.args.heave_max,
                heave_step_mm=self.args.heave_step,
                max_heave_step_mm=self.args.max_heave_step,
                estimate_torque=False,
                objective="agile",
            )
        )
        if solution is None:
            raise NoSolutionError(
                f"no continuous IK solution for roll={next_roll:.3f}, "
                f"pitch={next_pitch:.3f}"
            )
        if solution.max_crank_delta_deg > 12.0:
            raise NoSolutionError(
                f"IK crank jump {solution.max_crank_delta_deg:.2f}° exceeds 12°"
            )
        self._ensure_armed()
        self.link.target(solution, self.step_offsets)
        self.current = solution
        return solution

    def move_to(self, roll: float, pitch: float) -> PoseSolution:
        for _ in range(1000):
            if self.at_target(roll, pitch):
                break
            self.command_toward(roll, pitch)
        else:
            raise RuntimeError("pose target did not converge in 1000 waypoints")
        self.link.wait_idle()
        assert self.current is not None
        return self.current

    def hold_and_rebase(self) -> PoseSolution:
        """Stop at the physical motor positions and rebuild the IK pose."""
        if not self.link.is_open:
            raise RuntimeError("Stewart controller is not open")
        self.link.require_ok("HOLD", "OK HOLD")
        self.armed = False
        self.current = self.link.status().as_pose(self.step_offsets)
        return self.current

    def hold_and_close(self) -> None:
        if not self.link.is_open:
            return
        try:
            self.hold_and_rebase()
        except Exception as exc:
            print(f"WARNING: final HOLD failed: {exc}", file=sys.stderr)
        finally:
            self.link.close()


def confirm_motion(args: argparse.Namespace, mode: str, device: Path) -> bool:
    print(f"STEWART PLATFORM {mode.upper()} CONTROL")
    print(f"trackball={device} max_tilt={args.max_tilt:g}°")
    print(
        "Shared firmware: arduino/uim5756_stewart_r4/"
        "uim5756_stewart_r4.ino"
    )
    if any(args.step_offsets):
        print(f"explicit motor step offsets={tuple(args.step_offsets)}")
    if args.yes:
        return True
    return input("Type START to connect, calibrate if needed, and arm: ") == "START"


def run_loop_timing(last_update: float, rate_hz: float) -> tuple[float, float]:
    interval = 1.0 / rate_hz
    now = time.monotonic()
    timeout = max(0.0, interval - (now - last_update))
    return interval, timeout
