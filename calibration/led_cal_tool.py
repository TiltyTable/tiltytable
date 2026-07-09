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

KNOWN WIRING (from the physical layout reference, confirm against your
actual hardware — you can correct any of this live in the tool):

    LED strip A (D2) daisy-chains modules 0x43 -> 0x42 -> 0x47
    LED strip B (D6) daisy-chains modules 0x46 -> 0x45 -> 0x44
    LED strip C (D5) daisy-chains modules 0x48 -> 0x40 -> 0x41

    3x3 arrangement of modules -> 12x12 global grid, 4x4 per module:
      global row block = strip index      * 4   (A=rows 0-3, B=4-7, C=8-11)
      global col block = module position  * 4   (1st=cols 0-3, 2nd=4-7, 3rd=8-11)

    So tagging only needs a LOCAL 0-3, 0-3 position within whichever
    module block you're currently looking at; the tool adds the block
    offset to get the global coordinate automatically.

Requires the updated servo_calib.ino (adds "LN"/"LP" per-pixel LED
commands) to be flashed to the Arduino first.

CONTROLS
  Tab / Shift-Tab     switch strip (A/B/C)
  [ / ]               previous / next module in this strip's chain (1st/2nd/3rd)
  , / .               previous / next pixel index on this strip
  arrow keys          move the LOCAL cursor within the current 4x4 module block
  Enter / space       tag: link the current lit pixel to the cursor's cell,
                        then auto-advance to the next pixel
  s                   mark the current pixel as slack (not a grid cell), advance
  d                   delete any existing tag for the current pixel
  u                   step back one pixel (undo navigation, does not untag)
  C / V               increase / decrease this strip's LED_COUNT by 10
                        (sends "LN <strip> <count>" to resize the NeoPixel buffer)
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
MODULE_SIZE = 4          # each module is a 4x4 block of the global grid
DEFAULT_LED_COUNT = 150  # starting guess (~3 modules x 48 addressable each); adjust live with C/V

# Known physical wiring (confirm/correct against your hardware in the tool;
# this only seeds the config, it isn't hardcoded logic).
DEFAULT_STRIPS = {
    "0": {"name": "A", "pin": 2, "modules": ["0x43", "0x42", "0x47"], "led_count": DEFAULT_LED_COUNT},
    "1": {"name": "B", "pin": 6, "modules": ["0x46", "0x45", "0x44"], "led_count": DEFAULT_LED_COUNT},
    "2": {"name": "C", "pin": 5, "modules": ["0x48", "0x40", "0x41"], "led_count": DEFAULT_LED_COUNT},
}

COLOR_PRESETS = {
    ord('1'): ("white", (255, 255, 255)),
    ord('2'): ("red", (255, 0, 0)),
    ord('3'): ("green", (0, 255, 0)),
    ord('4'): ("blue", (0, 0, 255)),
}

CONFIG_PATH_DEFAULT = "led_grid_config.json"


def autodetect_port():
    cands = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return cands[0] if cands else None


def load_config(path):
    cfg = {
        "grid_rows": GRID_ROWS,
        "grid_cols": GRID_COLS,
        "module_size": MODULE_SIZE,
        "strips": json.loads(json.dumps(DEFAULT_STRIPS)),  # deep copy
        "cells": {},
        "skipped": {"0": [], "1": [], "2": []},
    }
    if os.path.exists(path):
        with open(path) as f:
            loaded = json.load(f)
        cfg.update(loaded)
        cfg.setdefault("cells", {})
        cfg.setdefault("skipped", {"0": [], "1": [], "2": []})
    return cfg


def save_config(cfg, path):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=False)
        f.write("\n")
    print(f"   ✓ saved {path}")


