#!/usr/bin/env python3
"""Supervised full-rotation/free-heave Stewart experiment.

This tool only speaks to ``uim5756_stewart_r4`` firmware through the
persistent Stewart supervisor. Dry-run and envelope modes do not connect to
hardware. Live modes begin from the motor-position snapshot returned by the
supervisor, then perform planning, motion, logging, and final hold.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import select
import sys
import termios
import time
import tty
from dataclasses import asdict, dataclass
from pathlib import Path

from analysis.stewart_exp_kinematics import (
    NoSolutionError,
    PoseSolution,
    STEPS_PER_CRANK_REV,
    calibrated_solution,
    circle_targets,
    endpoint_heave_range,
    linear_targets,
    plan_circle,
    plan_targets,
    steps_to_crank_deg,
)
from stewart_supervisor_client import DEFAULT_SOCKET, StewartSupervisorClient


@dataclass(frozen=True)
class ExpStatus:
    calibrated: bool
    restored: bool
    calibrating: bool
    armed: bool
    enabled: bool
    moving: bool
    steps: tuple[int, int, int]
    targets: tuple[int, int, int]
    marked: tuple[bool, bool, bool]
    roll_deg: float
    pitch_deg: float
    heave_mm: float
    max_speed_deg_s: float
    max_accel_deg_s2: float

    @property
    def crank_deg(self) -> tuple[float, float, float]:
        return tuple(steps_to_crank_deg(value) for value in self.steps)

    def as_pose(
        self,
        step_offsets: tuple[int, int, int] = (0, 0, 0),
    ) -> PoseSolution:
        model_steps = tuple(
            self.steps[axis] - step_offsets[axis] for axis in range(3)
        )
        return PoseSolution(
            roll_deg=self.roll_deg,
            pitch_deg=self.pitch_deg,
            heave_mm=self.heave_mm,
            crank_deg=tuple(steps_to_crank_deg(value) for value in model_steps),
            branch_index=(0, 0, 0),
            closure_margin_mm=0.0,
            worst_advisory_joint_deg=0.0,
            max_crank_delta_deg=0.0,
            dead_center_margin_deg=0.0,
            max_static_torque_nm=0.0,
        )


def _field(text: str, name: str, cast):
    match = re.search(rf"(?:^|\s){re.escape(name)}=([-+0-9.]+)", text)
    if not match:
        raise ValueError(f"missing {name}= in status: {text!r}")
    return cast(match.group(1))


def parse_status(text: str) -> ExpStatus:
    if not text.startswith("OK STATUS") or "exp=1" not in text:
        raise ValueError(f"not experimental status: {text!r}")
    return ExpStatus(
        calibrated=bool(_field(text, "calibrated", int)),
        restored=bool(_field(text, "restored", int)),
        calibrating=bool(_field(text, "calibrating", int)),
        armed=bool(_field(text, "armed", int)),
        enabled=bool(_field(text, "enabled", int)),
        moving=bool(_field(text, "moving", int)),
        steps=tuple(_field(text, f"s{i}", int) for i in range(3)),
        targets=tuple(_field(text, f"t{i}", int) for i in range(3)),
        marked=tuple(bool(_field(text, f"m{i}", int)) for i in range(3)),
        roll_deg=_field(text, "roll", float),
        pitch_deg=_field(text, "pitch", float),
        heave_mm=_field(text, "heave", float),
        max_speed_deg_s=_field(text, "vmax", float),
        max_accel_deg_s2=_field(text, "amax", float),
    )


class ExpLink:
    def __init__(
        self,
        socket_path: Path = DEFAULT_SOCKET,
        *,
        mode: str = "motion",
    ) -> None:
        self.socket_path = socket_path
        self.mode = mode
        self.client: StewartSupervisorClient | None = None
        self.startup_status: ExpStatus | None = None

    @property
    def is_open(self) -> bool:
        return self.client is not None and self.client.is_open

    def open(self) -> None:
        self.client = StewartSupervisorClient(self.socket_path, mode=self.mode)
        self.client.open()
        identity = self.exchange("EXP?", timeout=0.8)
        if not identity.startswith("OK EXP UIM5756PM_STEWART_EXP"):
            raise RuntimeError(
                "wrong firmware; expected experimental executor, got "
                f"{identity!r}"
            )
        # This is deliberately part of opening the link: no live client may
        # plan from assumed zeroes while the persistent Arduino/supervisor is
        # holding a different absolute motor position.
        self.startup_status = self.status()

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def exchange(self, command: str, timeout: float = 0.6) -> str:
        if self.client is None:
            raise RuntimeError("experimental link is not open")
        return self.client.exchange(command, timeout)

    def require_ok(self, command: str, prefix: str = "OK") -> str:
        reply = self.exchange(command)
        if not reply.startswith(prefix):
            raise RuntimeError(f"{command!r} failed: {reply!r}")
        return reply

    def status(self) -> ExpStatus:
        return parse_status(self.require_ok("STATUS", "OK STATUS"))

    def wait_idle(self, timeout: float = 90.0) -> ExpStatus:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.status()
            if not status.moving:
                return status
            time.sleep(0.1)
        raise TimeoutError("experimental move did not become idle")

    def wait_following(
        self,
        target_steps: tuple[int, int, int],
        max_error_deg: float,
        timeout: float = 30.0,
    ) -> ExpStatus:
        max_error_steps = max_error_deg * STEPS_PER_CRANK_REV / 360.0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.status()
            error = max(
                abs(target_steps[axis] - status.steps[axis])
                for axis in range(3)
            )
            if error <= max_error_steps:
                return status
            time.sleep(0.03)
        raise TimeoutError("experimental target following error stayed too large")

    def target(
        self,
        pose: PoseSolution,
        step_offsets: tuple[int, int, int] = (0, 0, 0),
    ) -> None:
        steps = tuple(
            pose.steps[axis] + step_offsets[axis] for axis in range(3)
        )
        self.require_ok(
            "TARGET "
            f"{steps[0]} {steps[1]} {steps[2]} "
            f"{pose.roll_deg:.5f} {pose.pitch_deg:.5f} {pose.heave_mm:.5f}",
            "OK TARGET",
        )


def read_key() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        data = os.read(fd, 1)
        if data == b"\x1b":
            deadline = time.monotonic() + 0.15
            while len(data) < 3 and time.monotonic() < deadline:
                if select.select([fd], [], [], 0.03)[0]:
                    data += os.read(fd, 3 - len(data))
        return data.decode("ascii", "ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def calibrate(link: ExpLink, fine: int = 100, coarse: int = 800) -> ExpStatus:
    print("\nEXPERIMENTAL CALIBRATION")
    print(
        "For each axis: ←/→ or -/+ fine; ↓/↑ or B/F coarse; "
        "Enter marks vertical; q aborts."
    )
    link.require_ok("CAL BEGIN")
    for axis in range(3):
        print(f"\nAxis {axis}: jog crank pin straight UP, then Enter.")
        while True:
            key = read_key()
            if key in ("q", "Q", "\x03"):
                link.require_ok("ABORT")
                raise KeyboardInterrupt
            if key in ("\r", "\n"):
                link.wait_idle()
                link.require_ok(f"CAL MARK {axis}")
                print(f"  axis {axis} marked")
                break
            pulses = {
                "\x1b[C": fine,
                "\x1b[D": -fine,
                "\x1b[A": coarse,
                "\x1b[B": -coarse,
                "+": fine,
                "-": -fine,
                "F": coarse,
                "f": coarse,
                "B": -coarse,
                "b": -coarse,
                "l": fine,
                "h": -fine,
                "k": coarse,
                "j": -coarse,
            }.get(key)
            if pulses is None:
                continue
            link.wait_idle()
            reply = link.require_ok(f"CAL JOG {axis} {pulses}", "OK CAL JOG")
            print(f"\r  {reply:<60}", end="", flush=True)
    link.require_ok("CAL FINISH")
    status = link.status()
    if not status.calibrated:
        raise RuntimeError("calibration did not set calibrated=1")
    return status


def build_target_list(
    args: argparse.Namespace, initial: PoseSolution
) -> list[tuple[float, float]]:
    if args.circle is not None:
        targets = linear_targets(
            initial.roll_deg,
            initial.pitch_deg,
            0.0,
            args.circle,
            args.ramp_points,
        )
        targets.extend(circle_targets(args.circle, args.points)[1:])
        return targets
    if args.target is not None:
        return linear_targets(
            initial.roll_deg,
            initial.pitch_deg,
            args.target[0],
            args.target[1],
            args.ramp_points,
        )
    if args.cardinals is not None:
        radius = args.cardinals
        target_list: list[tuple[float, float]] = []
        current = (initial.roll_deg, initial.pitch_deg)
        for destination in (
            (0.0, radius),
            (radius, 0.0),
            (0.0, -radius),
            (-radius, 0.0),
            (0.0, 0.0),
        ):
            target_list.extend(
                linear_targets(
                    current[0],
                    current[1],
                    destination[0],
                    destination[1],
                    args.ramp_points,
                )
            )
            current = destination
        return target_list
    raise ValueError("select --circle, --target, or --cardinals")


def plan(args: argparse.Namespace, initial: PoseSolution) -> list[PoseSolution]:
    return plan_targets(
        build_target_list(args, initial),
        initial=initial,
        heave_min_mm=args.heave_min,
        heave_max_mm=args.heave_max,
        heave_step_mm=args.heave_step,
        max_heave_step_mm=args.max_heave_step,
        max_crank_step_deg=args.max_crank_step,
    )


def summarize(planned: list[PoseSolution]) -> None:
    print(f"waypoints: {len(planned)}")
    print(
        f"heave: {min(p.heave_mm for p in planned):.2f} .. "
        f"{max(p.heave_mm for p in planned):.2f} mm"
    )
    print(
        f"max crank waypoint: "
        f"{max(p.max_crank_delta_deg for p in planned):.2f}°"
    )
    print(
        f"minimum closure margin: "
        f"{min(p.closure_margin_mm for p in planned):.2f} mm"
    )
    print(
        f"worst advisory joint proxy: "
        f"{max(p.worst_advisory_joint_deg for p in planned):.2f}°"
    )
    print(
        f"minimum dead-center margin: "
        f"{min(p.dead_center_margin_deg for p in planned):.2f}°"
    )
    print(
        f"worst estimated static motor torque (50 lb): "
        f"{max(p.max_static_torque_nm for p in planned):.2f} N·m"
    )


def write_log(path: Path, planned: list[PoseSolution]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for index, pose in enumerate(planned):
        row = asdict(pose)
        row["index"] = index
        row["steps"] = pose.steps
        payload.append(row)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def envelope_map(args: argparse.Namespace) -> dict[str, object]:
    directions = []
    for direction in range(0, 360, args.direction_step):
        radians = math.radians(direction)
        last = 0.0
        heave_range = None
        radius = args.radius_step
        while radius <= args.envelope_max + 1e-9:
            roll = radius * math.sin(radians)
            pitch = radius * math.cos(radians)
            found = endpoint_heave_range(
                roll,
                pitch,
                heave_min_mm=args.heave_min,
                heave_max_mm=args.heave_max,
                heave_step_mm=args.heave_step,
            )
            if found is None:
                break
            last, heave_range = radius, found
            radius += args.radius_step
        directions.append(
            {
                "direction_deg": direction,
                "max_radius_deg": round(last, 6),
                "heave_range_mm": heave_range,
            }
        )
    return {
        "heave_min_mm": args.heave_min,
        "heave_max_mm": args.heave_max,
        "directions": directions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--circle", type=float, metavar="DEG")
    parser.add_argument("--target", type=float, nargs=2, metavar=("ROLL", "PITCH"))
    parser.add_argument("--cardinals", type=float, metavar="DEG")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-firmware", action="store_true")
    parser.add_argument("--envelope-map", type=Path)
    parser.add_argument("--envelope-max", type=float, default=20.0)
    parser.add_argument("--direction-step", type=int, default=5)
    parser.add_argument("--radius-step", type=float, default=0.25)
    parser.add_argument("--heave-min", type=float, default=-15.0)
    parser.add_argument("--heave-max", type=float, default=30.0)
    parser.add_argument("--heave-step", type=float, default=0.25)
    parser.add_argument("--max-heave-step", type=float, default=0.5)
    parser.add_argument("--max-crank-step", type=float, default=12.0)
    parser.add_argument("--ramp-points", type=int, default=80)
    parser.add_argument("--points", type=int, default=120)
    parser.add_argument("--period", type=float, default=30.0)
    parser.add_argument("--crank-speed", type=float, default=40.0)
    parser.add_argument("--crank-accel", type=float, default=120.0)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--disable-on-exit", action="store_true")
    args = parser.parse_args()

    selected = sum(
        value is not None
        for value in (args.circle, args.target, args.cardinals, args.envelope_map)
    ) + int(args.check_firmware)
    if selected != 1:
        parser.error(
            "select exactly one of --circle, --target, --cardinals, "
            "--envelope-map, --check-firmware"
        )
    if not 1.0 <= args.crank_speed <= 90.0:
        parser.error("--crank-speed must be in [1, 90] deg/s")
    if not 1.0 <= args.crank_accel <= 500.0:
        parser.error("--crank-accel must be in [1, 500] deg/s^2")

    if args.envelope_map is not None:
        payload = envelope_map(args)
        args.envelope_map.parent.mkdir(parents=True, exist_ok=True)
        args.envelope_map.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {args.envelope_map}")
        return 0

    if args.dry_run:
        planned = plan(args, calibrated_solution())
        summarize(planned)
        if args.log:
            write_log(args.log, planned)
            print(f"wrote {args.log}")
        return 0

    link = ExpLink(
        args.socket,
        mode="readonly" if args.check_firmware else "motion",
    )
    try:
        link.open()
        if not args.check_firmware:
            link.require_ok(
                f"PROFILE {args.crank_speed:.3f} {args.crank_accel:.3f}",
                "OK PROFILE",
            )
        status = link.startup_status
        assert status is not None
        print(status)
        if args.check_firmware:
            return 0
        if not status.calibrated:
            status = calibrate(link)

        initial = status.as_pose()
        planned = plan(args, initial)
        summarize(planned)
        if args.log:
            write_log(args.log, planned)
            print(f"wrote {args.log}")

        if not args.yes:
            confirmation = input(
                "Type MOVE to arm and execute this experimental trajectory: "
            )
            if confirmation != "MOVE":
                print("Cancelled; holding current state.")
                link.require_ok("HOLD")
                return 2

        link.require_ok("ARM CONFIRM", "OK ARM")
        interval = args.period / max(1, args.points)
        for index, pose in enumerate(planned, start=1):
            link.target(pose)
            print(
                f"\r{index}/{len(planned)} "
                f"r={pose.roll_deg:+6.2f} p={pose.pitch_deg:+6.2f} "
                f"h={pose.heave_mm:+6.2f}",
                end="",
                flush=True,
            )
            time.sleep(interval)
        print()
        link.wait_idle()
        link.require_ok("HOLD", "OK HOLD")
        print("Trajectory complete; holding final pose.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted; requesting ABORT/HOLD.")
        try:
            link.require_ok("ABORT", "OK ABORT")
        except Exception as exc:
            print(f"WARNING: abort failed: {exc}", file=sys.stderr)
        return 130
    except (NoSolutionError, RuntimeError, TimeoutError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        try:
            link.require_ok("ABORT", "OK ABORT")
        except Exception:
            pass
        return 1
    finally:
        if link.is_open:
            try:
                if not args.check_firmware:
                    if args.disable_on_exit:
                        link.require_ok("DISABLE", "OK DISABLE")
                    else:
                        link.require_ok("HOLD", "OK HOLD")
            except Exception as exc:
                print(f"WARNING: final hold/disable failed: {exc}", file=sys.stderr)
            link.close()


if __name__ == "__main__":
    raise SystemExit(main())
