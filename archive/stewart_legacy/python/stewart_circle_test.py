#!/usr/bin/env python3
"""Supervised Stewart circular range-of-motion test.

The platform normal traces a circle at fixed heave using absolute position
commands. Default radius is the modeled-safe 4.6° all-direction envelope.
No motion occurs until the interactive confirmation is accepted.
"""

from __future__ import annotations

import argparse
import math
import sys
import time

import serial

from roller_ball import (
    MAX_TILT_DEG,
    OPERATING_HEAVE_MM,
    Stewart,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/arduino-stewart")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--radius", type=float, default=MAX_TILT_DEG)
    parser.add_argument("--heave", type=float, default=OPERATING_HEAVE_MM)
    parser.add_argument("--period", type=float, default=8.0, help="seconds per circle")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--points", type=int, default=120)
    parser.add_argument("--legacy-cal", action="store_true")
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument(
        "--allow-experimental-radius",
        action="store_true",
        help="allow radius above the modeled-safe 4.6° envelope",
    )
    parser.add_argument(
        "--disable-on-exit",
        action="store_true",
        help="release motors on exit (default: hold and persist level pose)",
    )
    args = parser.parse_args()

    if args.radius <= 0 or args.period <= 0 or args.cycles <= 0 or args.points < 12:
        parser.error("radius/period/cycles must be positive; points must be >= 12")
    if args.radius > MAX_TILT_DEG and not args.allow_experimental_radius:
        parser.error(
            f"radius above {MAX_TILT_DEG:g}° requires --allow-experimental-radius"
        )

    print("STEWART CIRCULAR RANGE TEST")
    print(
        f"radius={args.radius:g}° heave={args.heave:g} mm "
        f"period={args.period:g}s cycles={args.cycles}"
    )
    if args.radius > MAX_TILT_DEG:
        print(
            "WARNING: radius exceeds the modeled all-direction envelope; "
            "firmware IK may reject poses."
        )
    print("Keep the 50 lb table mechanically protected against reset or power loss.")
    if not args.yes:
        input("Press Enter to calibrate/restore and begin (Ctrl-C aborts) … ")

    stewart = Stewart(args.port, args.baud, verbose=False)
    try:
        stewart.open()
        _, _, active_heave = stewart.bring_up(
            args.heave, legacy_cal=args.legacy_cal
        )

        interval = args.period / args.points
        total_points = args.points * args.cycles
        pending_error = ""
        for index in range(total_points):
            phase = 2.0 * math.pi * index / args.points
            roll = args.radius * math.sin(phase)
            pitch = args.radius * math.cos(phase)
            feedback = stewart.pose(roll, pitch, active_heave)
            if "ERR pose" in feedback:
                pending_error = feedback
                raise RuntimeError(f"firmware rejected circle pose: {feedback}")
            print(
                f"\rroll={roll:+5.2f}° pitch={pitch:+5.2f}° "
                f"point={index + 1}/{total_points}",
                end="",
                flush=True,
            )
            time.sleep(interval)

        print("\nReturning level …")
        feedback = stewart.pose(0.0, 0.0, active_heave)
        if "ERR pose" in feedback:
            pending_error = feedback
        stewart.wait_idle()
        if pending_error:
            raise RuntimeError(pending_error)
        print("Circle complete.")
        return 0
    except KeyboardInterrupt:
        print("\nCircle test interrupted.")
        return 130
    except (RuntimeError, serial.SerialException) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            if args.disable_on_exit:
                stewart.disable()
            else:
                stewart.hold()
        except Exception as exc:
            print(f"WARNING: shutdown command failed: {exc}", file=sys.stderr)
        stewart.close()


if __name__ == "__main__":
    raise SystemExit(main())
