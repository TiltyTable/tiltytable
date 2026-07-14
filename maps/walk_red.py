#!/usr/bin/env python3
"""
Walk every grid cell red in order from (0,0), row by row.
LED-only diagnostic — no servo motion.

Usage:
  .venv/bin/python3 maps/walk_red.py
  .venv/bin/python3 maps/walk_red.py --hold 0.4
  .venv/bin/python3 maps/walk_red.py --trail     # leave visited cells dim red
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "calibration"))

import serial

LED_CFG = os.path.join(ROOT, "calibration", "led_grid_config.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/arduino-modules")
    ap.add_argument("--hold", type=float, default=0.35, help="seconds per cell")
    ap.add_argument("--trail", action="store_true",
                    help="leave each visited cell dim red instead of clearing")
    ap.add_argument("--rgb", nargs=3, type=int, default=[80, 0, 0], metavar=("R", "G", "B"))
    ap.add_argument("--dim", nargs=3, type=int, default=[20, 0, 0], metavar=("R", "G", "B"),
                    help="trail color when --trail")
    args = ap.parse_args()

    cfg = json.load(open(LED_CFG))
    cells = cfg.get("cells", {})
    strips = cfg.get("strips", {})

    ser = serial.Serial(args.port, 115200, timeout=0.2)
    time.sleep(2.2)
    ser.reset_input_buffer()

    def send(cmd):
        ser.write((cmd + "\n").encode())
        ser.flush()
        time.sleep(0.02)
        while ser.in_waiting:
            ser.readline()

    send("LX")
    for s, meta in strips.items():
        send(f"LN {s} {int(meta.get('led_count', 50))}")

    r, g, b = args.rgb
    dr, dg, db = args.dim
    missing = []
    print(f"Walking 12x12 from (0,0) — hold={args.hold}s trail={args.trail}")
    try:
        for row in range(12):
            for col in range(12):
                key = f"{row},{col}"
                loc = cells.get(key)
                if not loc:
                    missing.append(key)
                    print(f"  ({row},{col}) NO TAG")
                    time.sleep(args.hold)
                    continue
                strip, idx = loc["strip"], loc["index"]
                print(f"  ({row},{col}) strip {strip} idx {idx}")
                send(f"LP {strip} {idx} {r} {g} {b}")
                time.sleep(args.hold)
                if args.trail:
                    send(f"LP {strip} {idx} {dr} {dg} {db}")
                else:
                    send(f"LP {strip} {idx} 0 0 0")
        print("Done. Leaving table as-is (trail or clear).")
        if missing:
            print(f"Missing tags: {missing}")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        # keep LEDs if trail; always close port
        ser.close()


if __name__ == "__main__":
    main()
