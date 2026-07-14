#!/usr/bin/env python3
"""
led_strip_test.py — simple full-strip sanity check, ahead of per-pixel
calibration. Resizes each of the 9 module strands and fills the WHOLE
strip with a single low-brightness color, one strip at a time, so you
can see at a glance whether every real LED on that strand lights up.

USAGE
    python3 led_strip_test.py                     # defaults: dim white, 50px/strip, 3s hold
    python3 led_strip_test.py --brightness 15
    python3 led_strip_test.py --count 50
    python3 led_strip_test.py --color 40 0 0
    python3 led_strip_test.py --together           # light all 9 at once
    python3 led_strip_test.py --only 0             # one strip index
    python3 led_strip_test.py --port /dev/arduino-modules
"""

import argparse
import glob
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is required:  pip3 install pyserial")

# Strip index = row-major module order (matches servo_calib.ino / led_grid_config)
STRIP_LABELS = {
    0: "0x43 D3 TL",
    1: "0x45 D8 center",
    2: "0x48 D5 TR",
    3: "0x42 A1 LM",
    4: "0x44 D9 BM",
    5: "0x40 A3 RM",
    6: "0x47 A2 BL",
    7: "0x46 D7 TM",
    8: "0x41 D2 BR",
}
NUM_STRIPS = 9


def autodetect_port():
    for p in ("/dev/arduino-modules",):
        try:
            import os
            if os.path.exists(p):
                return p
        except Exception:
            pass
    cands = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return cands[0] if cands else None


def send(ser, cmd):
    ser.write((cmd + "\n").encode())
    ser.flush()


def drain(ser, wait=0.05):
    time.sleep(wait)
    while ser.in_waiting:
        line = ser.readline().decode(errors="replace").strip()
        if line:
            print(f"   < {line}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--count", type=int, default=50,
                     help="pixels to resize each strip to before filling (default 50)")
    ap.add_argument("--brightness", type=int, default=25,
                     help="dim white level 0-255 per channel (default 25); ignored if --color given")
    ap.add_argument("--color", nargs=3, type=int, metavar=("R", "G", "B"), default=None,
                     help="explicit R G B (0-255 each) instead of dim white")
    ap.add_argument("--hold", type=float, default=3.0, help="seconds to hold each strip lit")
    ap.add_argument("--together", action="store_true",
                     help="light all strips at once instead of one at a time")
    ap.add_argument("--only", type=int, choices=list(range(NUM_STRIPS)), default=None,
                     help="test just one strip index 0-8")
    args = ap.parse_args()

    r, g, b = args.color if args.color else (args.brightness,) * 3

    port = args.port or autodetect_port()
    if not port:
        sys.exit("No serial port found. Pass --port /dev/arduino-modules")

    print(f"Connecting to {port} @ {args.baud} ...")
    ser = serial.Serial(port, args.baud, timeout=0.2)
    time.sleep(2.0)
    ser.reset_input_buffer()
    send(ser, "E")
    drain(ser, 0.2)
    send(ser, "LX")
    drain(ser, 0.1)

    strips_to_test = [args.only] if args.only is not None else list(range(NUM_STRIPS))

    try:
        send(ser, "M")
        drain(ser, 0.2)
        print(f"Resizing strip(s) {strips_to_test} to {args.count} pixels ...")
        for s in strips_to_test:
            send(ser, f"LN {s} {args.count}")
            drain(ser, 0.15)
        send(ser, "M")
        drain(ser, 0.2)

        if args.together and args.only is None:
            print(f"Filling all {NUM_STRIPS} strips with RGB({r},{g},{b})")
            for s in strips_to_test:
                send(ser, f"L {s} {r} {g} {b}")
                drain(ser)
            time.sleep(args.hold)
        else:
            for s in strips_to_test:
                print(f"\n-- strip {s}: {STRIP_LABELS[s]} --")
                send(ser, f"L {s} {r} {g} {b}")
                drain(ser)
                print(f"   should be fully lit RGB({r},{g},{b})")
                time.sleep(args.hold)
                send(ser, f"L {s} 0 0 0")
                drain(ser)

        send(ser, "LX")
        drain(ser)
    finally:
        ser.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
