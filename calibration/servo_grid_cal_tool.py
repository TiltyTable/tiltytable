#!/usr/bin/env python3
"""
servo_grid_cal_tool.py — interactive calibration tool that maps each
PCA9685 board+channel to a global (row, col) coordinate on the 12x12
tilt-table grid, ALIGNED against the LED grid (led_grid_config.json).

CURRENT SOURCE OF TRUTH: the saved `servo_grid_config.json` (with
`led_grid_config.json`) IS the trusted global orientation map. Both share
the same (row, col) keys. Runtime tools should join on those keys — do
not invent a formula or assume the grids disagree.

HOW THIS FILE WAS BUILT: an early formula that assumed each PCA9685
shared its module's orientation with the co-located LED strip segment
was wrong (servos were rotated ~-90° relative to that seating). This
tool therefore tags by visual confirm: light a candidate cell's LED
("crosshair") while wiggling the channel, and tag only when they match.
Seeds still use BOARD_BLOCKS (+ optional SERVO_SEED_ROTATION_DEG) as a starting guess only.

SEEDING (a hypothesis, not ground truth): to avoid needing 144 manual
full-grid searches, each channel starts with a SEEDED guess from
BOARD_BLOCKS + column-grouping (+ optional SERVO_SEED_ROTATION_DEG).
This seed is usually close, sometimes already correct, but it is ONLY a
starting point: always confirm against the lit LED (or, if that cell has
no LED tag, by counting modules on the physical table) before tagging,
and freely move the guess anywhere on the full 12x12 grid with the arrow
keys if the seed looks wrong.

SANITY CHECK ON FIRST USE: watch the very first channel you wiggle. If
the lit LED is nowhere near the tile that moved — e.g. roughly the
OPPOSITE side of the table — quit without saving, adjust
SERVO_SEED_ROTATION_DEG (try ±90), and restart. If it's close but off by
one module, the seed is still useful — just nudge with arrows before
tagging.

IDENTIFYING A CHANNEL: pressing 'g' (go/wiggle) jogs the currently
selected channel around its calibrated NEUTRAL point — up to neutral +
half the distance to extended, back to neutral, down to neutral - half
the distance to recessed, back to neutral — if that channel already has
a calibrated envelope in its servo_config_0x4X.json. Falls back to a
small, symmetric default nudge around a generic center for channels with
no calibration yet.

Requires servo_calib.ino already flashed (same "A"/"P"/"O"/"E"/"LP"/"LN"
protocol as servo_tool.py / led_cal_tool.py). Resizes every LED strand in
led_grid_config.json (9 module strips) to its real led_count on connect —
the Uno resets on serial open (DTR auto-reset), so strips come back at the
firmware's 8-pixel default and LP commands to any higher index would
otherwise silently do nothing (the exact bug already found and fixed in
tilt_table_cli.py). Also runs the same background heartbeat servo_tool.py
uses, so the firmware's 5s stuck-on watchdog never fires during normal use.

PERSISTENT STATUS COLORS: beyond the one white "crosshair" LED that
tracks your current guess, every cell keeps a standing color visible on
the actual table at all times, so progress is visible at a glance without
reading the terminal: GREEN = channel tagged/mapped here, RED = channel
marked faulty here, colorless/off = not yet mapped (move over these).
The crosshair briefly shows white on top of whatever cell you're
currently guessing, then reverts to that cell's real status color the
moment you move off it or advance to the next channel.

CONTROLS
  Tab / Shift-Tab     next / previous board (0x40..0x48)
  , / .               previous / next channel (0-15) on this board
  arrow keys          move the guess cell anywhere on the FULL 12x12 grid
                        (re-lights the corresponding LED as you move)
  g                   wiggle (identify) the current channel again
  l                   re-light the current guess cell's LED
  Enter / space       tag: link the current channel to the guess cell
                        (turns that cell GREEN), then auto-advance
  f                   toggle FAULTY on the current channel (turns the
                        guess cell RED; press again to un-mark), advances
  s                   mark the current channel as not-on-the-grid, advance
  d                   delete any existing tag for the current channel
                        (turns that cell back off)
  u                   step back one channel (undo navigation, does not untag)
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
NUM_CHANNELS = 16

HARD_MIN, HARD_MAX = 0, 3000
NUDGE_LO, NUDGE_HI = 1400, 1700   # gentle default wiggle for an uncalibrated channel
POSITION_KEYS = ("recessed", "neutral", "extended")

HEARTBEAT_INTERVAL_S = 2.0   # keep servo_calib.ino's 5s stuck-on watchdog from ever firing

# Board -> (row_block, col_block) in the trusted global frame
# ((0,0)=top-left, row↓, col→). Derived from servo_grid_config.json after
# the 2026-07-11 origin remap. Seed only — cells JSON is ground truth.
BOARD_BLOCKS = {
    "0x43": (0, 0),  "0x46": (0, 4),  "0x48": (0, 8),
    "0x42": (4, 0),  "0x45": (4, 4),  "0x40": (4, 8),
    "0x47": (8, 0),  "0x44": (8, 4),  "0x41": (8, 8),
}
BOARD_ORDER = ["0x40", "0x41", "0x42", "0x43", "0x44", "0x45", "0x46", "0x47", "0x48"]

# Known column grouping from the servo layout diagram (top header order
# 15..0 fans into 4 columns of 4 channels each) — feeds the seed only.
def guessed_local_col(ch):
    return 3 - (ch // 4)

# Extra seed rotation inside BOARD_BLOCKS. Kept at 0 after the origin remap
# (blocks already match tagged cells). If a fresh re-seed is clearly mirrored,
# try ±90 here before retagging.
SERVO_SEED_ROTATION_DEG = 0

CONFIG_PATH_DEFAULT = "servo_grid_config.json"
LED_CONFIG_DEFAULT = "led_grid_config.json"
SERVO_CONFIG_GLOB = "servo_config_0x{:02x}.json"


def rotate_grid_point(row, col, degrees, size):
    """Rotate (row, col) within a size x size grid by a multiple of 90
    degrees. Each +90 step maps (r, c) -> (size-1-c, r); negative degrees
    step the other way. This is a SEED-only transform (see module
    docstring) — never treated as ground truth without the LED
    confirmation step."""
    steps = (int(degrees) // 90) % 4
    r, c = row, col
    for _ in range(steps):
        r, c = size - 1 - c, r
    return r, c


def seed_cell(addr, ch):
    """Starting guess for (addr, ch): old block+column-grouping guess,
    then rotated by SERVO_SEED_ROTATION_DEG. Clamped defensively even
    though a 90-degree rotation of in-bounds points can't go out of
    bounds — cheap insurance if BOARD_BLOCKS is ever hand-edited."""
    row_block, col_block = BOARD_BLOCKS.get(addr, (0, 0))
    old_row, old_col = row_block, col_block + guessed_local_col(ch)
    row, col = rotate_grid_point(old_row, old_col, SERVO_SEED_ROTATION_DEG, GRID_ROWS)
    return max(0, min(GRID_ROWS - 1, row)), max(0, min(GRID_COLS - 1, col))


def autodetect_port():
    cands = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return cands[0] if cands else None


def load_grid_config(path):
    cfg = {
        "grid_rows": GRID_ROWS,
        "grid_cols": GRID_COLS,
        "module_size": MODULE_SIZE,
        "cells": {},         # "row,col" -> {"address": "0x43", "channel": 5}  (GREEN)
        "faulty_cells": {},  # "row,col" -> {"address": "0x43", "channel": 5}  (RED)
        "skipped": {addr: [] for addr in BOARD_ORDER},
    }
    if os.path.exists(path):
        with open(path) as f:
            loaded = json.load(f)
        cfg.update(loaded)
        cfg.setdefault("cells", {})
        cfg.setdefault("faulty_cells", {})
        cfg.setdefault("skipped", {addr: [] for addr in BOARD_ORDER})
        for addr in BOARD_ORDER:
            cfg["skipped"].setdefault(addr, [])
    return cfg


def save_grid_config(cfg, path):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=False)
        f.write("\n")
    print(f"   ✓ saved {path}")


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def load_servo_configs():
    """address(str, e.g. '0x43') -> {"servos": {"<ch>": {"recessed":...,...}}}"""
    out = {}
    for addr in BOARD_ORDER:
        out[addr] = load_json(SERVO_CONFIG_GLOB.format(int(addr, 16)), {"servos": {}})
    return out


def wiggle_targets(servo_cfg, addr, ch):
    """Return (neutral, lo, hi) for the identification wiggle: lo/hi are
    NEUTRAL +/- half the distance to this channel's calibrated recessed /
    extended points. If only one side is calibrated, the other side
    mirrors it. Returns None if there's no calibrated neutral to pivot
    around, or neither recessed nor extended to measure a distance from."""
    s = servo_cfg.get(addr, {}).get("servos", {}).get(str(ch))
    if not s or "neutral" not in s:
        return None
    neutral = s["neutral"]
    half_up = (s["extended"] - neutral) / 2 if "extended" in s else None
    half_down = (neutral - s["recessed"]) / 2 if "recessed" in s else None
    if half_up is None and half_down is None:
        return None
    if half_up is None:
        half_up = half_down
    if half_down is None:
        half_down = half_up
    return neutral, neutral - half_down, neutral + half_up


# --------------------------------------------------------------------------
class Link:
    """Same shape as servo_tool.py's Link: background reader + heartbeat so
    the firmware's stuck-on watchdog never fires while this process runs."""

    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self._stop = False
        self._send_lock = threading.Lock()
        self._board_log_lock = threading.Lock()
        self._board_log = []
        self.last_sent = None
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

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
                if not text:
                    continue
                if text.startswith("ERR") or text.startswith("WATCHDOG"):
                    with self._board_log_lock:
                        self._board_log.append(text)

    def _heartbeat_loop(self):
        while not self._stop:
            time.sleep(HEARTBEAT_INTERVAL_S)
            if self._stop:
                break
            try:
                self.send("E")
            except Exception:
                break

    def pop_board_message(self):
        with self._board_log_lock:
            return self._board_log.pop(0) if self._board_log else None

    def send(self, cmd):
        with self._board_log_lock:
            self.last_sent = cmd
        with self._send_lock:
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


