#!/usr/bin/env python3
"""
led_color_cal_tool.py — make every tile the same brightness/color.

LED-only (no servos). Lights stay on until you type off or quit.

QUICK START (top modules too bright?):
  yellow
  darker top
  darker top          # again if still bright
  save

Or: red / green / yellow / … then darker/brighter on a region.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import threading
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is required: pip3 install pyserial")

from led_color import (
    CAL_DIR,
    get_gains,
    load_cal,
    load_palette,
    palette_names,
    resolve_name,
    save_cal,
    save_palette,
    set_gains,
    cell_key,
)

GRID = 12
MODULE = 4
HEARTBEAT_INTERVAL_S = 2.0
LED_CONFIG = os.path.join(CAL_DIR, "led_grid_config.json")
STEP = 0.12  # ~12% per darker/brighter press

# Friendly aliases → palette id
COLOR_ALIASES = {
    "red": "trap",
    "trap": "trap",
    "green": "wall",
    "wall": "wall",
    "start": "start",
    "cyan": "start",
    "end": "end",
    "magenta": "end",
    "finish": "end",
    "yellow": "yellow",
    "floor": "floor",
    "grey": "floor",
    "gray": "floor",
    "points": "points",
    "blue": "points",
    "black": "off",
    "off": "off",
}

BANNER = """
LED color calibration
─────────────────────
Light a color, then fix bands that look wrong.

If YELLOW looks blue on the left and okay on the right:
  yellow
  less blue left
  less blue left      (repeat until left matches right)
  save

Regions:  left | center | right | top | middle | bottom | all
          module <0-2> <0-2>     (0 0 = top-left module)

Colors:   black | red | green | cyan | magenta | yellow | gray | blue

Tint:     less blue / more blue / less red / more red / less green / more green
          warmer / cooler          (warmer = less blue + a bit more red)

Brightness: darker / brighter      (same regions)

