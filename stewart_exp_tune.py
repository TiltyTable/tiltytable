#!/usr/bin/env python3
"""Interactive live tuning CLI for the experimental Stewart stack."""

from __future__ import annotations

import argparse
import math
import shlex
import time
from pathlib import Path

from analysis.stewart_exp_kinematics import (
    PoseSolution,
    linear_targets,
    plan_heave_transition,
    plan_targets,
)
from stewart_exp_probe import ExpLink, calibrate
from stewart_supervisor_client import DEFAULT_SOCKET

DIRECTIONS = {
    ("roll", "+"): ("roll_positive", 1.0),
    ("roll", "-"): ("roll_negative", -1.0),
    ("pitch", "+"): ("pitch_positive", 1.0),
    ("pitch", "-"): ("pitch_negative", -1.0),
}


def direction_key(axis: str, sign: str) -> tuple[str, float]:
    try:
        return DIRECTIONS[(axis.lower(), sign)]
    except KeyError as exc:
        raise ValueError("axis must be roll/pitch and sign must be +/-") from exc


class TuningSession:
    def __init__(self, link: ExpLink) -> None:
        self.link = link
        self.current: PoseSolution | None = None

    def refresh(self) -> None:
        self.current = self.link.status().as_pose()

    def ensure_armed(self) -> None:
        status = self.link.status()
        if not status.armed:
            self.link.require_ok("ARM CONFIRM", "OK ARM")

    def prepare_agile_pose(self) -> None:
        if self.current is None:
            self.refresh()
        assert self.current is not None
        if (
            self.current.heave_mm <= 5.0
            or abs(self.current.roll_deg) >= 0.1
            or abs(self.current.pitch_deg) >= 0.1
        ):
            return
        self.ensure_armed()
        planned = plan_heave_transition(self.current, 0.0)
        for pose in planned:
            self.link.target(pose)
        self.link.wait_idle()
        self.current = planned[-1]

    def set_profile(self, speed: float, accel: float) -> None:
        if not 1.0 <= speed <= 90.0:
            raise ValueError("speed must be in [1, 90] deg/s")
        if not 1.0 <= accel <= 500.0:
            raise ValueError("accel must be in [1, 500] deg/s^2")
        reply = self.link.require_ok(
            f"PROFILE {speed:.3f} {accel:.3f}", "OK PROFILE"
        )
        print(reply)

    def move_to(
        self,
        roll: float,
        pitch: float,
        *,
        increment_deg: float | None = None,
    ) -> float:
        if self.current is None:
            self.refresh()
        assert self.current is not None
        distance = math.hypot(
            roll - self.current.roll_deg, pitch - self.current.pitch_deg
        )
        if increment_deg is None:
            increment_deg = min(1.5, max(0.5, distance / 8.0))
        points = max(1, math.ceil(distance / increment_deg))
        targets = linear_targets(
            self.current.roll_deg,
            self.current.pitch_deg,
            roll,
            pitch,
            points,
        )
        planned = plan_targets(
            targets,
            initial=self.current,
            max_heave_step_mm=0.5,
            max_crank_step_deg=12.0,
            estimate_torque=False,
            objective="agile",
        )
        self.ensure_armed()
        started = time.monotonic()
        for pose in planned:
            self.link.target(pose)
        self.link.wait_idle()
        if planned:
            self.current = planned[-1]
        return time.monotonic() - started

    def level(self) -> float:
        return self.move_to(0.0, 0.0)

    def hold(self) -> None:
        self.link.require_ok("HOLD", "OK HOLD")
        self.refresh()

    def threshold_test(
        self,
        axis: str,
        sign: str,
        step_deg: float = 0.1,
        maximum_deg: float = 10.0,
    ) -> None:
        if step_deg <= 0:
            raise ValueError("threshold step must be positive")
        axis = axis.lower()
        key, direction = direction_key(axis, sign)
        print(f"Returning level for {key} threshold trial...")
        self.level()
        print(
            "Place the ball at the test location. At each angle: "
            "[n/Enter] next, [m] reliably rolling, [q] abort."
        )
        angle = 0.0
        while angle + step_deg <= maximum_deg + 1e-9:
            angle = round(angle + step_deg, 6)
            roll = direction * angle if axis == "roll" else 0.0
            pitch = direction * angle if axis == "pitch" else 0.0
            elapsed = self.move_to(roll, pitch)
            response = input(
                f"{key}: {angle:.3f}° (move {elapsed:.2f}s) "
                "[n=next m=rolling q=abort] "
            ).strip().lower()
            if response == "m":
                print(f"Observed {key} threshold at {angle:.3f}°.")
                self.level()
                self.hold()
                return
            if response == "q":
                self.hold()
                return
        print(f"No reliable roll observed by {maximum_deg:g}°.")
        self.hold()

    def agility_test(self, axis: str, amplitude_deg: float, cycles: int) -> None:
        axis = axis.lower()
        if axis not in ("roll", "pitch"):
            raise ValueError("axis must be roll or pitch")
        if amplitude_deg <= 0 or cycles <= 0:
            raise ValueError("amplitude and cycles must be positive")
        status = self.link.status()
        print(
            f"Agility test: {axis} ±{amplitude_deg:g}°, {cycles} cycles, "
            f"profile={status.max_speed_deg_s:g}°/s "
            f"{status.max_accel_deg_s2:g}°/s²"
        )
        self.level()
        samples: list[float] = []
        for cycle in range(cycles):
            for direction in (1.0, -1.0):
                roll = direction * amplitude_deg if axis == "roll" else 0.0
                pitch = direction * amplitude_deg if axis == "pitch" else 0.0
                elapsed = self.move_to(roll, pitch)
                samples.append(elapsed)
                print(
                    f"  cycle {cycle + 1} {'+' if direction > 0 else '-'}: "
                    f"{elapsed:.3f}s"
                )
        self.level()
        self.hold()
        print(
            f"Agility timing: mean={sum(samples) / len(samples):.3f}s "
            f"max={max(samples):.3f}s"
        )

    def recalibrate_motors(self) -> None:
        self.current = None
        status = calibrate(self.link)
        self.current = status.as_pose()
        print("Full crank calibration complete.")

    def print_status(self) -> None:
        print(self.link.status())


