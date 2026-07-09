#!/usr/bin/env python3
"""Stewart platform calibration helper.

Physical procedure
------------------
1. Motors must be free (firmware disables them during calibrate).
2. Manually rotate all three cranks so they point STRAIGHT UP
   (maximum heave / highest platform).
3. Run this script (or send `calibrate` over serial).

That pose becomes the firmware's step + heave reference. Until then,
enable / pose / jog / etc. are rejected.
"""

from __future__ import annotations

import argparse
import sys
import time

import serial


def read_lines(ser: serial.Serial, seconds: float = 1.0) -> list[str]:
    end = time.time() + seconds
    lines: list[str] = []
    while time.time() < end:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()
        if line:
            lines.append(line)
    return lines


def send(ser: serial.Serial, cmd: str, wait: float = 0.8) -> list[str]:
    ser.reset_input_buffer()
    ser.write((cmd + "\n").encode())
    ser.flush()
    return read_lines(ser, wait)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        default="/dev/arduino-stewart",
        help="Stewart Uno R3 serial port",
    )
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip interactive confirmation (cranks already straight up)",
    )
    args = parser.parse_args()

    print(f"Opening {args.port} …")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.3)
    except serial.SerialException as exc:
        print(f"Cannot open port: {exc}", file=sys.stderr)
        return 1

    time.sleep(2.2)  # USB reset
    boot = read_lines(ser, 1.0)
    for line in boot:
        print(f"  < {line}")

    status = send(ser, "status")
    for line in status:
        print(f"  < {line}")

    if not args.yes:
        print()
        print("1. Confirm motors are free / disabled.")
        print("2. Manually point ALL three cranks STRAIGHT UP (max heave).")
        print("3. Press Enter to send: calibrate")
        try:
            input()
        except EOFError:
            print("No TTY — re-run with --yes once cranks are up.", file=sys.stderr)
            ser.close()
            return 2

    print("> calibrate")
    replies = send(ser, "calibrate")
    for line in replies:
        print(f"  < {line}")

    ok = any(line.startswith("OK calibrate") for line in replies)
    status2 = send(ser, "status")
    for line in status2:
        print(f"  < {line}")
    calibrated = any("calibrated 1" in line for line in status2)

    ser.close()
    if ok and calibrated:
        print("Calibrated. You can now enable / pose (motors will hold).")
        return 0
    print("Calibration did not confirm — check firmware / serial.", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
