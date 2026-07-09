#!/usr/bin/env python3
"""
led_strip_test.py — simple full-strip sanity check, ahead of per-pixel
calibration. Resizes each of the 3 strips to a generous pixel count and
fills the WHOLE strip with a single low-brightness color, one strip at a
time, so you can see at a glance whether every real LED on that strip
lights up (bad data/power connections partway down a strip will show up
as dark LEDs past a certain point, or the whole strip staying dark).

This is deliberately dumber than led_cal_tool.py (no per-pixel addressing)
so it's useful for isolating whether an issue is with the physical
strip/wiring or with the calibration tool itself.

USAGE
    python3 led_strip_test.py                     # defaults: dim white, 150px/strip, 4s hold
    python3 led_strip_test.py --brightness 15
    python3 led_strip_test.py --count 60           # if you already know the real length
    python3 led_strip_test.py --color 40 0 0       # dim red instead of white
    python3 led_strip_test.py --together           # light all 3 strips at once instead of one at a time
"""

import argparse
import glob
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is required:  pip3 install pyserial")

STRIP_LABELS = {0: "A (D2)", 1: "B (D6)", 2: "C (D5)"}


def autodetect_port():
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
    ap.add_argument("--count", type=int, default=150,
                     help="pixels to resize each strip to before filling (default 150, "
                          "~3 modules x 48 addressable each). The standard board is an Uno "
                          "R4 (32KB RAM) so this is roomy; the printed MEM readings will "
                          "read -1 on R4 (that diagnostic is AVR-only) since RAM pressure "
                          "isn't the concern there that it was on a classic Uno")
    ap.add_argument("--brightness", type=int, default=25,
                     help="dim white level 0-255 per channel (default 25); ignored if --color given")
    ap.add_argument("--color", nargs=3, type=int, metavar=("R", "G", "B"), default=None,
                     help="explicit R G B (0-255 each) instead of dim white")
    ap.add_argument("--hold", type=float, default=4.0, help="seconds to hold each strip lit")
    ap.add_argument("--together", action="store_true",
                     help="light all 3 strips at once instead of one at a time")
    ap.add_argument("--only", choices=["0", "1", "2", "A", "B", "C", "a", "b", "c"], default=None,
                     help="test just one strip (0/A, 1/B, or 2/C) instead of cycling through all 3")
    args = ap.parse_args()

    r, g, b = args.color if args.color else (args.brightness,) * 3
    only = None
    if args.only is not None:
        only = {"0": 0, "1": 1, "2": 2, "a": 0, "b": 1, "c": 2}[args.only.lower()]

    port = args.port or autodetect_port()
    if not port:
        sys.exit("No serial port found. Pass it explicitly: python3 led_strip_test.py /dev/cu.usbmodemXXXX")

    print(f"Connecting to {port} @ {args.baud} ...")
    ser = serial.Serial(port, args.baud, timeout=0.2)
    time.sleep(2.0)          # ride out the Uno's DTR auto-reset
    ser.reset_input_buffer()
    send(ser, "E")
    drain(ser, 0.2)
    # Force everything off first — opening the serial port doesn't reliably
    # reset every Arduino board (notably the R4), so pixels left lit by a
    # PREVIOUS run can still be showing when this one starts.
    send(ser, "LX")
    drain(ser, 0.1)

    strips_to_test = [only] if only is not None else list(range(3))

    try:
        send(ser, "M")
        drain(ser, 0.2)
        print(f"Resizing strip(s) {strips_to_test} to {args.count} pixels ...")
        for s in strips_to_test:
            send(ser, f"LN {s} {args.count}")
            drain(ser, 0.2)
        send(ser, "M")
        drain(ser, 0.2)
        print("(compare the two MEM readings above — if free SRAM is getting close to 0, "
              "lower --count or test strips one at a time with --only)")

        if args.together and only is None:
            print(f"Filling all 3 strips with RGB({r},{g},{b}) — check every strip for dark LEDs.")
            for s in strips_to_test:
                send(ser, f"L {s} {r} {g} {b}")
                drain(ser)
            time.sleep(args.hold)
        else:
            for s in strips_to_test:
                print(f"\n-- strip {STRIP_LABELS[s]} --")
                send(ser, f"L {s} {r} {g} {b}")
                drain(ser)
                print(f"   should be fully lit, dim RGB({r},{g},{b}), all {args.count} pixels — "
                      f"check for dark spots or a dark tail past some point")
                time.sleep(args.hold)
                send(ser, f"L {s} 0 0 0")
                drain(ser)

        send(ser, "LX")
        drain(ser)
    finally:
        ser.close()

    print("\nDone. If a strip only partially lit (dark past some point), that's a wiring/power "
          "issue on that strip, not a software issue. If nothing lit at all on a strip, check "
          "its data pin and power connections. If everything here lights fully but "
          "led_cal_tool.py still only shows one LED, that points at the per-pixel tool instead.")


if __name__ == "__main__":
    main()