Then:     save
"""


def autodetect_port():
    preferred = "/dev/arduino-modules"
    if os.path.exists(preferred):
        return preferred
    cands = (
        glob.glob("/dev/ttyACM*")
        + glob.glob("/dev/ttyUSB*")
        + glob.glob("/dev/cu.usbmodem*")
    )
    return cands[0] if cands else None


class Link:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self._stop = False
        self._lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._hb, daemon=True).start()

    def _read(self):
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
                line, buf = buf.split(b"\n", 1)
                text = line.decode(errors="replace").strip()
                if text.startswith(("ERR", "WATCHDOG", "HOLD")):
                    print(f"   !! {text}")

    def _hb(self):
        while not self._stop:
            time.sleep(HEARTBEAT_INTERVAL_S)
            if self._stop:
                break
            try:
                self.send("E")
            except Exception:
                break

    def send(self, cmd):
        with self._lock:
            self.ser.write((cmd + "\n").encode())
            self.ser.flush()

    def open_wait(self):
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self.send("E")

    def close(self):
        self._stop = True
        time.sleep(0.1)
        try:
            self.ser.close()
        except Exception:
            pass


def region_cells(name: str):
    """Named bands → list of (row,col)."""
    name = name.lower()
    if name == "top":
        return [(r, c) for r in range(0, 4) for c in range(GRID)]
    if name == "middle":
        return [(r, c) for r in range(4, 8) for c in range(GRID)]
    if name == "bottom":
        return [(r, c) for r in range(8, 12) for c in range(GRID)]
    if name == "left":
        return [(r, c) for r in range(GRID) for c in range(0, 4)]
    if name == "center":
        return [(r, c) for r in range(GRID) for c in range(4, 8)]
    if name == "right":
        return [(r, c) for r in range(GRID) for c in range(8, 12)]
    if name == "all":
        return [(r, c) for r in range(GRID) for c in range(GRID)]
    raise ValueError(name)


def module_cells(mr: int, mc: int):
    if not (0 <= mr <= 2 and 0 <= mc <= 2):
        raise ValueError("module row/col must be 0, 1, or 2 (0,0 = top-left)")
    r0, c0 = mr * MODULE, mc * MODULE
    return [(r, c) for r in range(r0, r0 + MODULE) for c in range(c0, c0 + MODULE)]


class Session:
    def __init__(self, link, led_cfg, palette, cal):
        self.link = link
        self.led_cfg = led_cfg
        self.palette = palette
        self.cal = cal
        self.color = "off"
        self.last_region_cells = region_cells("all")
        self.last_region_label = "all"
        self.last_factor = 1.0
        self.dirty = False
        self._strip_counts = {
            int(s): int(v.get("led_count", 50))
            for s, v in led_cfg.get("strips", {}).items()
        }

    def apply_led_counts(self):
        for s, n in self._strip_counts.items():
            self.link.send(f"LN {s} {n}")
            time.sleep(0.05)

    def led_at(self, row, col):
        c = self.led_cfg.get("cells", {}).get(f"{row},{col}")
        return (c["strip"], c["index"]) if c else None

    def set_pixel(self, row, col, rgb):
        loc = self.led_at(row, col)
        if not loc:
            return False
        s, i = loc
        self.link.send(f"LP {s} {i} {rgb[0]} {rgb[1]} {rgb[2]}")
        time.sleep(max(0.015, self._strip_counts.get(s, 50) * 0.0002))
        return True

    def show_color(self, name: str, cells=None, label=None):
        pid = COLOR_ALIASES.get(name.lower(), name.lower())
        if pid not in self.palette.get("colors", {}):
            print(f"   unknown color {name!r} — try: palette")
            return
        self.color = pid
        if cells is None:
            cells = [(r, c) for r in range(GRID) for c in range(GRID)]
            label = "whole table"
        n = 0
        for r, c in cells:
            if not self.led_at(r, c):
                continue
            rgb = resolve_name(self.palette, self.cal, pid, r, c)
            if self.set_pixel(r, c, rgb):
                n += 1
        nice = self.palette["colors"][pid].get("label", pid)
        print(f"   {nice} ({pid}) on {n} tiles" + (f" [{label}]" if label else ""))

    def scale_cells(self, cells, factor: float, label: str):
        """Multiply all RGB gains on cells by factor, then re-show current color there."""
        factor = max(0.5, min(1.5, factor))
        n = 0
        for r, c in cells:
            if not self.led_at(r, c):
                continue
            sr, sg, sb = get_gains(self.cal, r, c)
            set_gains(
                self.cal, r, c,
                max(0.05, min(3.0, sr * factor)),
                max(0.05, min(3.0, sg * factor)),
                max(0.05, min(3.0, sb * factor)),
            )
            n += 1
        self.dirty = True
        self.last_region_cells = cells
        self.last_region_label = label
        self.last_factor = factor
        verb = "darker" if factor < 1 else "brighter"
        print(f"   {verb} ×{factor:.2f} on {label} ({n} tiles). Re-lighting {self.color}…")
        self.show_color(self.color, cells, label)

    def tint_cells(self, cells, label: str, dr=1.0, dg=1.0, db=1.0, note=""):
        """Scale R/G/B gains independently (for blue cast, etc.)."""
        n = 0
        for r, c in cells:
            if not self.led_at(r, c):
                continue
            sr, sg, sb = get_gains(self.cal, r, c)
            set_gains(
                self.cal, r, c,
                max(0.05, min(3.0, sr * dr)),
                max(0.05, min(3.0, sg * dg)),
                max(0.05, min(3.0, sb * db)),
            )
            n += 1
        self.dirty = True
        self.last_region_cells = cells
        self.last_region_label = label
        self.last_tint = (dr, dg, db)
        print(f"   {note or 'tint'} on {label} ({n} tiles). Re-lighting {self.color}…")
        self.show_color(self.color, cells, label)


def _parse_region(parts, start_idx=1):
    """Return (cells, label)."""
    if start_idx >= len(parts):
        raise ValueError("need a region: left|center|right|top|middle|bottom|all|module r c")
    if parts[start_idx].lower() == "module":
        if start_idx + 2 >= len(parts):
            raise ValueError("module needs two numbers: module 0 0")
        mr, mc = int(parts[start_idx + 1]), int(parts[start_idx + 2])
        return module_cells(mr, mc), f"module {mr},{mc}"
    label = parts[start_idx].lower()
    return region_cells(label), label


TINT_STEP = 0.12


def run_repl(session: Session):
    print(BANNER)
    print(f"   saved tile gains: {len(session.cal.get('gains', {}))}")
    print("   Tip: left/center/right = column bands (cols 0-3 / 4-7 / 8-11).")
    while True:
        try:
            raw = input("[led] > ").strip()
        except EOFError:
            raw = "q"
        if not raw:
            continue
        parts = raw.split()
        cmd = parts[0].lower()

        try:
            if cmd in ("q", "quit", "exit"):
                if session.dirty:
                    ans = input("   unsaved — save now? [Y/n] ").strip().lower()
                    if ans in ("", "y", "yes"):
                        save_palette(session.palette)
                        save_cal(session.cal)
                        print("   saved.")
                return
            if cmd in ("help", "?"):
                print(BANNER)
                continue
            if cmd == "palette":
                for name in palette_names(session.palette):
                    e = session.palette["colors"][name]
                    print(f"   {name:10} {e['hex']}  {e.get('label', '')}")
                continue
            if cmd == "off":
                session.link.send("LX")
                print("   lights off")
                continue
            if cmd == "save":
                save_palette(session.palette)
                save_cal(session.cal)
                session.dirty = False
                print("   saved.")
                continue

            # yellow left  /  red module 0 0
            if cmd in COLOR_ALIASES or cmd in session.palette.get("colors", {}):
                if len(parts) == 1:
                    session.show_color(cmd)
                else:
                    cells, label = _parse_region(parts, 1)
                    session.show_color(cmd, cells, label)
                continue

            # less blue left  /  more red center  /  warmer left
            if cmd in ("less", "more") and len(parts) >= 3:
                channel = parts[1].lower()
                if channel not in ("red", "green", "blue", "r", "g", "b"):
                    print("   try: less blue left")
                    continue
                ch = {"red": "r", "green": "g", "blue": "b", "r": "r", "g": "g", "b": "b"}[channel]
                cells, label = _parse_region(parts, 2)
                delta = (1.0 - TINT_STEP) if cmd == "less" else (1.0 + TINT_STEP)
                dr = dg = db = 1.0
                if ch == "r":
                    dr = delta
                elif ch == "g":
                    dg = delta
                else:
                    db = delta
                session.tint_cells(cells, label, dr, dg, db, note=f"{cmd} {channel}")
                continue

            if cmd in ("warmer", "cooler") and len(parts) >= 2:
                cells, label = _parse_region(parts, 1)
                if cmd == "warmer":
                    # cut blue, slight red bump
                    session.tint_cells(
                        cells, label,
                        dr=1.0 + TINT_STEP * 0.5,
                        dg=1.0,
                        db=1.0 - TINT_STEP,
                        note="warmer",
                    )
                else:
                    session.tint_cells(
                        cells, label,
                        dr=1.0 - TINT_STEP * 0.5,
                        dg=1.0,
                        db=1.0 + TINT_STEP,
                        note="cooler",
                    )
                continue

            if cmd in ("darker", "brighter") and len(parts) >= 2:
                factor = (1.0 - STEP) if cmd == "darker" else (1.0 + STEP)
                cells, label = _parse_region(parts, 1)
                session.scale_cells(cells, factor, label)
                continue

            if cmd in ("+", "-") and session.last_region_cells:
                # repeat last tint if we have one, else brightness
                if getattr(session, "last_tint", None):
                    dr, dg, db = session.last_tint
                    if cmd == "-":
                        # invert direction of last tint step toward neutral-ish repeat of opposite
                        dr = 1.0 / dr if dr else 1.0
                        dg = 1.0 / dg if dg else 1.0
                        db = 1.0 / db if db else 1.0
                    session.tint_cells(
                        session.last_region_cells, session.last_region_label,
                        dr, dg, db, note="repeat tint",
                    )
                else:
                    factor = (1.0 - STEP) if cmd == "-" else (1.0 + STEP)
                    session.scale_cells(
                        session.last_region_cells, factor, session.last_region_label
                    )
                continue

            print("   try:  yellow")
            print("         less blue left")
            print("         warmer center")
            print("         save")
        except ValueError as ex:
            print(f"   ! {ex}")


def main():
    ap = argparse.ArgumentParser(
        description="Make LED colors look even across the table (human-in-the-loop)."
    )
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    port = args.port or autodetect_port()
    if not port:
        sys.exit("No serial port; pass --port /dev/arduino-modules")

    with open(LED_CONFIG) as f:
        led_cfg = json.load(f)
    palette = load_palette()
    cal = load_cal()

    print(f"Connecting to {port} …")
    link = Link(port, args.baud)
    link.open_wait()
    session = Session(link, led_cfg, palette, cal)
    session.apply_led_counts()
    try:
        run_repl(session)
    finally:
        link.close()
        print("Disconnected (lights left as last shown).")


if __name__ == "__main__":
    main()
