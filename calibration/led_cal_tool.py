#!/usr/bin/env python3
"""
led_cal_tool.py — interactive calibration tool that maps each LED strip's
addressable pixel index to a global (row, col) coordinate on the 12x12
tilt-table grid, using the same jog-and-tag philosophy as servo_tool.py's
`calibrate` mode.

WHY MANUAL (not computed): each of the 9 modules is a 4x4 grid of LEDs
wired in a zigzag, but the zigzag's entry/exit side is NOT consistent
across modules — some enter from the right and exit from the left. There
is also physical spacing between real LED positions, so only roughly every
3rd addressable pixel along the strip is actually mounted at a grid cell;
the rest are unlit slack. Both of these mean the pixel-index -> grid-cell
mapping cannot be safely computed from a formula — it has to be tagged by
a human looking at the physical hardware, one lit pixel at a time.

WIRING (2026-07-12): nine independent strands (one per 4x4 module), not
daisy-chained. Strip index 0–8 is row-major module order:

    0: 0x43 D3     1: 0x45 D8 (center)     2: 0x48 D5
    3: 0x42 A1     4: 0x44 D9 (bottom mid) 5: 0x40 A3
    6: 0x47 A2     7: 0x46 D7 (top mid)    8: 0x41 D2

Keep NeoPixel data off A4/A5 (Wire I2C for the PCA9685s).

Global frame: (0,0) = top-left, row↓, col→. Each strip owns one 4x4 block
(row_block / col_block in led_grid_config.json). Tagging uses a LOCAL
0–3,0–3 cursor; the tool adds the block offset for the global coordinate.

Requires servo_calib.ino with NUM_STRIPS=9 (LN/LP commands) on the modules
board.

CONTROLS
  Tab / Shift-Tab     next / previous strip (module)
  [ / ]               same as Tab / Shift-Tab (module nav alias)
  , / .               previous / next pixel index on this strip
  arrow keys          move the LOCAL cursor within the current 4x4 module
  Enter / space       tag: link the current lit pixel to the cursor's cell,
                        then auto-advance to the next pixel
  s                   mark the current pixel as slack (not a grid cell), advance
  d                   delete any existing tag for the current pixel
  u                   step back one pixel (undo navigation, does not untag)
  C / V               increase / decrease this strip's LED_COUNT by 10
  1 2 3 4             preset color: white / red / green / blue
  w                   save config
  q                   save & quit
"""

import curses
import glob
import json
import locale
import os
import sys
import threading
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is required:  pip3 install pyserial")

GRID_ROWS = 12
GRID_COLS = 12
MODULE_SIZE = 4
DEFAULT_LED_COUNT = 50
NUM_STRIPS = 9

# One strand per 4x4 module. Strip index = row-major module order.
DEFAULT_STRIPS = {
    "0": {"name": "0x43", "pin": 3,    "address": "0x43", "modules": ["0x43"],
          "led_count": DEFAULT_LED_COUNT, "row_block": 0, "col_block": 0},  # top left
    "1": {"name": "0x45", "pin": 8,    "address": "0x45", "modules": ["0x45"],
          "led_count": DEFAULT_LED_COUNT, "row_block": 4, "col_block": 4},  # center
    "2": {"name": "0x48", "pin": 5,    "address": "0x48", "modules": ["0x48"],
          "led_count": DEFAULT_LED_COUNT, "row_block": 0, "col_block": 8},  # top right
    "3": {"name": "0x42", "pin": "A1", "address": "0x42", "modules": ["0x42"],
          "led_count": DEFAULT_LED_COUNT, "row_block": 4, "col_block": 0},  # left mid
    "4": {"name": "0x44", "pin": 9,    "address": "0x44", "modules": ["0x44"],
          "led_count": DEFAULT_LED_COUNT, "row_block": 8, "col_block": 4},  # bottom mid
    "5": {"name": "0x40", "pin": "A3", "address": "0x40", "modules": ["0x40"],
          "led_count": DEFAULT_LED_COUNT, "row_block": 4, "col_block": 8},  # right mid
    "6": {"name": "0x47", "pin": "A2", "address": "0x47", "modules": ["0x47"],
          "led_count": DEFAULT_LED_COUNT, "row_block": 8, "col_block": 0},  # bottom left
    "7": {"name": "0x46", "pin": 7,    "address": "0x46", "modules": ["0x46"],
          "led_count": DEFAULT_LED_COUNT, "row_block": 0, "col_block": 4},  # top mid
    "8": {"name": "0x41", "pin": 2,    "address": "0x41", "modules": ["0x41"],
          "led_count": DEFAULT_LED_COUNT, "row_block": 8, "col_block": 8},  # bottom right
}