class Link:
    """Minimal serial link for the LED-only protocol (L/LN/LP/LX). No
    watchdog heartbeat needed here — servo_calib.ino's stuck-on watchdog
    only ever touches servo channels, never LED strips."""

    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self._stop = False
        self._lock = threading.Lock()
        self._board_log_lock = threading.Lock()
        self._board_log = []   # unexpected replies (ERR/WATCHDOG), for the TUI to surface
        self.last_sent = None  # most recent command sent, for debugging
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
        """Return and clear the oldest unread ERR/WATCHDOG line from the
        board, or None. Call this after every keypress so problems the
        firmware reports (e.g. a rejected LP command) are actually visible
        instead of silently swallowed."""
        with self._board_log_lock:
            return self._board_log.pop(0) if self._board_log else None

    def send(self, cmd):
        with self._board_log_lock:
            self.last_sent = cmd   # what we most recently TRIED to send, for debugging
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

        self.strip = 0
        self.module_pos = {0: 0, 1: 0, 2: 0}
        self.abs_index = {0: 0, 1: 0, 2: 0}
        self.local_r = 0
        self.local_c = 0
        self.color_name, self.color = "white", (255, 255, 255)
        self.dirty = False
        self.msg = "Tag the lit pixel's cell, then it auto-advances. Tab switches strips."
        self._lit = None  # (strip, index) currently lit, so we can turn it off cleanly
        self.last_key = None  # raw curses key code of the most recent keypress, for debugging

        self._apply_led_counts()
        self._light_current()

    # ---- helpers ----------------------------------------------------
    def _strip_cfg(self, strip=None):
        return self.cfg["strips"][str(self.strip if strip is None else strip)]

    def _apply_led_counts(self):
        for s in range(3):
            count = int(self.cfg["strips"][str(s)].get("led_count", DEFAULT_LED_COUNT))
            self.link.send(f"LN {s} {count}")
            time.sleep(0.05)

    def _block_offset(self):
        row_block = self.strip * MODULE_SIZE
        col_block = self.module_pos[self.strip] * MODULE_SIZE
        return row_block, col_block

    def _global_cell(self):
        row_block, col_block = self._block_offset()
        return row_block + self.local_r, col_block + self.local_c

    def _light_current(self):
        # NeoPixel's show() disables interrupts for the whole transmission
        # (~1-2ms for a 60px strip), during which the Uno can't receive
        # Serial bytes. Firing the "off" and "on" LP commands back-to-back
        # with no gap risks the second command's bytes landing mid-show()
        # and arriving corrupted/truncated (shows up as "ERR LP <partial>",
        # or worse, a garbled-but-still-parseable command with wrong RGB
        # values — which is exactly the kind of thing that could look like
        # a pixel randomly turning red). At ~150px/strip, show() takes
        # noticeably longer than the 60px case this was first tuned
        # against, so the gap needs to scale with led_count, not be fixed.
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
        """Find the (row,col) key tagged to this (strip,index), or None."""
        for key, val in self.cfg["cells"].items():
            if val.get("strip") == strip and val.get("index") == index:
                return key
        return None

    # ---- actions ------------------------------------------------------
    def select_strip(self, delta):
        self.strip = (self.strip + delta) % 3
        self.local_r = self.local_c = 0
        self._light_current()
        self.msg = f"strip {self._strip_cfg()['name']} (pin D{self._strip_cfg()['pin']})"

    def select_module(self, delta):
        self.module_pos[self.strip] = (self.module_pos[self.strip] + delta) % 3
        self.local_r = self.local_c = 0
        addr = self._strip_cfg()["modules"][self.module_pos[self.strip]]
        self.msg = f"module {self.module_pos[self.strip] + 1}/3 = {addr}"

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
        idx = self.abs_index[self.strip]
        if idx not in self.cfg["skipped"][s]:
            self.cfg["skipped"][s].append(idx)
            self.dirty = True
        self.step_index(+1)
        # step_index() sets self.msg to describe the new pixel; prepend
        # confirmation that the PREVIOUS one was actually skipped, since
        # that assignment would otherwise get silently overwritten.
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

    # ---- drawing -----------------------------------------------------
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
        addr = sc["modules"][self.module_pos[self.strip]]
        idx = self.abs_index[self.strip]
        count = int(sc.get("led_count", DEFAULT_LED_COUNT))
        n_tagged = len(self.cfg["cells"])

        put(0, 1, "LED GRID CALIBRATION", bold)
        unsaved = "   *UNSAVED*" if self.dirty else ""
        put(0, max(30, w - 28), f"[{n_tagged}/{GRID_ROWS * GRID_COLS} cells tagged]",
            ok_a if n_tagged == GRID_ROWS * GRID_COLS else 0)
        put(1, 1, f"config: {self.config_path}{unsaved}", warn_a if self.dirty else 0)
        put(1, max(60, w - 40), f"key={self.last_key!r}  sent={self.link.last_sent!r}")

        put(3, 2, f">> STRIP {sc['name']} (D{sc['pin']})   module {self.module_pos[self.strip] + 1}/3 = {addr}"
                  f"   pixel {idx}/{count - 1}   color={self.color_name}", cur_a)

        # local 4x4 block
        row_block, col_block = self._block_offset()
        put(5, 2, f"local block (global rows {row_block}-{row_block + 3}, cols {col_block}-{col_block + 3}):", bold)
        for r in range(MODULE_SIZE):
            row_cells = []
            for c in range(MODULE_SIZE):
                key = f"{row_block + r},{col_block + c}"
                is_cursor = (r == self.local_r and c == self.local_c)
                mark = "@" if is_cursor else ("#" if key in self.cfg["cells"] else ".")
                row_cells.append(mark)
            put(6 + r, 4, "   ".join(row_cells), cur_a if any(x == "@" for x in row_cells) else 0)

        # global 12x12 overview
        put(5, 40, "global 12x12 (letter = strip, boxed = current module):", bold)
        for r in range(GRID_ROWS):
            line_chars = []
            for c in range(GRID_COLS):
                key = f"{r},{c}"
                if key in self.cfg["cells"]:
                    ch = self.cfg["strips"][str(self.cfg["cells"][key]["strip"])]["name"]
                else:
                    ch = "."
                in_current_block = (row_block <= r < row_block + MODULE_SIZE and
                                     col_block <= c < col_block + MODULE_SIZE)
                line_chars.append(f"[{ch}]" if in_current_block else f" {ch} ")
            put(6 + r, 40, "".join(line_chars))

        put(h - 6, 1, self.msg[: w - 2], warn_a if "removed" in self.msg or "!" in self.msg else ok_a)
        put(h - 4, 1, "Tab/S-Tab: strip   [ ]: module   , . : pixel   arrows: local cursor", 0)
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
        ord('['):        lambda: tui.select_module(-1),
        ord(']'):        lambda: tui.select_module(+1),
        ord('.'):        lambda: tui.step_index(+1),
        ord(','):        lambda: tui.step_index(-1),
        ord('u'):        lambda: tui.step_index(-1),
        ord('U'):        lambda: tui.step_index(-1),
        curses.KEY_UP:    lambda: tui.move_cursor(-1, 0),
        curses.KEY_DOWN:  lambda: tui.move_cursor(+1, 0),
        curses.KEY_LEFT:  lambda: tui.move_cursor(0, -1),
        curses.KEY_RIGHT: lambda: tui.move_cursor(0, +1),
        ord(' '):        tui.tag,
        10:              tui.tag,   # Enter
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
            tui.last_key = k   # always recorded, shown in the header, regardless of outcome
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
        sys.exit("No serial port found. Pass --port /dev/cu.usbmodemXXXX")

    print(f"Connecting to {port} @ {args.baud} ...")
    link = Link(port, args.baud)
    link.open_wait()
    # Force every strip off before doing anything else. Opening the serial
    # port does not reliably reset the board on every Arduino (notably the
    # R4), so pixels left lit by a PREVIOUS script/session can still be
    # showing — there's no way to query current LED state from the board,
    # so the only safe move is to explicitly clear it ourselves.
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