class ServoGridCalTUI:
    def __init__(self, link, cfg, config_path, servo_cfg, led_cfg):
        self.link = link
        self.cfg = cfg
        self.config_path = config_path
        self.servo_cfg = servo_cfg
        self.led_cfg = led_cfg
        self._strip_led_counts = {
            int(s): int(v.get("led_count", 50))
            for s, v in led_cfg.get("strips", {}).items()
        }

        self.board_i = 0
        self.channel = {addr: 0 for addr in BOARD_ORDER}
        self.guess_row = 0
        self.guess_col = 0
        self.dirty = False
        self.msg = "Press g to wiggle the current channel. Tab switches boards."
        self._active_addr = None
        self._lit_cell = None   # (row, col) currently showing the white crosshair

        self._paint_all_status()   # show existing GREEN/RED progress before anything else
        self._select_board(0, initial=True)

    # ---- helpers ----------------------------------------------------
    def _addr(self):
        return BOARD_ORDER[self.board_i]

    def _cell_at(self, addr, ch):
        for key, val in self.cfg["cells"].items():
            if val.get("address") == addr and val.get("channel") == ch:
                return key
        return None

    def _faulty_key_for(self, addr, ch):
        for key, val in self.cfg.get("faulty_cells", {}).items():
            if val.get("address") == addr and val.get("channel") == ch:
                return key
        return None

    def _ensure_board_selected(self):
        addr = self._addr()
        if addr != self._active_addr:
            self.link.send(f"A {addr}")
            time.sleep(0.3)
            self._active_addr = addr

    def _led_ref(self, row, col):
        c = self.led_cfg.get("cells", {}).get(f"{row},{col}")
        return (c["strip"], c["index"]) if c else None

    def _status_color(self, row, col):
        """Persistent status color for a cell: GREEN if a channel is
        tagged there, RED if a channel is marked faulty there, or None
        (colorless/off) if neither — the standing state that's visible on
        the physical table at all times, independent of the one white
        crosshair that tracks the current guess."""
        key = f"{row},{col}"
        if key in self.cfg["cells"]:
            return (0, 255, 0)
        if key in self.cfg.get("faulty_cells", {}):
            return (255, 0, 0)
        return None

    def _restore_color(self, row, col):
        """Push (row,col)'s persistent status color to its LED — GREEN,
        RED, or off. Used whenever the white crosshair moves away from a
        cell, and whenever a tag/faulty mark changes on a cell that isn't
        currently the active guess."""
        ref = self._led_ref(row, col)
        if not ref:
            return
        strip, idx = ref
        r, g, b = self._status_color(row, col) or (0, 0, 0)
        self.link.send(f"LP {strip} {idx} {r} {g} {b}")
        time.sleep(max(0.03, self._strip_led_counts.get(strip, 50) * 0.0003))

    def _repaint(self, row, col):
        """Refresh (row,col)'s LED right now, UNLESS it's the currently
        active guess cell (which stays white until the guess itself
        moves — _light_guess() will resolve its color then)."""
        if (row, col) != (self.guess_row, self.guess_col):
            self._restore_color(row, col)

    def _paint_all_status(self):
        """Light every already-tagged cell GREEN and every already-faulty
        cell RED, all at once — run on startup so prior progress is
        visible on the physical table immediately, not just as you pass
        over each cell again."""
        for key in self.cfg.get("cells", {}):
            row, col = (int(x) for x in key.split(","))
            self._restore_color(row, col)
        for key in self.cfg.get("faulty_cells", {}):
            row, col = (int(x) for x in key.split(","))
            self._restore_color(row, col)

    def _light_guess(self):
        """Restore whatever cell the crosshair was on before (to its
        persistent GREEN/RED/off status), then light the LED at the
        current guess cell bright white, if the LED grid has a tag there.
        Returns True if something was actually lit."""
        if self._lit_cell is not None and self._lit_cell != (self.guess_row, self.guess_col):
            self._restore_color(*self._lit_cell)
        ref = self._led_ref(self.guess_row, self.guess_col)
        if not ref:
            self._lit_cell = None
            return False
        strip, idx = ref
        self.link.send(f"LP {strip} {idx} 255 255 255")
        time.sleep(max(0.03, self._strip_led_counts.get(strip, 50) * 0.0003))
        self._lit_cell = (self.guess_row, self.guess_col)
        return True

    def wiggle(self):
        self._ensure_board_selected()
        addr, ch = self._addr(), self.channel[self._addr()]
        targets = wiggle_targets(self.servo_cfg, addr, ch)
        if targets:
            neutral, lo, hi = targets
            note = ""
        else:
            neutral = (NUDGE_LO + NUDGE_HI) / 2
            lo, hi = NUDGE_LO, NUDGE_HI
            note = " (uncalibrated — small default nudge, not this servo's real range)"
        neutral = max(HARD_MIN, min(HARD_MAX, int(neutral)))
        lo = max(HARD_MIN, min(HARD_MAX, int(lo)))
        hi = max(HARD_MIN, min(HARD_MAX, int(hi)))
        for us in (neutral, hi, neutral, lo, neutral):
            self.link.send(f"P {ch} {us}")
            time.sleep(0.3)
        self.link.send(f"O {ch}")
        self.msg = (f"wiggled {addr} ch {ch} (neutral {neutral}, "
                    f"+{hi - neutral}/-{neutral - lo}us){note} — "
                    f"does the lit LED line up with the tile that moved?")

    def _describe_channel(self):
        addr, ch = self._addr(), self.channel[self._addr()]
        tag = self._cell_at(addr, ch)
        faulty = self._faulty_key_for(addr, ch)
        skipped = ch in self.cfg["skipped"].get(addr, [])
        s = f"{addr} ch {ch}"
        if tag:
            s += f" — already tagged as {tag}"
        elif faulty:
            s += f" — already marked FAULTY at {faulty}"
        elif skipped:
            s += " — already marked not-on-grid"
        return s

    def _seed_and_light(self):
        addr, ch = self._addr(), self.channel[self._addr()]
        self.guess_row, self.guess_col = seed_cell(addr, ch)
        lit = self._light_guess()
        note = "" if lit else "  (no LED tagged at the seeded cell — nudge to a lit cell to anchor, or judge by counting modules)"
        self.msg = self._describe_channel() + note

    # ---- actions ------------------------------------------------------
    def _select_board(self, delta, initial=False):
        if not initial:
            old_addr, old_ch = self._addr(), self.channel[self._addr()]
            self.link.send(f"O {old_ch}")
        self.board_i = (self.board_i + delta) % len(BOARD_ORDER)
        self._seed_and_light()
        self.wiggle()

    def select_board(self, delta):
        self._select_board(delta)

    def select_channel(self, delta):
        addr = self._addr()
        old_ch = self.channel[addr]
        self.link.send(f"O {old_ch}")
        self.channel[addr] = (old_ch + delta) % NUM_CHANNELS
        self._seed_and_light()
        self.wiggle()

    def move_guess(self, dr, dc):
        self.guess_row = max(0, min(GRID_ROWS - 1, self.guess_row + dr))
        self.guess_col = max(0, min(GRID_COLS - 1, self.guess_col + dc))
        lit = self._light_guess()
        self.msg = f"guess ({self.guess_row},{self.guess_col})" + ("" if lit else "  (no LED tagged here)")

    def relight(self):
        lit = self._light_guess()
        self.msg = "re-lit guess cell" if lit else "no LED tagged at this cell to light"

    def tag(self):
        addr, ch = self._addr(), self.channel[self._addr()]
        row, col = self.guess_row, self.guess_col
        key = f"{row},{col}"
        # A confirmed physical mapping supersedes an earlier "not on grid"
        # decision.  Keep the status mutually consistent so this channel is
        # not both selectable by cell and still reported as skipped later.
        if ch in self.cfg["skipped"].get(addr, []):
            self.cfg["skipped"][addr].remove(ch)
        old_faulty_key = self._faulty_key_for(addr, ch)
        if old_faulty_key:
            del self.cfg["faulty_cells"][old_faulty_key]
            if old_faulty_key != key:
                fr, fc = (int(x) for x in old_faulty_key.split(","))
                self._repaint(fr, fc)
        # A channel can only live at ONE cell at a time — if it was already
        # tagged somewhere else (e.g. re-tagged after the -90 rotation fix,
        # or just moved), drop that stale entry so the same (addr,ch) never
        # ends up duplicated under two different (row,col) keys. Without
        # this, build_global_sequence() in servo_tool.py double-counts the
        # channel and the old cell keeps showing a phantom "mapped" mark.
        old_tag_key = self._cell_at(addr, ch)
        if old_tag_key and old_tag_key != key:
            del self.cfg["cells"][old_tag_key]
            tr, tc = (int(x) for x in old_tag_key.split(","))
            self._repaint(tr, tc)
        self.cfg["cells"][key] = {"address": addr, "channel": ch}
        self.dirty = True
        confirm = f"tagged {addr} ch {ch} -> cell ({row},{col}) [GREEN]"
        self.select_channel(+1)
        # select_channel() overwrites self.msg describing the new channel;
        # prepend confirmation so it isn't silently lost. select_channel()
        # -> _seed_and_light() -> _light_guess() will restore THIS cell's
        # color once the guess moves off it, and it'll now resolve to
        # green since cfg["cells"] was just updated above.
        self.msg = f"{confirm} — " + self.msg

    def toggle_faulty(self):
        """Mark the current channel FAULTY at the current guess cell
        (turns it RED), or un-mark it if it's already flagged faulty.
        A faulty channel can't be jiggled to confirm alignment, so this
        just records wherever the guess cursor happens to be — move it
        first if you want a more precise location, or leave it at the
        seed if that's close enough for a "something's broken here" flag."""
        addr, ch = self._addr(), self.channel[self._addr()]
        existing = self._faulty_key_for(addr, ch)
        if existing:
            del self.cfg["faulty_cells"][existing]
            self.dirty = True
            er, ec = (int(x) for x in existing.split(","))
            self._repaint(er, ec)
            self.msg = f"un-marked {addr} ch {ch} faulty (was {existing})"
            return
        row, col = self.guess_row, self.guess_col
        key = f"{row},{col}"
        old_tag_key = self._cell_at(addr, ch)
        if old_tag_key:
            del self.cfg["cells"][old_tag_key]
            if old_tag_key != key:
                tr, tc = (int(x) for x in old_tag_key.split(","))
                self._repaint(tr, tc)
        self.cfg.setdefault("faulty_cells", {})[key] = {"address": addr, "channel": ch}
        self.dirty = True
        confirm = f"marked {addr} ch {ch} FAULTY at ({row},{col}) [RED]"
        self.select_channel(+1)
        self.msg = f"{confirm} — " + self.msg

    def skip(self):
        addr, ch = self._addr(), self.channel[self._addr()]
        if ch not in self.cfg["skipped"][addr]:
            self.cfg["skipped"][addr].append(ch)
            self.dirty = True
        note = f"{addr} ch {ch} marked not-on-grid"
        self.select_channel(+1)
        self.msg = f"{note} -> " + self.msg

    def delete_tag(self):
        addr, ch = self._addr(), self.channel[self._addr()]
        key = self._cell_at(addr, ch)
        if key:
            del self.cfg["cells"][key]
            self.dirty = True
            self.msg = f"removed tag for {addr} ch {ch} (was {key})"
            row, col = (int(x) for x in key.split(","))
            self._repaint(row, col)
        else:
            self.msg = f"{addr} ch {ch} has no tag to remove"

    def step_back(self):
        self.select_channel(-1)

    def save(self):
        try:
            save_grid_config(self.cfg, self.config_path)
        except OSError as ex:
            # Don't let a locked/permission-denied config file crash the
            # whole TUI — surface the error and leave `dirty` set so a
            # retry of 's' doesn't get skipped once the file is fixed.
            self.msg = f"! SAVE FAILED for {self.config_path}: {ex} — still unsaved, fix and press 's' again"
            return
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

        addr = self._addr()
        ch = self.channel[addr]
        n_tagged = len(self.cfg["cells"])
        n_faulty = len(self.cfg.get("faulty_cells", {}))
        total_cells = GRID_ROWS * GRID_COLS

        put(0, 1, "SERVO GRID ALIGNMENT (vs. LED grid)", bold)
        unsaved = "   *UNSAVED*" if self.dirty else ""
        put(0, max(38, w - 34), f"[{n_tagged} green / {n_faulty} red / {total_cells} total]",
            ok_a if n_tagged == total_cells else 0)
        put(1, 1, f"config: {self.config_path}{unsaved}", warn_a if self.dirty else 0)
        put(1, max(60, w - 40), f"sent={self.link.last_sent!r}")

        lit_note = "LED LIT (white)" if self._lit_cell == (self.guess_row, self.guess_col) else "no LED here"
        put(3, 2, f">> BOARD {addr} ({self.board_i + 1}/{len(BOARD_ORDER)})   channel {ch:>2}   "
                  f"guess ({self.guess_row},{self.guess_col}) [{lit_note}]", cur_a)

        put(5, 2, "global 12x12  (@ = guess cursor [white], # = tagged [green], "
                  "X = faulty [red], o = led-tagged only, . = neither):", bold)
        for r in range(GRID_ROWS):
            row_chars = []
            for c in range(GRID_COLS):
                key = f"{r},{c}"
                is_cursor = (r == self.guess_row and c == self.guess_col)
                tagged = key in self.cfg["cells"]
                faulty = key in self.cfg.get("faulty_cells", {})
                led_tagged = key in self.led_cfg.get("cells", {})
                mark = ("@" if is_cursor else
                        "#" if tagged else
                        "X" if faulty else
                        "o" if led_tagged else ".")
                row_chars.append(mark)
            put(6 + r, 4, " ".join(row_chars), cur_a if any(x == "@" for x in row_chars) else 0)

        put(h - 6, 1, self.msg[: w - 2], warn_a if ("removed" in self.msg or "!" in self.msg) else ok_a)
        put(h - 4, 1, "Tab/S-Tab: board   , . : channel   arrows: move guess (full grid)   g: wiggle   l: re-light", 0)
        put(h - 3, 1, "Enter/space: tag (GREEN)+advance   f: toggle faulty (RED)+advance   s: skip   d: delete tag   u: back", 0)
        put(h - 2, 1, "w: save   q: save & quit", 0)
        stdscr.refresh()