COLOR_PRESETS = {
    ord('1'): ("white", (255, 255, 255)),
    ord('2'): ("red", (255, 0, 0)),
    ord('3'): ("green", (0, 255, 0)),
    ord('4'): ("blue", (0, 0, 255)),
}

_CAL_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH_DEFAULT = os.path.join(_CAL_DIR, "led_grid_config.json")


def pin_label(pin):
    """Format pin for display: 3 -> 'D3', 'A1' -> 'A1'."""
    if isinstance(pin, str) and pin.upper().startswith("A"):
        return pin.upper()
    return f"D{pin}"


def autodetect_port():
    for p in ("/dev/arduino-modules",):
        if os.path.exists(p):
            return p
    cands = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return cands[0] if cands else None


def load_config(path):
    cfg = {
        "grid_rows": GRID_ROWS,
        "grid_cols": GRID_COLS,
        "module_size": MODULE_SIZE,
        "wiring": "9 independent strands (one per module)",
        "strips": json.loads(json.dumps(DEFAULT_STRIPS)),
        "cells": {},
        "skipped": {str(i): [] for i in range(NUM_STRIPS)},
    }
    if os.path.exists(path):
        with open(path) as f:
            loaded = json.load(f)
        cfg.update(loaded)
        cfg.setdefault("cells", {})
        n = len(cfg.get("strips", {})) or NUM_STRIPS
        cfg.setdefault("skipped", {str(i): [] for i in range(n)})
        # Fill missing row_block/col_block from defaults
        for sid, meta in DEFAULT_STRIPS.items():
            if sid in cfg["strips"]:
                cfg["strips"][sid].setdefault("row_block", meta["row_block"])
                cfg["strips"][sid].setdefault("col_block", meta["col_block"])
                cfg["strips"][sid].setdefault("address", meta["address"])
    return cfg


def save_config(cfg, path):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=False)
        f.write("\n")
    print(f"   ✓ saved {path}")


class Link:
    """Minimal serial link for the LED-only protocol (L/LN/LP/LX)."""

    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self._stop = False
        self._lock = threading.Lock()
        self._board_log_lock = threading.Lock()
        self._board_log = []
        self.last_sent = None
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
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
                if text.startswith("ERR") or text.startswith("WATCHDOG"):
                    with self._board_log_lock:
                        self._board_log.append(text)

    def pop_board_message(self):
        with self._board_log_lock:
            return self._board_log.pop(0) if self._board_log else None

    def send(self, cmd):
        with self._board_log_lock:
            self.last_sent = cmd
        with self._lock:
            self.ser.write((cmd + "\n").encode())
            self.ser.flush()

    def open_wait(self):
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self.send("E")

    def close(self):
        self._stop = True
        time.sleep(0.15)
        try:
            self.ser.close()
        except Exception:
            pass


