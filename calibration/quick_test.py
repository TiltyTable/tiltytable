#!/usr/bin/env python3
"""
quick_test.py — one-shot smoke test for the tilt table hardware.

Confirms, over a single serial session, that:
  1. All 3 NeoPixel LED strips (D2 / D6 / D5) light up red, green, then blue.
  2. Every channel on all 9 PCA9685 boards (0x40-0x48) visibly wiggles.

IMPORTANT: calibrated ranges vary WILDLY per servo (e.g. board 0x40's
channels genuinely run in the ~130-320us band while others run in the
~900-2600us band) — this is real, per-servo calibration, not a units bug.
So this test loads each board's own servo_config_0x4X.json and sweeps each
channel only within its OWN calibrated [min(recessed,extended),
max(recessed,extended)] envelope. Channels with no calibration yet fall
back to a conservative default and are flagged, rather than guessed at.

Requires the updated servo_calib.ino (adds the "L"/"LX" LED commands and
the stuck-on watchdog) to be flashed to the Arduino first.

USAGE
    python3 quick_test.py                       # auto-detect port
    python3 quick_test.py /dev/cu.usbmodemXXXX   # explicit port
    python3 quick_test.py --leds-only
    python3 quick_test.py --servos-only
"""

import argparse
import glob
import json
import os
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is required:  pip3 install pyserial")

BOARD_ADDRESSES = [0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48]
NUM_CHANNELS = 16
POSITION_KEYS = ("recessed", "neutral", "extended")
# Fallback ONLY for a channel with no calibration at all — a small, gentle
# nudge around center rather than guessing at a wide range that might not
# fit this particular servo.
FALLBACK_LO, FALLBACK_HI = 1400, 1600
DWELL = 0.12                  # seconds per position — fast but visible
NUM_LED_STRIPS = 3
LED_COLORS = [("RED", 255, 0, 0), ("GREEN", 0, 255, 0), ("BLUE", 0, 0, 255)]
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))


def autodetect_port():
    cands = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return cands[0] if cands else None


def load_board_config(addr):
    path = os.path.join(CONFIG_DIR, f"servo_config_0x{addr:02X}.json".lower())
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f).get("servos", {})


def channel_bounds(servos, ch):
    """(lo, hi, calibrated) for one channel using its own recorded points."""
    s = servos.get(str(ch))
    if s:
        vals = [s[k] for k in POSITION_KEYS if k in s]
        if len(vals) >= 2:
            return min(vals), max(vals), True
    return FALLBACK_LO, FALLBACK_HI, False


def send(ser, cmd):
    ser.write((cmd + "\n").encode())
    ser.flush()


def drain(ser, wait=0.05):
    time.sleep(wait)
    while ser.in_waiting:
        line = ser.readline().decode(errors="replace").strip()
        if line:
            tag = "!!" if line.startswith("WATCHDOG") else "<"
            print(f"   {tag} {line}")


def test_leds(ser):
    print("\n=== LED strip test (D2 / D6 / D5) ===")
    for strip in range(NUM_LED_STRIPS):
        print(f"-- strip {strip} --")
        for name, r, g, b in LED_COLORS:
            send(ser, f"L {strip} {r} {g} {b}")
            drain(ser)
            print(f"   strip {strip} should be {name} — check it now")
            time.sleep(0.6)
        send(ser, f"L {strip} 0 0 0")
        drain(ser)
    send(ser, "LX")
    drain(ser)
    print("LED test complete.")


def test_servos(ser):
    print("\n=== Servo test — 9 boards x 16 channels ===")
    print(f"    (each channel sweeps within its OWN calibrated range; "
          f"uncalibrated channels get a gentle {FALLBACK_LO}-{FALLBACK_HI}us nudge)")
    for addr in BOARD_ADDRESSES:
        servos = load_board_config(addr)
        print(f"-- board 0x{addr:02X} --" + ("" if servos else "  (no config found — using fallback for all channels)"))
        send(ser, f"A 0x{addr:02X}")
        drain(ser, 0.3)
        uncalibrated = []
        for ch in range(NUM_CHANNELS):
            lo, hi, calibrated = channel_bounds(servos, ch)
            if not calibrated:
                uncalibrated.append(ch)
            send(ser, f"P {ch} {lo}")
            drain(ser, DWELL)
            send(ser, f"P {ch} {hi}")
            drain(ser, DWELL)
            send(ser, f"O {ch}")
            drain(ser, 0.02)
        if uncalibrated:
            print(f"   note: channel(s) {uncalibrated} on 0x{addr:02X} have no "
                  f"calibration — only got a small {FALLBACK_LO}-{FALLBACK_HI}us nudge.")
        print(f"   board 0x{addr:02X} done — confirm all 16 channels wiggled")
    print("Servo test complete.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("port", nargs="?", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--leds-only", action="store_true")
    ap.add_argument("--servos-only", action="store_true")
    args = ap.parse_args()

    port = args.port or autodetect_port()
    if not port:
        sys.exit("No serial port found. Pass it explicitly: python3 quick_test.py /dev/cu.usbmodemXXXX")

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

    try:
        if not args.servos_only:
            test_leds(ser)
        if not args.leds_only:
            test_servos(ser)
    finally:
        ser.close()

    print("\nAll done. If a channel or strip didn't visibly respond, "
          "check wiring/power for that board or strip.")


if __name__ == "__main__":
    main()
