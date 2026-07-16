#!/usr/bin/env python3
"""Interactive game-feel tuning CLI for the experimental Stewart stack."""

from __future__ import annotations

import argparse
import json
import math
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from analysis.stewart_exp_kinematics import (
    PoseSolution,
    STEPS_PER_CRANK_REV,
    linear_targets,
    plan_heave_transition,
    plan_targets,
    steps_to_crank_deg,
)
from stewart_exp_probe import ExpLink, calibrate, read_key
from stewart_supervisor_client import DEFAULT_SOCKET

DEFAULT_RESULTS = Path("calibration/stewart_game_tuning.json")
MAX_TARGET_JUMP_DEG = 12.0
MAX_TARGET_JUMP_STEPS = round(
    MAX_TARGET_JUMP_DEG * STEPS_PER_CRANK_REV / 360.0
)
DIRECTIONS = {
    ("roll", "+"): ("roll_positive", 1.0),
    ("roll", "-"): ("roll_negative", -1.0),
    ("pitch", "+"): ("pitch_positive", 1.0),
    ("pitch", "-"): ("pitch_negative", -1.0),
}


@dataclass
class TuningResults:
    thresholds: dict[str, list[float]] = field(default_factory=dict)
    profiles: list[dict[str, object]] = field(default_factory=list)
    recommended_activation_deg: float | None = None
    selected_profile: dict[str, float] | None = None
    level_trim_roll_deg: float = 0.0
    level_trim_pitch_deg: float = 0.0
    motor_trim_steps: list[int] = field(default_factory=lambda: [0, 0, 0])
    level_anchor_steps: list[int] | None = None
    level_anchor_model: dict[str, object] | None = None

    @classmethod
    def load(cls, path: Path) -> "TuningResults":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        level_anchor_steps = raw.get("level_anchor_steps")
        if level_anchor_steps is not None:
            if not isinstance(level_anchor_steps, list) or len(level_anchor_steps) != 3:
                raise ValueError("level_anchor_steps must contain three values")
            level_anchor_steps = [int(value) for value in level_anchor_steps]
        level_anchor_model = raw.get("level_anchor_model")
        if level_anchor_model is not None and not isinstance(level_anchor_model, dict):
            raise ValueError("level_anchor_model must be an object")
        return cls(
            thresholds={
                key: [float(value) for value in values]
                for key, values in raw.get("thresholds", {}).items()
            },
            profiles=list(raw.get("profiles", [])),
            recommended_activation_deg=raw.get("recommended_activation_deg"),
            selected_profile=raw.get("selected_profile"),
            level_trim_roll_deg=float(raw.get("level_trim_roll_deg", 0.0)),
            level_trim_pitch_deg=float(raw.get("level_trim_pitch_deg", 0.0)),
            motor_trim_steps=[
                int(value) for value in raw.get("motor_trim_steps", [0, 0, 0])
            ],
            level_anchor_steps=level_anchor_steps,
            level_anchor_model=level_anchor_model,
        )

    def recompute_recommendation(self, margin_deg: float = 0.1) -> float | None:
        values = [
            value for trials in self.thresholds.values() for value in trials
        ]
        self.recommended_activation_deg = (
            round(max(values) + margin_deg, 3) if values else None
        )
        return self.recommended_activation_deg

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "thresholds": self.thresholds,
                    "recommended_activation_deg": self.recommended_activation_deg,
                    "selected_profile": self.selected_profile,
                    "level_trim_roll_deg": self.level_trim_roll_deg,
                    "level_trim_pitch_deg": self.level_trim_pitch_deg,
                    "motor_trim_steps": self.motor_trim_steps,
                    "level_anchor_steps": self.level_anchor_steps,
                    "level_anchor_model": self.level_anchor_model,
                    "profiles": self.profiles,
                },
                indent=2,
            )
            + "\n"
        )

    def clear_motion_calibration(self) -> None:
        """Clear state whose absolute meaning changes after crank calibration."""
        self.level_trim_roll_deg = 0.0
        self.level_trim_pitch_deg = 0.0
        self.motor_trim_steps = [0, 0, 0]
        self.level_anchor_steps = None
        self.level_anchor_model = None

    def anchor_pose(self) -> PoseSolution | None:
        if self.level_anchor_steps is None or self.level_anchor_model is None:
            return None
        model_steps_raw = self.level_anchor_model.get("model_steps")
        if not isinstance(model_steps_raw, list) or len(model_steps_raw) != 3:
            raise ValueError("level_anchor_model.model_steps must contain 3 values")
        model_steps = tuple(int(value) for value in model_steps_raw)
        return PoseSolution(
            roll_deg=float(self.level_anchor_model["roll_deg"]),
            pitch_deg=float(self.level_anchor_model["pitch_deg"]),
            heave_mm=float(self.level_anchor_model["heave_mm"]),
            crank_deg=tuple(steps_to_crank_deg(value) for value in model_steps),
            branch_index=tuple(
                int(value)
                for value in self.level_anchor_model.get(
                    "branch_index", [0, 0, 0]
                )
            ),
            closure_margin_mm=0.0,
            worst_advisory_joint_deg=0.0,
            max_crank_delta_deg=0.0,
            dead_center_margin_deg=0.0,
            max_static_torque_nm=0.0,
        )

    def game_origin(self) -> tuple[float, float]:
        anchor = self.anchor_pose()
        if anchor is not None:
            return anchor.roll_deg, anchor.pitch_deg
        return self.level_trim_roll_deg, self.level_trim_pitch_deg