class LedCalTUI:
    def __init__(self, link, cfg, config_path):
        self.link = link
        self.cfg = cfg
        self.config_path = config_path

        self.strip_ids = sorted((int(s) for s in cfg["strips"]), key=int)
        if not self.strip_ids:
            self.strip_ids = list(range(NUM_STRIPS))
        self.n_strips = len(self.strip_ids)
        self.strip = self.strip_ids[0]
        self.abs_index = {s: 0 for s in self.strip_ids}
        self.local_r = 0
        self.local_c = 0
        self.color_name, self.color = "white", (255, 255, 255)
        self.dirty = False
        self.msg = "Tag the lit pixel's cell, then it auto-advances. Tab switches modules."
        self._lit = None
        self.last_key = None

        self._apply_led_counts()
        self._light_current()

    def _strip_cfg(self, strip=None):
        return self.cfg["strips"][str(self.strip if strip is None else strip)]

    def _apply_led_counts(self):
        for s in self.strip_ids:
            count = int(self.cfg["strips"][str(s)].get("led_count", DEFAULT_LED_COUNT))
            self.link.send(f"LN {s} {count}")
            time.sleep(0.05)

    def _block_offset(self):
        sc = self._strip_cfg()
        return int(sc.get("row_block", 0)), int(sc.get("col_block", 0))

    def _global_cell(self):
        row_block, col_block = self._block_offset()
        return row_block + self.local_r, col_block + self.local_c

    def _light_current(self):
        if self._lit is not None:
            old_s, old_i = self._lit
            old_count = int(self.cfg["strips"][str(old_s)].get("led_count", DEFAULT_LED_COUNT))
            self.link.send(f"LP {old_s} {old_i} 0 0 0")
            time.sleep(max(0.03, old_count * 0.0003))
        idx = self.abs_index[self.strip]
        r, g, b = self.color
        self.link.send(f"LP {self.strip} {idx} {r} {g} {b}")
        self._lit = (self.strip, idx)

    def _cell_at(self, strip, index):
        for key, val in self.cfg["cells"].items():
            if val.get("strip") == strip and val.get("index") == index:
                return key
        return None

    def select_strip(self, delta):
        i = self.strip_ids.index(self.strip)
        self.strip = self.strip_ids[(i + delta) % self.n_strips]
        self.local_r = self.local_c = 0
        self._light_current()
        sc = self._strip_cfg()
        self.msg = (f"strip {self.strip}: {sc.get('name', sc.get('address', '?'))} "
                    f"({pin_label(sc['pin'])})")

    def step_index(self, delta):
        s = self.strip
        count = int(self._strip_cfg().get("led_count", DEFAULT_LED_COUNT))
        self.abs_index[s] = max(0, min(count - 1, self.abs_index[s] + delta))
        self._light_current()
        tag = self._cell_at(s, self.abs_index[s])
        skipped = self.abs_index[s] in self.cfg["skipped"].get(str(s), [])
        self.msg = f"pixel {self.abs_index[s]}"
        if tag:
            self.msg += f" — already tagged as {tag}"
        elif skipped:
            self.msg += " — already marked slack"

    def move_cursor(self, dr, dc):
        self.local_r = (self.local_r + dr) % MODULE_SIZE
        self.local_c = (self.local_c + dc) % MODULE_SIZE

    def set_color(self, name, rgb):
        self.color_name, self.color = name, rgb
        self._light_current()

    def tag(self):
        row, col = self._global_cell()
        key = f"{row},{col}"
        self.cfg["cells"][key] = {"strip": self.strip, "index": self.abs_index[self.strip]}
        self.dirty = True
        self.msg = f"tagged pixel {self.abs_index[self.strip]} -> cell ({row},{col})"
        self.step_index(+1)

    def skip(self):
        s = str(self.strip)
        self.cfg.setdefault("skipped", {})
        self.cfg["skipped"].setdefault(s, [])
        idx = self.abs_index[self.strip]
        if idx not in self.cfg["skipped"][s]:
            self.cfg["skipped"][s].append(idx)
            self.dirty = True
        self.step_index(+1)
        self.msg = f"pixel {idx} marked slack -> " + self.msg

    def delete_tag(self):
        s, idx = self.strip, self.abs_index[self.strip]
        key = self._cell_at(s, idx)
        if key:
            del self.cfg["cells"][key]
            self.dirty = True
            self.msg = f"removed tag for pixel {idx} (was {key})"
        else:
            self.msg = f"pixel {idx} has no tag to remove"

    def adjust_led_count(self, delta):
        s = str(self.strip)
        current = int(self.cfg["strips"][s].get("led_count", DEFAULT_LED_COUNT))
        new_count = max(1, current + delta)
        self.cfg["strips"][s]["led_count"] = new_count
        self.link.send(f"LN {self.strip} {new_count}")
        time.sleep(max(0.03, new_count * 0.0003))
        self.dirty = True
        self.abs_index[self.strip] = min(self.abs_index[self.strip], new_count - 1)
        self._light_current()
        self.msg = f"strip {self._strip_cfg()['name']} led_count -> {new_count}"

    def save(self):
        save_config(self.cfg, self.config_path)
        self.dirty = False
        self.msg = f"saved -> {self.config_path}"

    def draw(self, stdscr):
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        C = curses

        def put(y, x, text, attr=0):
            if 0 <= y < h and 0 <= x < w:
                try:
                    stdscr.addnstr(y, x, text, max(0, w - x - 1), attr)
                except C.error:
                    pass

        bold = C.A_BOLD
        cur_a = C.color_pair(1) | bold
        ok_a = C.color_pair(2)
        warn_a = C.color_pair(3)

        sc = self._strip_cfg()
        addr = sc.get("address") or (sc.get("modules") or ["?"])[0]
        idx = self.abs_index[self.strip]
        count = int(sc.get("led_count", DEFAULT_LED_COUNT))
        n_tagged = len(self.cfg["cells"])
        strip_i = self.strip_ids.index(self.strip) + 1

        put(0, 1, "LED GRID CALIBRATION (9 module strands)", bold)
        put(0, max(40, w - 28), f"[{n_tagged}/{GRID_ROWS * GRID_COLS} cells tagged]",
            ok_a if n_tagged == GRID_ROWS * GRID_COLS else 0)
        unsaved = "   *UNSAVED*" if self.dirty else ""
        put(1, 1, f"config: {self.config_path}{unsaved}", warn_a if self.dirty else 0)
        put(1, max(60, w - 40), f"key={self.last_key!r}  sent={self.link.last_sent!r}")

        put(3, 2,
            f">> strip {self.strip} ({strip_i}/{self.n_strips})  {addr}  "
            f"{pin_label(sc['pin'])}   pixel {idx}/{count - 1}   color={self.color_name}",
            cur_a)

        row_block, col_block = self._block_offset()
        put(5, 2, f"local 4x4 (global rows {row_block}-{row_block + 3}, "
                  f"cols {col_block}-{col_block + 3}):", bold)
        for r in range(MODULE_SIZE):
            row_cells = []
            for c in range(MODULE_SIZE):
                key = f"{row_block + r},{col_block + c}"
                is_cursor = (r == self.local_r and c == self.local_c)
                mark = "@" if is_cursor else ("#" if key in self.cfg["cells"] else ".")
                row_cells.append(mark)
            put(6 + r, 4, "   ".join(row_cells),
                cur_a if any(x == "@" for x in row_cells) else 0)

        put(5, 40, "global 12x12 (digit = strip, boxed = current module):", bold)
        for r in range(GRID_ROWS):
            line_chars = []
            for c in range(GRID_COLS):
                key = f"{r},{c}"
                if key in self.cfg["cells"]:
                    ch = str(self.cfg["cells"][key]["strip"])
                else:
                    ch = "."
                in_block = (row_block <= r < row_block + MODULE_SIZE and
                            col_block <= c < col_block + MODULE_SIZE)
                line_chars.append(f"[{ch}]" if in_block else f" {ch} ")
            put(6 + r, 40, "".join(line_chars))

        put(h - 6, 1, self.msg[: w - 2],
            warn_a if "removed" in self.msg or "!" in self.msg else ok_a)
        put(h - 4, 1, "Tab/S-Tab or [ ]: next/prev module   , . : pixel   arrows: local cursor", 0)
        put(h - 3, 1, "Enter/space: tag+advance   s: skip   d: delete tag   u: back   1-4: color", 0)
        put(h - 2, 1, "C/V: +/- led_count(10)   w: save   q: save & quit", 0)
        stdscr.refresh()


