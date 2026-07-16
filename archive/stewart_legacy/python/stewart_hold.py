#!/usr/bin/env python3
"""Enable all Stewart motor drivers and hold their current positions.

This intentionally does not calibrate or move the platform.  Opening the Uno
serial port may reset it, so the ``hold`` command is sent after a short boot
wait.  The Arduino keeps the drivers enabled after this script exits.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is required: pip3 install pyserial")

from stewart_serial import open_stewart_serial


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hold all three Stewart motors at their present positions"
    )
    parser.add_argument("--port", default="/dev/arduino-stewart")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--boot-wait", type=float, default=2.0,
        help="seconds to wait after opening the serial port (default: 2.0)",
    )
    args = parser.parse_args()

    try:
        ser = open_stewart_serial(args.port, args.baud, timeout=0.2)
    except serial.SerialException as exc:
        print(f"Cannot open {args.port}: {exc}", file=sys.stderr)
        return 1

    try:
        time.sleep(args.boot_wait)
        ser.reset_input_buffer()
        ser.write(b"hold\n")
        ser.flush()

        deadline = time.monotonic() + 1.0
        replies: list[str] = []
        while time.monotonic() < deadline:
            raw = ser.readline()
            if raw:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    replies.append(line)
                    print(line)
                    if line == "OK holding current positions":
                        return 0

        print("Arduino did not confirm hold", file=sys.stderr)
        return 1
    finally:
        # Do not send disable: the requested behavior is to keep holding after
        # this process exits. HUPCL is cleared by open_stewart_serial().
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
