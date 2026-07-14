#!/usr/bin/env python3
"""
quick_test.py — one-shot smoke test for the tilt table hardware.

Confirms, over a single serial session, that:
  1. All 9 NeoPixel module strands light up red, green, then blue.
  2. Every channel on all 9 PCA9685 boards (0x40-0x48) visibly wiggles.

IMPORTANT: calibrated ranges vary WILDLY per servo (e.g. board 0x40's
channels genuinely run in the ~130-320us band while others run in the
~900-2600us band) — this is real, per-servo calibration, not a units bug.
So this test loads each board's own servo_config_0x4X.json and sweeps each
channel only within its OWN calibrated [min(recessed,extended),
max(recessed,extended)] envelope. Channels with no calibration yet fall
back to a conservative default and are flagged, rather than guessed at.

Requires servo_calib.ino with NUM_STRIPS=9 on the modules board.

USAGE
    python3 quick_test.py                       # auto-detect port
    python3 quick_test.py /dev/arduino-modules
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
FALLBACK_LO, FALLBACK_HI = 1400, 1600
DWELL = 0.12
NUM_LED_STRIPS = 9
DEFAULT_LED_COUNT = 50
LED_COLORS = [("RED", 255, 0, 0), ("GREEN", 0, 255, 0), ("BLUE", 0, 0, 255)]
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))

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


def autodetect_port():
    for p in ("/dev/arduino-modules",):
        if os.path.exists(p):
            return p
    cands = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return cands[0] if cands else None


def load_led_counts():
    path = os.path.join(CONFIG_DIR, "led_grid_config.json")
    if not os.path.exists(path):
        return {s: DEFAULT_LED_COUNT for s in range(NUM_LED_STRIPS)}
    with open(path) as f:
        cfg = json.load(f)
    counts = {}
    for s, meta in cfg.get("strips", {}).items():
        counts[int(s)] = int(meta.get("led_count", DEFAULT_LED_COUNT))
    for s in range(NUM_LED_STRIPS):
        counts.setdefault(s, DEFAULT_LED_COUNT)
    return counts


def load_board_config(addr):
    path = os.path.join(CONFIG_DIR, f"servo_config_0x{addr:02X}.json".lower())
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f).get("servos", {})


def channel_bounds(servos, ch):
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
    counts = load_led_counts()
    print(f"\n=== LED strand test ({NUM_LED_STRIPS} module strips) ===")
    for strip in range(NUM_LED_STRIPS):
        n = counts[strip]
        send(ser, f"LN {strip} {n}")
        drain(ser, 0.1)
        label = STRIP_LABELS.get(strip, f"strip {strip}")
        print(f"-- strip {strip}: {label} ({n} px) --")
        for name, r, g, b in LED_COLORS:
            send(ser, f"L {strip} {r} {g} {b}")
            drain(ser)
            print(f"   strip {strip} should be {name} — check it now")
            time.sleep(0.5)
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
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--leds-only", action="store_true")
    ap.add_argument("--servos-only", action="store_true")
    args = ap.parse_args()

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

    try:
        if not args.servos_only:
            test_leds(ser)
        if not args.leds_only:
            test_servos(ser)
    finally:
        send(ser, "X")
        send(ser, "LX")
        ser.close()

    print("\nAll done. If a channel or strip didn't visibly respond, "
          "check wiring/power for that board or strip.")


if __name__ == "__main__":
    main()