def absolute_step_waypoints(
    start_steps: tuple[int, int, int],
    end_steps: tuple[int, int, int],
    max_jump_steps: int = MAX_TARGET_JUMP_STEPS,
) -> list[tuple[int, int, int]]:
    """Interpolate exact integer endpoints within the firmware jump limit."""
    if max_jump_steps <= 0:
        raise ValueError("max_jump_steps must be positive")
    largest_delta = max(
        abs(end_steps[axis] - start_steps[axis]) for axis in range(3)
    )
    count = max(1, math.ceil(largest_delta / max_jump_steps))
    return [
        tuple(
            start_steps[axis]
            + round((end_steps[axis] - start_steps[axis]) * index / count)
            for axis in range(3)
        )
        for index in range(1, count + 1)
    ]


def direction_key(axis: str, sign: str) -> tuple[str, float]:
    try:
        return DIRECTIONS[(axis.lower(), sign)]
    except KeyError as exc:
        raise ValueError("axis must be roll/pitch and sign must be +/-") from exc


def clear_after_fresh_crank_calibration(
    results: TuningResults,
    path: Path,
) -> None:
    results.clear_motion_calibration()
    results.save(path)


class TuningSession:
    def __init__(
        self,
        link: ExpLink,
        results: TuningResults,
        results_path: Path,
        threshold_margin_deg: float = 0.1,
    ) -> None:
        self.link = link
        self.results = results
        self.results_path = results_path
        self.threshold_margin_deg = threshold_margin_deg
        self.current: PoseSolution | None = None

    def refresh(self) -> None:
        status = self.link.status()
        self.current = status.as_pose(tuple(self.results.motor_trim_steps))

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
        offsets = tuple(self.results.motor_trim_steps)
        for pose in planned:
            self.link.target(pose, offsets)
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
            offsets = tuple(self.results.motor_trim_steps)
            self.link.target(pose, offsets)
        self.link.wait_idle()
        elapsed = time.monotonic() - started
        if planned:
            self.current = planned[-1]
        return elapsed

    def capture_level_anchor(self) -> None:
        """Persist the current absolute physical level and its IK reference."""
        status = self.link.status()
        trims = tuple(self.results.motor_trim_steps)
        model_steps = [
            status.steps[axis] - trims[axis] for axis in range(3)
        ]
        branch_index = (
            list(self.current.branch_index)
            if self.current is not None
            else [0, 0, 0]
        )
        self.results.level_anchor_steps = list(status.steps)
        self.results.level_anchor_model = {
            "roll_deg": status.roll_deg,
            "pitch_deg": status.pitch_deg,
            "heave_mm": status.heave_mm,
            "model_steps": model_steps,
            "branch_index": branch_index,
        }
        self.results.level_trim_roll_deg = status.roll_deg
        self.results.level_trim_pitch_deg = status.pitch_deg
        self.results.save(self.results_path)
        self.current = status.as_pose(trims)

    def move_to_level_anchor(self) -> float:
        """Return to the exact physical-level steps without invoking IK."""
        anchor = self.results.anchor_pose()
        if anchor is None or self.results.level_anchor_steps is None:
            raise RuntimeError("no canonical physical level anchor is saved")
        status = self.link.status()
        target_steps = tuple(self.results.level_anchor_steps)
        waypoints = absolute_step_waypoints(status.targets, target_steps)
        self.ensure_armed()
        started = time.monotonic()
        count = len(waypoints)
        for index, steps in enumerate(waypoints, start=1):
            fraction = index / count
            roll = status.roll_deg + (anchor.roll_deg - status.roll_deg) * fraction
            pitch = (
                status.pitch_deg
                + (anchor.pitch_deg - status.pitch_deg) * fraction
            )
            heave = status.heave_mm + (anchor.heave_mm - status.heave_mm) * fraction
            self.link.require_ok(
                "TARGET "
                f"{steps[0]} {steps[1]} {steps[2]} "
                f"{roll:.5f} {pitch:.5f} {heave:.5f}",
                "OK TARGET",
            )
        final_status = self.link.wait_idle()
        if final_status.steps != target_steps:
            raise RuntimeError(
                "level anchor endpoint mismatch: "
                f"expected {target_steps}, got {final_status.steps}"
            )
        self.current = anchor
        return time.monotonic() - started

    def level(self) -> float:
        if self.results.level_anchor_steps is not None:
            return self.move_to_level_anchor()
        return self.move_to(
            self.results.level_trim_roll_deg,
            self.results.level_trim_pitch_deg,
        )

    def move_game_to(self, roll: float, pitch: float) -> float:
        """Move in game coordinates relative to the stored physical-level trim."""
        origin_roll, origin_pitch = self.results.game_origin()
        return self.move_to(
            origin_roll + roll,
            origin_pitch + pitch,
        )

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
            elapsed = self.move_game_to(roll, pitch)
            response = input(
                f"{key}: {angle:.3f}° (move {elapsed:.2f}s) "
                "[n=next m=rolling q=abort] "
            ).strip().lower()
            if response == "m":
                self.record_threshold(axis, sign, angle)
                self.level()
                self.hold()
                return
            if response == "q":
                self.hold()
                return
        print(f"No reliable roll observed by {maximum_deg:g}°.")
        self.hold()

    def record_threshold(self, axis: str, sign: str, angle_deg: float) -> None:
        if angle_deg <= 0:
            raise ValueError("threshold angle must be positive")
        key, _ = direction_key(axis.lower(), sign)
        self.results.thresholds.setdefault(key, []).append(float(angle_deg))
        recommended = self.results.recompute_recommendation(
            self.threshold_margin_deg
        )
        self.results.save(self.results_path)
        print(
            f"Recorded {key}={angle_deg:.3f}°. "
            f"Conservative activation={recommended:.3f}° "
            f"(margin {self.threshold_margin_deg:.3f}°)"
        )

    def select_current_profile(self) -> None:
        status = self.link.status()
        self.results.selected_profile = {
            "speed_deg_s": status.max_speed_deg_s,
            "accel_deg_s2": status.max_accel_deg_s2,
        }
        self.results.save(self.results_path)
        print(f"Selected game profile: {self.results.selected_profile}")

    def agility_test(
        self,
        axis: str,
        amplitude_deg: float,
        cycles: int,
    ) -> None:
        axis = axis.lower()
        if axis not in ("roll", "pitch"):
            raise ValueError("axis must be roll or pitch")
        if amplitude_deg <= 0 or cycles <= 0:
            raise ValueError("amplitude and cycles must be positive")
        status = self.link.status()
        speed, accel = status.max_speed_deg_s, status.max_accel_deg_s2
        print(
            f"Agility test: {axis} ±{amplitude_deg:g}°, {cycles} cycles, "
            f"profile={speed:g}°/s {accel:g}°/s²"
        )
        self.level()
        samples: list[float] = []
        for cycle in range(cycles):
            for direction in (1.0, -1.0):
                target_roll = direction * amplitude_deg if axis == "roll" else 0.0
                target_pitch = (
                    direction * amplitude_deg if axis == "pitch" else 0.0
                )
                elapsed = self.move_game_to(target_roll, target_pitch)
                samples.append(elapsed)
                print(
                    f"  cycle {cycle + 1} {'+' if direction > 0 else '-'}: "
                    f"{elapsed:.3f}s"
                )
        self.level()
        self.hold()
        rating_text = input("Operator agility rating 1–5 (blank to skip): ").strip()
        notes = input("Observations (optional): ").strip()
        record: dict[str, object] = {
            "timestamp": time.time(),
            "speed_deg_s": speed,
            "accel_deg_s2": accel,
            "axis": axis,
            "amplitude_deg": amplitude_deg,
            "cycles": cycles,
            "move_times_s": samples,
            "mean_move_time_s": sum(samples) / len(samples),
            "max_move_time_s": max(samples),
            "notes": notes,
        }
        if rating_text:
            rating = int(rating_text)
            if not 1 <= rating <= 5:
                raise ValueError("rating must be 1–5")
            record["rating"] = rating
        self.results.profiles.append(record)
        self.results.save(self.results_path)
        print(f"Saved profile result to {self.results_path}")

    def print_status(self) -> None:
        status = self.link.status()
        print(status)
        print(
            f"recommended activation: "
            f"{self.results.recommended_activation_deg}"
        )
        print(
            f"physical-level trim: roll={self.results.level_trim_roll_deg:+.3f}° "
            f"pitch={self.results.level_trim_pitch_deg:+.3f}°"
        )
        print(f"physical-level anchor: {self.results.level_anchor_steps}")

    def mark_level_trim(self) -> None:
        if self.current is None:
            self.refresh()
        assert self.current is not None
        self.capture_level_anchor()
        assert self.current is not None
        print(
            "Marked current absolute pose as physical level: "
            f"roll={self.current.roll_deg:+.3f}° "
            f"pitch={self.current.pitch_deg:+.3f}° "
            f"steps={self.results.level_anchor_steps}"
        )

    def clear_level_trim(self) -> None:
        self.results.level_trim_roll_deg = 0.0
        self.results.level_trim_pitch_deg = 0.0
        self.results.level_anchor_steps = None
        self.results.level_anchor_model = None
        self.results.save(self.results_path)
        print("Cleared physical-level trim and canonical anchor.")

    def clear_motor_trims(self) -> None:
        self.results.motor_trim_steps = [0, 0, 0]
        self.results.level_anchor_steps = None
        self.results.level_anchor_model = None
        self.results.save(self.results_path)
        print("Cleared per-motor trims and dependent level anchor.")

    def recalibrate_motors(self) -> None:
        """Clear motion calibration and run full live crank-zero calibration."""
        self.results.clear_motion_calibration()
        self.results.save(self.results_path)
        self.current = None
        status = calibrate(self.link)
        self.current = status.as_pose()
        print(
            "Full crank calibration complete; cleared motor trims, level "
            "trim, and level anchor."
        )

    def adjust_motor(self, axis: int, pulses: int) -> None:
        if axis not in (0, 1, 2):
            raise ValueError("motor axis must be 0, 1, or 2")
        if abs(pulses) > 500:
            raise ValueError("single motor trim is limited to ±500 pulses")
        if self.current is None:
            self.refresh()
        assert self.current is not None
        self.ensure_armed()
        status = self.link.status()
        target_steps = list(status.targets)
        target_steps[axis] += pulses
        reply = self.link.require_ok(
            "TARGET "
            f"{target_steps[0]} {target_steps[1]} {target_steps[2]} "
            f"{self.current.roll_deg:.5f} {self.current.pitch_deg:.5f} "
            f"{self.current.heave_mm:.5f}",
            "OK TARGET",
        )
        self.link.wait_idle()
        self.results.motor_trim_steps[axis] += pulses
        self.results.save(self.results_path)
        self.refresh()
        print(
            f"{reply}; motor trims={self.results.motor_trim_steps}"
        )

    def motor_calibration(self, selected_axis: int | None = None) -> None:
        axes = [selected_axis] if selected_axis is not None else [0, 1, 2]
        self.level()
        self.results.level_anchor_steps = None
        self.results.level_anchor_model = None
        self.results.save(self.results_path)
        print(
            "LIVE MOTOR LEVEL TRIM — other motors remain energized. "
            "←/→ or -/+ = 20 pulses; ↓/↑ or B/F = 100; Enter=next; q=hold/abort."
        )
        for axis in axes:
            if axis not in (0, 1, 2):
                raise ValueError("motor axis must be 0, 1, or 2")
            print(f"\nMotor {axis}: adjust table joint height, Enter when satisfied.")
            while True:
                key = read_key()
                if key in ("q", "Q", "\x03"):
                    self.hold()
                    return
                if key in ("\r", "\n"):
                    print(f"  motor {axis} trim accepted")
                    break
                pulses = {
                    "\x1b[C": 20,
                    "\x1b[D": -20,
                    "\x1b[A": 100,
                    "\x1b[B": -100,
                    "+": 20,
                    "-": -20,
                    "F": 100,
                    "f": 100,
                    "B": -100,
                    "b": -100,
                }.get(key)
                if pulses is not None:
                    self.adjust_motor(axis, pulses)
        self.capture_level_anchor()
        print(
            "Captured canonical physical level anchor: "
            f"{self.results.level_anchor_steps}"
        )
        self.hold()