HELP = """Commands:
  status
  level
  pose <roll> <pitch>
  nudge <roll|pitch> <delta_deg>
  recalibrate
  profile [speed_deg_s accel_deg_s2]
  threshold <roll|pitch> <+|-> [step_deg]
  agility <roll|pitch> <amplitude_deg> <cycles>
  hold
  help
  quit
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    args = parser.parse_args()

    link = ExpLink(args.socket, mode="motion")
    session = TuningSession(link)
    try:
        link.open()
        status = link.startup_status
        assert status is not None
        if not status.calibrated:
            status = calibrate(link)
        session.current = status.as_pose()
        if (
            session.current.heave_mm > 5.0
            and abs(session.current.roll_deg) < 0.1
            and abs(session.current.pitch_deg) < 0.1
        ):
            confirmation = input(
                "Type PREPARE to move level from calibration heave to the "
                "agile operating heave: "
            )
            if confirmation == "PREPARE":
                session.prepare_agile_pose()
        print(HELP)
        while True:
            try:
                raw = input("stewart-tune> ").strip()
            except EOFError:
                raw = "quit"
            if not raw:
                continue
            parts = shlex.split(raw)
            command = parts[0].lower()
            try:
                if command in ("quit", "exit", "q"):
                    session.hold()
                    return 0
                if command == "help":
                    print(HELP)
                elif command == "status" and len(parts) == 1:
                    session.print_status()
                elif command == "level" and len(parts) == 1:
                    print(f"level move: {session.level():.3f}s")
                elif command == "pose" and len(parts) == 3:
                    print(
                        f"pose move: "
                        f"{session.move_to(float(parts[1]), float(parts[2])):.3f}s"
                    )
                elif command == "nudge" and len(parts) == 3:
                    if session.current is None:
                        session.refresh()
                    assert session.current is not None
                    delta = float(parts[2])
                    roll = session.current.roll_deg
                    pitch = session.current.pitch_deg
                    if parts[1] == "roll":
                        roll += delta
                    elif parts[1] == "pitch":
                        pitch += delta
                    else:
                        raise ValueError("axis must be roll or pitch")
                    session.move_to(roll, pitch)
                elif command == "recalibrate" and len(parts) == 1:
                    confirmation = input(
                        "This disables all motors before jogging each crank. "
                        "Support the table, then type RECALIBRATE: "
                    )
                    if confirmation == "RECALIBRATE":
                        session.recalibrate_motors()
                    else:
                        print("Recalibration cancelled.")
                elif command == "profile":
                    if len(parts) == 1:
                        print(link.require_ok("PROFILE?", "OK PROFILE"))
                    elif len(parts) == 3:
                        session.set_profile(float(parts[1]), float(parts[2]))
                    else:
                        raise ValueError("profile [speed accel]")
                elif command == "threshold" and len(parts) in (3, 4):
                    session.threshold_test(
                        parts[1],
                        parts[2],
                        float(parts[3]) if len(parts) == 4 else 0.1,
                    )
                elif command == "agility" and len(parts) == 4:
                    session.agility_test(
                        parts[1], float(parts[2]), int(parts[3])
                    )
                elif command == "hold" and len(parts) == 1:
                    session.hold()
                else:
                    print("Unknown or malformed command. Type help.")
            except Exception as exc:
                print(f"ERROR: {exc}")
                try:
                    session.hold()
                except Exception:
                    pass
    except KeyboardInterrupt:
        print("\nInterrupted; holding.")
        try:
            session.hold()
        except Exception:
            pass
        return 130
    finally:
        if link.is_open:
            try:
                session.hold()
            except Exception:
                pass
            link.close()


if __name__ == "__main__":
    raise SystemExit(main())
