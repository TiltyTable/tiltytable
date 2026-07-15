#!/usr/bin/env python3
"""
stewart_cli.py — one interactive serial session for the Uno R3 Stewart
platform (UIM5756PM, /dev/arduino-stewart), the same style of tool as
calibration/tilt_table_cli.py is for the module grid.

Opens the serial link once without resetting the Uno (DTR/RTS held low),
streams the board's replies in a background reader, and lets you type
firmware commands directly at a prompt.

FIRMWARE COMMANDS (typed verbatim at the prompt)
    status                              board state (always allowed)
    help                               firmware command list
    calibrate            (alias: zero)  legacy: all cranks already straight up
    cal_begin            start interactive per-axis calibration
    cal_axis <0-2>       mark jogged axis as straight up (vertical)
    cal_finish           finish interactive cal (after all cal_axis)
    enable [axis]                      energize all axes (or one: 0/1/2)
    disable [axis]                     release all axes (or one)
    pose <roll> <pitch> <heave_mm>     IK move (needs calibrate)
    vel <roll_s> <pitch_s> <heave_s>   velocity move (needs calibrate)
    angle <a0> <a1> <a2>               per-crank deltas in deg from neutral
    steps <s0> <s1> <s2>               raw step targets
    jog <axis> <pulses>                single-axis relative move

LOCAL COMMANDS (handled by this tool, not sent to the board)
    ?                                  this cheat sheet
    s                                  shortcut for "status"
    off                               shortcut for "disable" (all axes)
    q / quit / exit                    disable all axes, then quit

SAFETY
    calibrate / status / help / disable never move motors. enable / jog work
    during cal_begin (interactive setup). enable / pose / vel / angle / steps /
    jog for normal motion need full calibration (cal_finish or legacy calibrate).
    On exit (or Ctrl-C) this tool sends "disable" so no axis is left energized.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is required:  pip3 install pyserial")

DEFAULT_PORT = "/dev/arduino-stewart"
DEFAULT_BAUD = 115200

MOTION_CMDS = ("enable", "on", "pose", "p", "vel", "v",
               "angle", "a", "steps", "s", "jog", "j")


class Link:
    """Serial link with a background reader that prints every board line."""

    def __init__(self, port: str, baud: int):
        from stewart_serial import open_stewart_serial

        self.ser = open_stewart_serial(port, baud, timeout=0.2)
        self._stop = False
        self._send_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        buf = b""
        while not self._stop:
            try:
                data = self.ser.read(256)
            except Exception:
                break
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").strip()
                if text:
                    print(f"  < {text}")

    def send(self, cmd: str) -> None:
        with self._send_lock:
            self.ser.write((cmd + "\n").encode())
            self.ser.flush()

    def close(self) -> None:
        self._stop = True
        try:
            self._reader.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass


LOCAL_HELP = __doc__


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive Stewart serial CLI")
    parser.add_argument("--port", default=DEFAULT_PORT, help="serial port")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    args = parser.parse_args()

    print(f"Opening {args.port} @ {args.baud} (no DTR reset) …")
    try:
        link = Link(args.port, args.baud)
    except serial.SerialException as exc:
        print(f"Cannot open port: {exc}", file=sys.stderr)
        return 1

    link.send("status")
    time.sleep(0.4)

    print()
    print("Connected. Type firmware commands, '?' for help, 'q' to quit.")
    print("Motion needs calibrate first if status shows calibrated 0.")
    print()

    try:
        while True:
            try:
                raw = input("stewart> ").strip()
            except EOFError:
                print()
                break
            if not raw:
                continue

            low = raw.lower()
            if low in ("q", "quit", "exit"):
                break
            if low == "?":
                print(LOCAL_HELP)
                continue
            if low == "s":
                raw, low = "status", "status"
            elif low == "off":
                raw, low = "disable", "disable"

            first = low.split()[0]
            if first in MOTION_CMDS:
                print(f"   (motion) {raw}")

            link.send(raw)
            time.sleep(0.4)  # let the reader print the reply before next prompt
    except KeyboardInterrupt:
        print()
    finally:
        print("Disabling all axes and closing …")
        try:
            link.send("disable")
            time.sleep(0.3)
        except Exception:
            pass
        link.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