def _cal_main(stdscr, link, cfg, config_path, servo_cfg, led_cfg):
    curses.curs_set(0)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
    stdscr.keypad(True)
    tui = ServoGridCalTUI(link, cfg, config_path, servo_cfg, led_cfg)

    actions = {
        9:               lambda: tui.select_board(+1),   # Tab
        curses.KEY_BTAB: lambda: tui.select_board(-1),
        ord('.'):        lambda: tui.select_channel(+1),
        ord(','):        lambda: tui.select_channel(-1),
        ord('u'):        tui.step_back,
        ord('U'):        tui.step_back,
        curses.KEY_UP:    lambda: tui.move_guess(-1, 0),
        curses.KEY_DOWN:  lambda: tui.move_guess(+1, 0),
        curses.KEY_LEFT:  lambda: tui.move_guess(0, -1),
        curses.KEY_RIGHT: lambda: tui.move_guess(0, +1),
        ord('g'):        tui.wiggle,
        ord('G'):        tui.wiggle,
        ord('l'):        tui.relight,
        ord('L'):        tui.relight,
        ord('f'):        tui.toggle_faulty,
        ord('F'):        tui.toggle_faulty,
        ord(' '):        tui.tag,
        10:              tui.tag,   # Enter
        13:              tui.tag,
        ord('s'):        tui.skip,
        ord('S'):        tui.skip,
        ord('d'):        tui.delete_tag,
        ord('D'):        tui.delete_tag,
        ord('w'):        tui.save,
        ord('W'):        tui.save,
    }

    while True:
        board_msg = link.pop_board_message()
        if board_msg:
            tui.msg = f"! board: {board_msg}"
        tui.draw(stdscr)
        k = stdscr.getch()
        if k in (ord('q'), ord('Q')):
            if tui.dirty:
                tui.save()
            if not tui.dirty:
                return
            # save() failed (e.g. locked/permission-denied file) — require
            # a SECOND q/Q to quit anyway so nothing is lost silently.
            tui.draw(stdscr)
            tui.msg += "  — press q again to quit WITHOUT saving, or fix the issue and press w"
            tui.draw(stdscr)
            k2 = stdscr.getch()
            if k2 in (ord('q'), ord('Q')):
                return
            continue
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
    ap.add_argument("--led-config", default=LED_CONFIG_DEFAULT)
    args = ap.parse_args()

    locale.setlocale(locale.LC_ALL, "")
    cfg = load_grid_config(args.config)
    servo_cfg = load_servo_configs()
    led_cfg = load_json(args.led_config, {"cells": {}, "strips": {}})
    if not led_cfg.get("cells"):
        print(f"   note: {args.led_config} has no tagged cells — this tool can still seed "
              f"guesses, but can't visually confirm any of them. Run led_cal_tool.py first.")
    missing = [a for a in BOARD_ORDER if not servo_cfg.get(a, {}).get("servos")]
    if missing:
        print(f"   note: no servo_config_0x4X.json (or it's empty) for {missing} — "
              f"those boards' channels will only get the small default nudge, "
              f"not their real calibrated range. Run servo_tool.py calibrate first if you can.")

    port = args.port or autodetect_port()
    if not port:
        sys.exit("No serial port found. Pass --port /dev/cu.usbmodemXXXX")

    print(f"Connecting to {port} @ {args.baud} ...")
    link = Link(port, args.baud)
    link.open_wait()

    # Resize every LED strip to its real led_count BEFORE anything else —
    # the Uno just reset (DTR auto-reset on serial open) and comes back at
    # the firmware's 8-pixel-per-strip default, so LP confirmation
    # commands to most tagged cells would otherwise silently do nothing
    # (the exact bug already found and fixed in tilt_table_cli.py).
    for strip, count in {int(s): int(v.get("led_count", 50))
                          for s, v in led_cfg.get("strips", {}).items()}.items():
        link.send(f"LN {strip} {count}")
        time.sleep(0.05)

    try:
        curses.wrapper(_cal_main, link, cfg, args.config, servo_cfg, led_cfg)
    except curses.error as ex:
        print(f"TUI failed ({ex}). Try a larger terminal window.")
    finally:
        # Release every channel on every board we could have touched, and
        # clear every LED strip — never leave a servo energized/stalled or
        # a confirmation LED lit on exit.
        for addr in BOARD_ORDER:
            link.send(f"A {addr}")
            time.sleep(0.05)
            for ch in range(NUM_CHANNELS):
                link.send(f"O {ch}")
        link.send("LX")
        link.close()

    print(f"Config: {os.path.abspath(args.config)}")


if __name__ == "__main__":
    main()
