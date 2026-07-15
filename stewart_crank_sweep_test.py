#!/usr/bin/env python3
"""Bare crank sweep test — no Stewart top required.

Use this after flashing uim5756pm_stewart when you only have the three motor
assemblies on the bench (no platform / Heim rods).

Physical setup
--------------
1. Leave motors **disabled** (default after boot).
2. Manually rotate **all three cranks straight up** (pin at highest point).
3. Run this script.

It sends ``calibrate`` (records that pose as the reference), enables the
drivers, commands a **90° crank sweep to horizontal** (firmware neutral
``NEUTRAL_CRANK_DEG = 180°``), waits for the move to finish, then disables.

Firmware ``angle`` targets are crank **deltas from horizontal** (deg):
  straight up (calibrated pose) ≈ -90
  horizontal (neutral)            = 0

Examples
--------
  .venv/bin/python3 stewart_crank_sweep_test.py
  .venv/bin/python3 stewart_crank_sweep_test.py --yes
  .venv/bin/python3 stewart_crank_sweep_test.py --axis 0   # one motor only
"""

from __future__ import annotations

import argparse
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit(
        "pyserial is required. Use the project venv:\n"
        "  .venv/bin/python3 stewart_crank_sweep_test.py"
    )

from stewart_serial import open_stewart_serial, wait_if_reset

CALIBRATED_UP_DEG = -90.0
HORIZONTAL_DEG = 0.0


def read_lines(ser: serial.Serial, seconds: float = 0.6) -> list[str]:
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


def send(ser: serial.Serial, cmd: str, wait: float = 0.6) -> list[str]:
    ser.reset_input_buffer()
    ser.write((cmd + "\n").encode())
    ser.flush()
    return read_lines(ser, wait)


def parse_status(lines: list[str]) -> dict[str, str | int | float | bool]:
    text = " ".join(lines)
    out: dict[str, str | int | float | bool] = {}
    m = re.search(r"calibrated\s+(\d)", text)
    if m:
        out["calibrated"] = m.group(1) == "1"
    m = re.search(r"enabled\s+(\d)", text)
    if m:
        out["enabled"] = m.group(1) == "1"
    m = re.search(r"moving\s+(\d)", text)
    if m:
        out["moving"] = m.group(1) == "1"
    for i in range(3):
        m = re.search(rf"axis{i}_deg\s+([-0-9.]+)", text)
        if m:
            out[f"axis{i}_deg"] = float(m.group(1))
    return out


def wait_idle(ser: serial.Serial, timeout_s: float = 120.0) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        lines = send(ser, "status", wait=0.35)
        for line in lines:
            print(f"  < {line}")
        st = parse_status(lines)
        if st.get("moving") is False:
            return True
        time.sleep(0.15)
    return False


def angle_targets(axis: int | None) -> tuple[float, float, float]:
    """Return angle command deltas (deg) for top → horizontal sweep."""
    if axis is None:
        return (HORIZONTAL_DEG, HORIZONTAL_DEG, HORIZONTAL_DEG)
    hold = CALIBRATED_UP_DEG
    targets = [hold, hold, hold]
    targets[axis] = HORIZONTAL_DEG
    return tuple(targets)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/arduino-stewart")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip prompt (cranks already straight up)",
    )
    parser.add_argument(
        "--axis",
        type=int,
        choices=(0, 1, 2),
        help="exercise one axis only (others hold at straight up)",
    )
    parser.add_argument(
        "--return",
        dest="return_up",
        action="store_true",
        help="after horizontal, sweep back to straight up before disable",
    )
    args = parser.parse_args()

    print(f"Opening {args.port} …")
    try:
        ser = open_stewart_serial(args.port, args.baud, timeout=0.3)
    except serial.SerialException as exc:
        print(f"Cannot open port: {exc}", file=sys.stderr)
        return 1

    if wait_if_reset(ser):
        print("Board rebooted after serial open — waiting for firmware …")

    for line in send(ser, "status"):
        print(f"  < {line}")

    if not args.yes:
        print()
        print("Place ALL cranks straight UP (highest), motors free/disabled.")
        if args.axis is not None:
            print(f"Then axis {args.axis} will sweep up → horizontal (~90°).")
        else:
            print("Then all three will sweep up → horizontal (~90°).")
        print("Press Enter to calibrate and move, Ctrl-C to abort.")
        try:
            input()
        except EOFError:
            print("No TTY — re-run with --yes once cranks are up.", file=sys.stderr)
            ser.close()
            return 2

    replies = send(ser, "calibrate", wait=0.8)
    for line in replies:
        print(f"  < {line}")
    if not any(line.startswith("OK calibrate") for line in replies):
        print("Calibration failed.", file=sys.stderr)
        ser.close()
        return 3

    if args.axis is not None:
        enable_cmd = f"enable {args.axis}"
    else:
        enable_cmd = "enable"
    print(f"> {enable_cmd}")
    for line in send(ser, enable_cmd):
        print(f"  < {line}")

    a0, a1, a2 = angle_targets(args.axis)
    move_cmd = f"angle {a0:.3f} {a1:.3f} {a2:.3f}"
    print(f"> {move_cmd}   # straight up ({CALIBRATED_UP_DEG}°) → horizontal (0°)")
    for line in send(ser, move_cmd, wait=0.4):
        print(f"  < {line}")

    print("Waiting for move …")
    if not wait_idle(ser):
        print("Timed out waiting for motion to finish.", file=sys.stderr)
        send(ser, "disable")
        ser.close()
        return 4

    if args.return_up:
        back_cmd = f"angle {CALIBRATED_UP_DEG:.3f} {CALIBRATED_UP_DEG:.3f} {CALIBRATED_UP_DEG:.3f}"
        if args.axis is not None:
            hold = CALIBRATED_UP_DEG
            back = [hold, hold, hold]
            back[args.axis] = CALIBRATED_UP_DEG
            back_cmd = f"angle {back[0]:.3f} {back[1]:.3f} {back[2]:.3f}"
        print(f"> {back_cmd}   # return to straight up")
        for line in send(ser, back_cmd, wait=0.4):
            print(f"  < {line}")
        if not wait_idle(ser):
            print("Timed out on return move.", file=sys.stderr)

    print("> disable")
    for line in send(ser, "disable"):
        print(f"  < {line}")

    ser.close()
    print("Done — axes disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
