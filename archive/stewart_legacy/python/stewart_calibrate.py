#!/usr/bin/env python3
"""Stewart platform calibration — interactive per-axis TUI (recommended).

Opens a full-screen terminal UI. For each leg (axis 0 → 1 → 2):

1. Enable that axis
2. Jog with arrow keys until the crank is **straight up / vertical by eye**
3. **Enter** records ``cal_axis N``
4. After all three: ``cal_finish``

Requires firmware with ``cal_begin`` / ``cal_axis`` / ``cal_finish``.
Opening serial resets the Uno — run after every reconnect.

Legacy one-shot (all cranks manually up first): ``--legacy``
"""

from __future__ import annotations

import argparse
import sys
import time

import serial

from stewart_cal_tui import run_interactive_calibration_tui
from stewart_serial import open_stewart_serial, wait_if_reset

DEFAULT_JOG_FINE = 200
DEFAULT_JOG_COARSE = 1600


def read_lines(ser: serial.Serial, seconds: float = 0.8) -> list[str]:
    end = time.time() + seconds
    lines: list[str] = []
    while time.time() < end:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()
        if line:
            lines.append(line)
            end = time.time() + 0.12
    return lines


def send(ser: serial.Serial, cmd: str, wait: float = 0.8) -> list[str]:
    ser.reset_input_buffer()
    ser.write((cmd + "\n").encode())
    ser.flush()
    lines = read_lines(ser, wait)
    for line in lines:
        print(f"  < {line}")
    return lines


def _ok(lines: list[str], prefix: str) -> bool:
    return any(line.startswith(prefix) for line in lines)


def run_legacy_calibration(ser: serial.Serial, *, skip_prompt: bool = False) -> bool:
    if not skip_prompt:
        print()
        print("Legacy calibrate: motors free, ALL cranks straight up manually.")
        print("Press Enter to send calibrate, Ctrl-C to abort.")
        try:
            input()
        except EOFError:
            print("No TTY — use --yes or interactive mode.", file=sys.stderr)
            return False

    lines = send(ser, "calibrate", wait=1.0)
    if not _ok(lines, "OK calibrate"):
        return False
    status = send(ser, "status", wait=0.6)
    return any("calibrated 1" in line for line in status)


def run_interactive_calibration(
    ser: serial.Serial,
    *,
    jog_fine: int = DEFAULT_JOG_FINE,
    jog_coarse: int = DEFAULT_JOG_COARSE,
    skip_intro: bool = False,
) -> bool:
    return run_interactive_calibration_tui(
        ser,
        jog_fine=jog_fine,
        jog_coarse=jog_coarse,
        skip_intro=skip_intro,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/arduino-stewart")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="legacy one-shot calibrate (all cranks manually up first)",
    )
    parser.add_argument("--yes", "-y", action="store_true", help="skip intro screen")
    parser.add_argument("--jog-fine", type=int, default=DEFAULT_JOG_FINE)
    parser.add_argument("--jog-coarse", type=int, default=DEFAULT_JOG_COARSE)
    args = parser.parse_args()

    print(f"Opening {args.port} …")
    try:
        ser = open_stewart_serial(args.port, args.baud, timeout=0.3)
    except serial.SerialException as exc:
        print(f"Cannot open port: {exc}", file=sys.stderr)
        return 1

    if wait_if_reset(ser):
        print("Board rebooted on serial open — waiting for firmware …")
        time.sleep(0.5)

    if args.legacy:
        send(ser, "status", wait=0.5)
        ok = run_legacy_calibration(ser, skip_prompt=args.yes)
        send(ser, "disable", wait=0.4)
        ser.close()
        if ok:
            print("\nCalibrated. enable / pose / roller_ball may proceed.")
            return 0
        print("\nCalibration did not complete.", file=sys.stderr)
        return 2

    # TUI takes over the terminal; minimal prints before wrapper.
    ok = run_interactive_calibration(
        ser,
        jog_fine=args.jog_fine,
        jog_coarse=args.jog_coarse,
        skip_intro=args.yes,
    )
    send(ser, "disable", wait=0.4)
    ser.close()
    if ok:
        print("Calibrated. enable / pose / roller_ball may proceed.")
        return 0
    print("Calibration did not complete.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