HELP = """Commands:
  status
  level
  pose <roll> <pitch>
  nudge <roll|pitch> <delta_deg>
  trim level
  trim clear
  motor <0|1|2> <pulses>
  motorcal [0|1|2]
  motorclear
  recalibrate
  profile [speed_deg_s accel_deg_s2]
  threshold <roll|pitch> <+|-> [step_deg]
  mark <roll|pitch> <+|-> <angle_deg>
  agility <roll|pitch> <amplitude_deg> <cycles>
  profile select
  hold
  save
  help
  quit
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--threshold-margin", type=float, default=0.1)
    args = parser.parse_args()
    if args.threshold_margin < 0:
        parser.error("--threshold-margin must be >= 0")

    link = ExpLink(
        args.socket,
        mode="motion",
    )
    results = TuningResults.load(args.results)
    session = TuningSession(
        link,
        results,
        args.results,
        threshold_margin_deg=args.threshold_margin,
    )
    try:
        link.open()
        status = link.startup_status
        assert status is not None
        if not status.calibrated:
            clear_after_fresh_crank_calibration(results, args.results)
            print("Cleared stale trims and level anchor before crank calibration.")
            status = calibrate(link)
        elif (
            not status.restored
            and status.steps == (0, 0, 0)
            and abs(status.heave_mm - 30.0) < 0.01
            and (
                any(results.motor_trim_steps)
                or results.level_anchor_steps is not None
            )
        ):
            clear_after_fresh_crank_calibration(results, args.results)
            print("Fresh zero-step calibration cleared stale trims and level anchor.")
        session.current = status.as_pose(tuple(results.motor_trim_steps))
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
                elif command == "status":
                    session.print_status()
                elif command == "level":
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
                elif command == "trim" and parts[1:] == ["level"]:
                    session.mark_level_trim()
                elif command == "trim" and parts[1:] == ["clear"]:
                    session.clear_level_trim()
                elif command == "motor" and len(parts) == 3:
                    session.adjust_motor(int(parts[1]), int(parts[2]))
                elif command == "motorcal" and len(parts) in (1, 2):
                    session.motor_calibration(
                        int(parts[1]) if len(parts) == 2 else None
                    )
                elif command == "motorclear" and len(parts) == 1:
                    session.clear_motor_trims()
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
                    elif len(parts) == 2 and parts[1] == "select":
                        session.select_current_profile()
                    elif len(parts) == 3:
                        session.set_profile(float(parts[1]), float(parts[2]))
                    else:
                        raise ValueError("profile [speed accel] | profile select")
                elif command == "threshold" and len(parts) in (3, 4):
                    session.threshold_test(
                        parts[1],
                        parts[2],
                        float(parts[3]) if len(parts) == 4 else 0.1,
                    )
                elif command == "mark" and len(parts) == 4:
                    session.record_threshold(
                        parts[1], parts[2], float(parts[3])
                    )
                elif command == "agility" and len(parts) == 4:
                    session.agility_test(
                        parts[1], float(parts[2]), int(parts[3])
                    )
                elif command == "hold":
                    session.hold()
                elif command == "save":
                    results.save(args.results)
                    print(f"saved {args.results}")
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
