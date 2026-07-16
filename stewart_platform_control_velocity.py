#!/usr/bin/env python3
"""Trackball angular-velocity control for the free-heave Stewart platform.

A swipe adds angular velocity (+X -> +pitch velocity, +Y -> +roll velocity).
Velocity decays exponentially to zero while its integral becomes the absolute
roll/pitch target.  The shared IK layer is free to select heave continuously.
"""

from __future__ import annotations

import argparse
import math
import sys
import time

from analysis.stewart_exp_kinematics import NoSolutionError
from stewart_platform_control_common import (
    StewartPlatformController,
    TrackballDevice,
    add_common_arguments,
    clamp_vector,
    confirm_motion,
    decay_velocity,
    integrate_velocity,
    resolve_trackball,
    run_loop_timing,
    validate_common_arguments,
)


def apply_velocity_counts(
    roll_velocity: float,
    pitch_velocity: float,
    dx: int,
    dy: int,
    *,
    velocity_per_count: float,
    roll_sign: float,
    pitch_sign: float,
    max_velocity_deg_s: float,
) -> tuple[float, float]:
    pitch_velocity += dx * velocity_per_count * pitch_sign
    roll_velocity += dy * velocity_per_count * roll_sign
    return clamp_vector(
        roll_velocity, pitch_velocity, max_velocity_deg_s
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--velocity-per-count", type=float, default=0.6)
    parser.add_argument("--velocity-decay-s", type=float, default=0.35)
    parser.add_argument("--max-velocity", type=float, default=30.0)
    parser.add_argument("--velocity-epsilon", type=float, default=0.02)
    parser.add_argument("--deadband", type=int, default=1)
    parser.add_argument("--roll-sign", type=float, choices=(-1.0, 1.0), default=1.0)
    parser.add_argument(
        "--pitch-sign", type=float, choices=(-1.0, 1.0), default=1.0
    )
    args = parser.parse_args()
    try:
        validate_common_arguments(args)
        if min(
            args.velocity_per_count,
            args.velocity_decay_s,
            args.max_velocity,
            args.velocity_epsilon,
        ) <= 0.0 or args.deadband < 0:
            raise ValueError(
                "velocity scales/limits must be positive and deadband >= 0"
            )
        device_path = resolve_trackball(args)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))

    if not confirm_motion(args, "decaying angular-velocity", device_path):
        return 2

    trackball = TrackballDevice(device_path)
    controller = StewartPlatformController(args)
    try:
        trackball.open()
        controller.open()
        assert controller.current is not None
        desired_roll = controller.current.roll_deg
        desired_pitch = controller.current.pitch_deg
        roll_velocity = 0.0
        pitch_velocity = 0.0
        last_update = time.monotonic()
        last_print = 0.0
        print("Live mapping: +X -> +pitch velocity, +Y -> +roll velocity.")

        while True:
            interval, timeout = run_loop_timing(last_update, args.rate_hz)
            trackball.wait(timeout)
            now = time.monotonic()
            dt = now - last_update
            if dt < interval:
                continue
            last_update = now
            dx, dy = trackball.pop()

            if abs(dx) > args.deadband or abs(dy) > args.deadband:
                roll_velocity, pitch_velocity = apply_velocity_counts(
                    roll_velocity,
                    pitch_velocity,
                    dx,
                    dy,
                    velocity_per_count=args.velocity_per_count,
                    roll_sign=args.roll_sign,
                    pitch_sign=args.pitch_sign,
                    max_velocity_deg_s=args.max_velocity,
                )

            desired_roll, desired_pitch, roll_velocity, pitch_velocity = (
                integrate_velocity(
                    desired_roll,
                    desired_pitch,
                    roll_velocity,
                    pitch_velocity,
                    dt,
                    args.max_tilt,
                )
            )
            roll_velocity, pitch_velocity = decay_velocity(
                roll_velocity,
                pitch_velocity,
                dt,
                args.velocity_decay_s,
            )
            if math.hypot(roll_velocity, pitch_velocity) < args.velocity_epsilon:
                roll_velocity = pitch_velocity = 0.0

            if not controller.at_target(desired_roll, desired_pitch):
                controller.command_toward(desired_roll, desired_pitch)

            if now - last_print >= 0.1:
                assert controller.current is not None
                print(
                    f"\rrequest r={desired_roll:+6.2f}° p={desired_pitch:+6.2f}° "
                    f"velocity r={roll_velocity:+6.2f} p={pitch_velocity:+6.2f}°/s "
                    f"IK h={controller.current.heave_mm:+6.2f} mm",
                    end="",
                    flush=True,
                )
                last_print = now
    except KeyboardInterrupt:
        print("\nStopping; holding the current platform position.")
        return 0
    except (NoSolutionError, RuntimeError, TimeoutError) as exc:
        print(f"\ncontrol error: {exc}", file=sys.stderr)
        return 1
    finally:
        controller.hold_and_close()
        trackball.close()


if __name__ == "__main__":
    raise SystemExit(main())