def _cal_main(stdscr, link, cfg, config_path):
    curses.curs_set(0)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
    stdscr.keypad(True)
    tui = LedCalTUI(link, cfg, config_path)

    actions = {
        9:               lambda: tui.select_strip(+1),   # Tab
        curses.KEY_BTAB: lambda: tui.select_strip(-1),
        ord('['):        lambda: tui.select_strip(-1),
        ord(']'):        lambda: tui.select_strip(+1),
        ord('.'):        lambda: tui.step_index(+1),
        ord(','):        lambda: tui.step_index(-1),
        ord('u'):        lambda: tui.step_index(-1),
        ord('U'):        lambda: tui.step_index(-1),
        curses.KEY_UP:    lambda: tui.move_cursor(-1, 0),
        curses.KEY_DOWN:  lambda: tui.move_cursor(+1, 0),
        curses.KEY_LEFT:  lambda: tui.move_cursor(0, -1),
        curses.KEY_RIGHT: lambda: tui.move_cursor(0, +1),
        ord(' '):        tui.tag,
        10:              tui.tag,
        13:              tui.tag,
        ord('s'):        tui.skip,
        ord('S'):        tui.skip,
        ord('d'):        tui.delete_tag,
        ord('D'):        tui.delete_tag,
        ord('C'):        lambda: tui.adjust_led_count(+10),
        ord('V'):        lambda: tui.adjust_led_count(-10),
        ord('w'):        tui.save,
        ord('W'):        tui.save,
    }
    for key, (name, rgb) in COLOR_PRESETS.items():
        actions[key] = (lambda n, c: (lambda: tui.set_color(n, c)))(name, rgb)

    while True:
        board_msg = link.pop_board_message()
        if board_msg:
            tui.msg = f"! board: {board_msg}"
        tui.draw(stdscr)
        k = stdscr.getch()
        if k != -1:
            tui.last_key = k
        if k in (ord('q'), ord('Q')):
            if tui.dirty:
                tui.save()
            return
        elif k in actions:
            actions[k]()
        elif k != -1:
            tui.msg = f"(unbound key pressed: code={k!r})"


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--config", default=CONFIG_PATH_DEFAULT)
    args = ap.parse_args()

    locale.setlocale(locale.LC_ALL, "")
    cfg = load_config(args.config)

    port = args.port or autodetect_port()
    if not port:
        sys.exit("No serial port found. Pass --port /dev/arduino-modules")

    print(f"Connecting to {port} @ {args.baud} ...")
    link = Link(port, args.baud)
    link.open_wait()
    link.send("LX")
    time.sleep(0.1)

    try:
        curses.wrapper(_cal_main, link, cfg, args.config)
    except curses.error as ex:
        print(f"TUI failed ({ex}). Try a larger terminal window.")
    finally:
        link.send("LX")
        link.close()

    print(f"Config: {os.path.abspath(args.config)}")


if __name__ == "__main__":
    main()
