#!/usr/bin/env python3
"""Trackball position control for the free-heave Stewart platform.

Linux REL_X counts directly increase/decrease pitch and REL_Y counts directly
increase/decrease roll.  The requested roll/pitch is an absolute pose target;
the shared IK layer is free to select heave continuously.
"""

from __future__ import annotations

import argparse
import sys
import time

from analysis.stewart_exp_kinematics import NoSolutionError
from stewart_platform_control_common import (
    StewartPlatformController,
    TrackballDevice,
    add_common_arguments,
    clamp_vector,
    confirm_motion,
    resolve_trackball,
    run_loop_timing,
    validate_common_arguments,
)


def apply_position_counts(
    roll: float,
    pitch: float,
    dx: int,
    dy: int,
    *,
    degrees_per_count: float,
    roll_sign: float,
    pitch_sign: float,
    max_tilt_deg: float,
) -> tuple[float, float]:
    pitch += dx * degrees_per_count * pitch_sign
    roll += dy * degrees_per_count * roll_sign
    return clamp_vector(roll, pitch, max_tilt_deg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--degrees-per-count", type=float, default=0.04)
    parser.add_argument("--deadband", type=int, default=1)
    parser.add_argument("--roll-sign", type=float, choices=(-1.0, 1.0), default=1.0)
    parser.add_argument(
        "--pitch-sign", type=float, choices=(-1.0, 1.0), default=1.0
    )
    args = parser.parse_args()
    try:
        validate_common_arguments(args)
        if args.degrees_per_count <= 0.0 or args.deadband < 0:
            raise ValueError("position scale must be positive and deadband >= 0")
        device_path = resolve_trackball(args)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))

    if not confirm_motion(args, "direct roll/pitch", device_path):
        return 2

    trackball = TrackballDevice(device_path)
    controller = StewartPlatformController(args)
    try:
        trackball.open()
        controller.open()
        assert controller.current is not None
        desired_roll = controller.current.roll_deg
        desired_pitch = controller.current.pitch_deg
        last_update = time.monotonic()
        last_print = 0.0
        print("Live mapping: +X -> +pitch, +Y -> +roll. Ctrl-C holds.")

        while True:
            interval, timeout = run_loop_timing(last_update, args.rate_hz)
            trackball.wait(timeout)
            now = time.monotonic()
            if now - last_update < interval:
                continue
            last_update = now
            dx, dy = trackball.pop()

            if abs(dx) > args.deadband or abs(dy) > args.deadband:
                desired_roll, desired_pitch = apply_position_counts(
                    desired_roll,
                    desired_pitch,
                    dx,
                    dy,
                    degrees_per_count=args.degrees_per_count,
                    roll_sign=args.roll_sign,
                    pitch_sign=args.pitch_sign,
                    max_tilt_deg=args.max_tilt,
                )

            if not controller.at_target(desired_roll, desired_pitch):
                controller.command_toward(desired_roll, desired_pitch)

            if now - last_print >= 0.1:
                assert controller.current is not None
                print(
                    f"\rrequest r={desired_roll:+6.2f}° p={desired_pitch:+6.2f}° "
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
