#!/usr/bin/env python3
"""
servo_tool.py — calibrate and drive 16 SG90 linear actuators on a PCA9685,
using the `servo_calib.ino` firmware. ONE script, ONE config file.

All positions live in an address-specific JSON config
(default: servo_config_<i2c-address>.json):

    {
      "frequency_hz": 50,
      "i2c_address": "0x40",
      "servos": {
        "0": { "recessed": 900, "neutral": 1500, "extended": 2100 },   # microseconds
        ...
      }
    }

SUBCOMMANDS
    calibrate         interactive: jog each servo, tag rec/neu/ext, save.
    drive             interactive: move servos by NAME (e.g. "0 extended").
    drive <ch> <pos>  one-shot: move one channel to a named position, exit.

COMMON OPTIONS
    --port  /dev/cu.usbmodemXXXX   (auto-detected if omitted)
    --baud  115200
    --config servo_config_0x40.json
    --i2c-address 0x40             (PCA9685 address; hex or decimal)

EXAMPLES
    python3 servo_tool.py calibrate                    # GLOBAL: whole 12x12 table, all 9 boards
    python3 servo_tool.py --i2c-address 0x43 calibrate  # just one board
    python3 servo_tool.py drive
    python3 servo_tool.py drive 3 extended

GLOBAL CALIBRATE MODE: for `calibrate` specifically, omitting BOTH
--i2c-address and --config loads every board's servo_config_0x4X.json at
once and lets you calibrate the ENTIRE table in one session. Arrow keys
select a mapped global grid cell and automatically switch to that cell's
PCA9685 board; Tab/Shift-Tab remains a continuous sequence through every
channel: every channel that has a
servo_grid_config.json tag first, in row-major global (row,col) order
(so you can just walk the physical grid), then any channel with NO grid
tag yet (the flaky/unmapped ones) grouped by board at the end — so
nothing is unreachable, it just comes last. Switching boards mid-session
sends the PCA9685 "A <addr>" select automatically. 's' saves every board
that has unsaved changes, not just the current one. Passing
--i2c-address (or --config) still gets you the original single-board
behavior, scoped to just that one board's 16 channels.

NOTE ON HOLDING POSITION: opening the serial port resets the Uno (standard
Uno DTR auto-reset), which re-limps every channel. So a one-shot `drive`
necessarily drops all other servos for ~2 s before commanding the one you
asked for. To hold many servos simultaneously, use interactive `drive` and
keep the session open. The PCA9685 latches PWM in hardware, so positions
persist as long as the board is not reset and OE stays enabled.

NOTE ON RANGES: calibrated recessed/neutral/extended values are per-servo
and can differ wildly between channels and boards — that's expected, not a
bug. Never assume a "typical" microsecond band applies across the board;
always prefer a channel's own calibrated values when they exist.

NOTE ON THE WATCHDOG: servo_calib.ino force-releases every channel if it
gets no host traffic at all for 5 seconds (guards against a servo being
left driven against a mechanical limit forever if the host crashes or the
USB link drops). This script runs a background heartbeat while any serial
link is open specifically so that watchdog never fires during normal use —
it only ever fires if this process actually dies or the port drops.
"""

import argparse
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


# Values are servo pulse widths in MICROSECONDS (firmware uses writeMicroseconds).
SOFT_MIN = 500     # µs — typical SG90 ~0° end; below this it usually buzzes
SOFT_MAX = 2500    # µs — typical SG90 ~180° end; above this it slams the stop
HARD_MIN, HARD_MAX = 0, 3000
NEUTRAL_US = 1500  # center
JOG_START  = 2000  # µs — where calibration jog begins on an uncalibrated channel
                   # (empirically near these SG90s' live travel; 1500 was dead)

NUM_CHANNELS = 16
GRID_ROWS, GRID_COLS = 12, 12
BOARD_ORDER = ["0x40", "0x41", "0x42", "0x43", "0x44", "0x45", "0x46", "0x47", "0x48"]
POSITION_KEYS = ("recessed", "neutral", "extended")
POS_ALIASES = {
    "rec": "recessed", "recessed": "recessed", "r": "recessed",
    "neu": "neutral", "neutral": "neutral", "n": "neutral",
    "ext": "extended", "extended": "extended", "x": "extended",
}
DEFAULT_CONFIG_PREFIX = "servo_config"

# How often to ping the board so its watchdog (5s timeout in the firmware)
# never fires while this process is alive and the link is open.
HEARTBEAT_INTERVAL_S = 2.0


def autodetect_port():
    cands = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return cands[0] if cands else None


def load_config(path):
    cfg = {"frequency_hz": 50, "i2c_address": "0x40", "servos": {}}
    if os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
        cfg.setdefault("servos", {})
        cfg.setdefault("i2c_address", "0x40")
    return cfg


def config_name_for_address(addr):
    return f"{DEFAULT_CONFIG_PREFIX}_{format_i2c_address(addr).lower()}.json"


def config_matches_address(path, addr):
    try:
        with open(path) as f:
            cfg = json.load(f)
        return parse_i2c_address(cfg.get("i2c_address", "0x40")) == addr
    except (OSError, json.JSONDecodeError, argparse.ArgumentTypeError):
        return False


def find_config_for_address(addr):
    addr_text = format_i2c_address(addr).lower()
    expected = config_name_for_address(addr)
    if os.path.exists(expected):
        return expected

    matches = []
    for path in glob.glob(f"{DEFAULT_CONFIG_PREFIX}_*.json"):
        name = os.path.basename(path).lower()
        if addr_text in name and config_matches_address(path, addr):
            matches.append(path)
    return sorted(matches)[0] if matches else expected


def save_config(cfg, path):
    out = dict(cfg)
    servos = out.get("servos", {})
    out["servos"] = {
        ch: servos[ch]
        for ch in sorted(servos, key=lambda x: (0, int(x)) if str(x).isdigit() else (1, str(x)))
    }
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print(f"   ✓ saved {path}")


def parse_i2c_address(value):
    try:
        addr = int(str(value), 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not an address; use 0x40-0x7F or 64-127"
        )
    if not 0x40 <= addr <= 0x7F:
        raise argparse.ArgumentTypeError(
            f"{value!r} is outside the PCA9685 address range 0x40-0x7F"
        )
    return addr


def format_i2c_address(addr):
    return f"0x{addr:02X}"


def calibrated_bounds(cfg, ch):
    """Return (lo, hi) from whatever of recessed/neutral/extended this
    channel has calibrated, or None if it has fewer than 2 saved points.
    This is the per-servo safe envelope — never assume a global band
    applies instead when this is available (ranges vary wildly per servo)."""
    s = cfg.get("servos", {}).get(str(ch))
    if not s:
        return None
    vals = [s[k] for k in POSITION_KEYS if k in s]
    if len(vals) < 2:
        return None
    return min(vals), max(vals)


def resolve_sweep_bounds(cfg, ch, default_lo, default_hi):
    """Prefer a channel's own calibrated envelope; fall back to the
    caller-provided default band only when nothing is calibrated yet for
    this channel (e.g. brand-new board bring-up)."""
    bounds = calibrated_bounds(cfg, ch)
    if bounds:
        return bounds[0], bounds[1], True
    return default_lo, default_hi, False


# ---- LED cross-reference (calibrate mode only) ---------------------------
# Ties this board's channels to led_grid_config.json + servo_grid_config.json
# — the mapping figured out in servo_grid_cal_tool.py — so the calibration
# TUI can show WHICH physical tile each channel is, and mark channels GREEN
# once their recessed/neutral/extended are all captured. Purely cosmetic:
# if either grid file is missing, or a channel has no servo-grid tag yet
# (including channels that turned out flaky and never got a reliable tag),
# this just degrades to no LED feedback for that channel — never an error.
STATUS_GREEN = (0, 255, 0)
STATUS_RED = (255, 0, 0)
CROSSHAIR_WHITE = (255, 255, 255)
LED_CONFIG_PATH = "led_grid_config.json"
SERVO_GRID_CONFIG_PATH = "servo_grid_config.json"


def load_json_default(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def build_channel_led_refs(i2c_address, servo_grid_cfg, led_cfg):
    """channel(int) -> (strip, index) or None, for THIS board — found by
    chaining servo_grid_config.json's board+channel -> global(row,col)
    with led_grid_config.json's global(row,col) -> strip,index."""
    addr = format_i2c_address(i2c_address)
    refs = {ch: None for ch in range(NUM_CHANNELS)}
    for key, val in servo_grid_cfg.get("cells", {}).items():
        ch = val.get("channel")
        if val.get("address") == addr and ch in refs:
            led = led_cfg.get("cells", {}).get(key)
            if led:
                refs[ch] = (led["strip"], led["index"])
    return refs


def build_global_sequence(servo_grid_cfg):
    """[(addr, ch, row_or_None, col_or_None), ...] — every physical
    channel across all 9 boards, in ONE walkable order: every
    servo_grid_config.json-tagged channel first, in row-major global
    (row,col) order (so Tab/Shift-Tab walks the physical grid), then any
    channel with NO grid tag yet grouped by board at the end. Nothing is
    ever left unreachable — untagged/flaky channels just come last."""
    by_cell = {}
    tagged = set()
    for key, val in servo_grid_cfg.get("cells", {}).items():
        addr, ch = val.get("address"), val.get("channel")
        if addr is None or ch is None:
            continue
        row, col = (int(x) for x in key.split(","))
        by_cell[(row, col)] = (addr, ch)
        tagged.add((addr, ch))

    sequence = []
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            if (row, col) in by_cell:
                addr, ch = by_cell[(row, col)]
                sequence.append((addr, ch, row, col))
    for addr in BOARD_ORDER:
        for ch in range(NUM_CHANNELS):
            if (addr, ch) not in tagged:
                sequence.append((addr, ch, None, None))
    return sequence


# --------------------------------------------------------------------------
class Link:
    """Serial link with a background reader that echoes board replies, plus
    a background heartbeat so the firmware's stuck-on watchdog never fires
    while this process is alive."""

    def __init__(self, port, baud, verbose=True):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self.verbose = verbose          # False = don't print board lines (TUI)
        self._stop = False
        self._last_val = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
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
                if text.startswith("VAL"):
                    p = text.split()
                    if len(p) == 3:
                        with self._lock:
                            self._last_val[int(p[1])] = int(p[2])
                if text.startswith("WATCHDOG"):
                    print(f"   !! {text} — the host link was silent too long. "
                          f"If this fires during normal use, something in this "
                          f"script hung; positions were dropped for safety.")
                elif self.verbose:
                    print(f"   < {text}")

    def _heartbeat_loop(self):
        while not self._stop:
            time.sleep(HEARTBEAT_INTERVAL_S)
            if self._stop:
                break
            try:
                self.send("E")
            except Exception:
                break

    def send(self, cmd):
        with self._send_lock:
            self.ser.write((cmd + "\n").encode())
            self.ser.flush()

    def get_count(self, ch, timeout=0.5):
        with self._lock:
            self._last_val.pop(ch, None)
        self.send(f"G {ch}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if ch in self._last_val:
                    return self._last_val[ch]
            time.sleep(0.02)
        return None

    def open_wait(self):
        """Ride out the auto-reset/bootloader, then enable outputs."""
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self.send("E")

    def set_i2c_address(self, addr):
        self.send(f"A {format_i2c_address(addr)}")
        time.sleep(0.3)

    def close(self):
        self._stop = True
        time.sleep(0.25)
        try:
            self.ser.close()
        except Exception:
            pass


def clamp_warn(count):
    """Absolute failsafe clamp (0-3000us hard limits of the firmware
    itself) — a last resort for corrupted config values. Prefer
    calibrated_bounds()/resolve_sweep_bounds() wherever a channel's own
    calibration is available."""
    count = max(HARD_MIN, min(HARD_MAX, int(count)))
    if count < SOFT_MIN or count > SOFT_MAX:
        print(f"   ! {count} is outside the typical SG90 band "
              f"({SOFT_MIN}-{SOFT_MAX}); this may be correct for this "
              f"specific servo's calibration — just confirm it's expected.")
    return count


# ===================== CALIBRATE — live keyboard TUI =====================
#
# No typing. Pick a servo, hold the arrow keys to jog it in real time, tap
# one key to capture each position. Everything is visible on one screen.
#
#   Left / Right   jog -10 / +10 us       ,  / .   jog -2 / +2 us (fine)
#   Up   / Down    jog +50 / -50 us       Tab/S-Tab  next / prev channel
#   r / n / e      tag Recessed/Neutral/Extended at the current count
#   1 / 2 / 3      go to saved rec / neu / ext
#   space          release current servo (limp — stops buzzing)
#   t              test: sweep current servo rec->neu->ext->neu
#   s              save config        q   save & quit

class CalTUI:
    def __init__(self, link, cfg, config_path, led_refs=None, strip_led_counts=None):
        self.link = link
        self.cfg = cfg
        self.config_path = config_path
        self.ch = 0
        self.count = self._neutral_of(0)
        self.dirty = False
        self.msg = "Hold arrow keys to jog the selected servo. Tab switches servos."

        self.led_refs = led_refs or {}                    # ch -> (strip, index) or None
        self._strip_led_counts = strip_led_counts or {}
        self._lit_ch = None                                # channel currently showing the white crosshair
        self._paint_all_status()
        self._light_current_led()

    def _neutral_of(self, ch):
        return self.cfg["servos"].get(str(ch), {}).get("neutral", JOG_START)

    def _servo(self, ch):
        return self.cfg["servos"].get(str(ch), {})

    def _is_done(self, ch):
        s = self._servo(ch)
        return all(k in s for k in POSITION_KEYS)

    def _is_faulty(self, ch):
        return bool(self._servo(ch).get("faulty"))

    # ---- LED cross-reference -------------------------------------------
    def _status_color(self, ch):
        """RED wins over GREEN — a faulty flag is a more urgent thing to
        see at a glance than "fully calibrated," even if both happen to
        be true (e.g. positions were captured before it turned out
        flaky)."""
        if self._is_faulty(ch):
            return STATUS_RED
        if self._is_done(ch):
            return STATUS_GREEN
        return None

    def _restore_led(self, ch):
        """Push ch's persistent status color (RED if faulty, GREEN if
        fully calibrated, off otherwise) to its LED, if it has one mapped."""
        ref = self.led_refs.get(ch)
        if not ref:
            return
        strip, idx = ref
        r, g, b = self._status_color(ch) or (0, 0, 0)
        self.link.send(f"LP {strip} {idx} {r} {g} {b}")
        time.sleep(max(0.03, self._strip_led_counts.get(strip, 150) * 0.0003))

    def _paint_all_status(self):
        """Light every already-flagged channel's LED (green or red), all
        at once, so resuming a session shows prior progress immediately."""
        for ch in range(NUM_CHANNELS):
            if self._status_color(ch):
                self._restore_led(ch)

    def _light_current_led(self):
        """Restore whichever channel's LED was showing the white
        crosshair before (to its real green/off status), then light the
        newly-selected channel's LED white, if it has one mapped."""
        if self._lit_ch is not None and self._lit_ch != self.ch:
            self._restore_led(self._lit_ch)
        ref = self.led_refs.get(self.ch)
        if not ref:
            self._lit_ch = None
            return
        strip, idx = ref
        r, g, b = CROSSHAIR_WHITE
        self.link.send(f"LP {strip} {idx} {r} {g} {b}")
        time.sleep(max(0.03, self._strip_led_counts.get(strip, 150) * 0.0003))
        self._lit_ch = self.ch

    def move(self, count):
        count = max(HARD_MIN, min(HARD_MAX, int(count)))
        self.count = count
        self.link.send(f"P {self.ch} {count}")
        if count < SOFT_MIN or count > SOFT_MAX:
            self.msg = f"! {count} outside typical SG90 band {SOFT_MIN}-{SOFT_MAX} - confirm this servo really needs that"
        else:
            self.msg = f"ch {self.ch}  ->  {count}"

    def jog(self, delta):
        self.move(self.count + delta)

    def select(self, ch):
        self.link.send(f"O {self.ch}")
        self.ch = ch % NUM_CHANNELS
        self.count = self._neutral_of(self.ch)
        self._light_current_led()
        led_note = "" if self.led_refs.get(self.ch) else "  (no LED mapped to this channel yet)"
        fault_note = "  — FLAGGED FAULTY" if self._is_faulty(self.ch) else ""
        self.msg = f"channel {self.ch} selected ({self.count} us) - jog to energize it{led_note}{fault_note}"

    def tag(self, key):
        self._servo(self.ch)  # ensure dict path via setdefault below
        self.cfg["servos"].setdefault(str(self.ch), {})[key] = self.count
        self.dirty = True
        extra = ""
        s = self.cfg["servos"][str(self.ch)]
        if all(k in s for k in POSITION_KEYS):
            r, n, e = (s[k] for k in POSITION_KEYS)
            if not ((r <= n <= e) or (r >= n >= e)):
                extra = "  (note: rec/neu/ext not in order)"
            extra += "  [LED will show GREEN once you move off this channel]"
        self.msg = f"captured ch {self.ch} {key} = {self.count}{extra}"

    def toggle_faulty(self):
        """Flag/un-flag the current channel as faulty (RED). Faulty wins
        over a green "done" status in _status_color(), so a broken
        channel stays visibly red even if it happens to have positions
        captured already."""
        s = self.cfg["servos"].setdefault(str(self.ch), {})
        s["faulty"] = not s.get("faulty")
        self.dirty = True
        if s["faulty"]:
            self.msg = f"ch {self.ch} FLAGGED FAULTY  [LED will show RED once you move off this channel]"
        else:
            self.msg = f"un-flagged ch {self.ch} faulty"
        # If this channel isn't the one currently showing the white
        # crosshair (no LED mapped here, so nothing was lit), push the
        # new status color now; if it IS the live crosshair, leave it
        # white — it'll resolve to red/green/off once the guess moves on.
        if self._lit_ch != self.ch:
            self._restore_led(self.ch)

    def goto(self, key):
        s = self._servo(self.ch)
        if key in s:
            self.move(s[key])
        else:
            self.msg = f"ch {self.ch} has no {key} captured yet"

    def release(self):
        self.link.send(f"O {self.ch}")
        self.msg = f"ch {self.ch} released (limp)"

    def save(self):
        try:
            save_config(self.cfg, self.config_path)
        except OSError as ex:
            # Never crash the whole TUI over one bad file (e.g. a locked/
            # permission-denied config on the host) — surface it and keep
            # `dirty` set so nothing is silently treated as saved when it
            # wasn't. Retry with 's' once the underlying issue is fixed.
            self.msg = f"! SAVE FAILED for {self.config_path}: {ex} — still unsaved, fix and press 's' again"
            return
        self.dirty = False
        self.msg = f"saved -> {self.config_path}"

    def test(self, stdscr):
        s = self._servo(self.ch)
        if not all(k in s for k in POSITION_KEYS):
            self.msg = "capture all three positions before testing"
            return
        for key in ("recessed", "neutral", "extended", "neutral"):
            self.move(s[key])
            self.msg = f"test: ch {self.ch} -> {key} ({s[key]})"
            self.draw(stdscr)
            curses.napms(700)

    # ---- drawing -----------------------------------------------------------
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

        done = sum(1 for ch in range(NUM_CHANNELS) if self._is_done(ch))
        bold = C.A_BOLD
        cur_a = C.color_pair(1) | bold
        ok_a = C.color_pair(2)
        warn_a = C.color_pair(3)

        put(0, 1, "PCA9685 SERVO CALIBRATION", bold)
        put(0, max(28, w - 16), f"[{done}/{NUM_CHANNELS} done]", ok_a if done == NUM_CHANNELS else 0)
        unsaved = "   *UNSAVED*" if self.dirty else ""
        put(1, 1, f"config: {self.config_path}{unsaved}", warn_a if self.dirty else 0)

        # --- current channel panel ---
        s = self._servo(self.ch)
        led_state = ("LED white (live)" if self.led_refs.get(self.ch) else "no LED mapped")
        put(3, 2, f">> CHANNEL {self.ch:>2}      {self.count:>4} us   [{led_state}]", cur_a)

        lo, hi, width = SOFT_MIN, SOFT_MAX, 44

        def posx(v):
            v = min(max(v, lo), hi)
            return int((v - lo) / (hi - lo) * (width - 1))

        track = list("." * width)
        for key, letter in (("recessed", "R"), ("neutral", "N"), ("extended", "E")):
            if key in s:
                track[posx(s[key])] = letter
        track[posx(self.count)] = "|"
        put(4, 4, str(lo))
        put(4, 8, "".join(track))
        put(4, 8 + width + 1, str(hi))

        def cell(key):
            return f"{key[:3]}={s.get(key, '----'):>4}" if key in s else f"{key[:3]}= ----"
        put(5, 8, "    ".join(cell(k) for k in POSITION_KEYS))

        # --- 16-channel table, two columns ---
        put(7, 2, "ch   rec   neu   ext", bold)
        put(7, 42, "ch   rec   neu   ext", bold)

        def fmt(i):
            si = self._servo(i)
            g = lambda k: (str(si[k]) if k in si else "--")
            return f"{i:>2}  {g('recessed'):>4}  {g('neutral'):>4}  {g('extended'):>4}"

        for i in range(8):
            for col, ch in ((2, i), (42, i + 8)):
                mark = ">" if ch == self.ch else " "
                done_mark = " *" if self._is_done(ch) else ""
                attr = cur_a if ch == self.ch else (ok_a if self._is_done(ch) else 0)
                put(8 + i, col, f"{mark}{fmt(ch)}{done_mark}", attr)

        # --- message + key legend ---
        put(h - 5, 1, self.msg[: w - 2],
            warn_a if self.msg.startswith("!") else ok_a)
        put(h - 3, 1, "Left/Right +/-10us  Up/Down +/-50us  , . +/-2us  r n e: tag  1 2 3: goto  f: toggle faulty", 0)
        put(h - 2, 1, "Tab: next channel    Shift-Tab/u: back    space: release    t: test    s: save    q: quit", 0)
        put(h - 1, 1, "LED: white = this channel (live)   green = fully captured   red = flagged faulty   off = not calibrated / no LED mapped", 0)
        stdscr.refresh()


def _cal_main(stdscr, link, cfg, config_path, led_refs, strip_led_counts):
    curses.curs_set(0)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)   # current
        curses.init_pair(2, curses.COLOR_GREEN, -1)                  # done
        curses.init_pair(3, curses.COLOR_YELLOW, -1)                 # warn
    stdscr.keypad(True)
    tui = CalTUI(link, cfg, config_path, led_refs, strip_led_counts)

    actions = {
        curses.KEY_RIGHT: lambda: tui.jog(+10),
        curses.KEY_LEFT:  lambda: tui.jog(-10),
        curses.KEY_UP:    lambda: tui.jog(+50),
        curses.KEY_DOWN:  lambda: tui.jog(-50),
        ord('.'): lambda: tui.jog(+2),
        ord(','): lambda: tui.jog(-2),
        ord('r'): lambda: tui.tag("recessed"),
        ord('n'): lambda: tui.tag("neutral"),
        ord('e'): lambda: tui.tag("extended"),
        ord('1'): lambda: tui.goto("recessed"),
        ord('2'): lambda: tui.goto("neutral"),
        ord('3'): lambda: tui.goto("extended"),
        ord(' '): tui.release,
        9:        lambda: tui.select(tui.ch + 1),   # Tab
        curses.KEY_BTAB: lambda: tui.select(tui.ch - 1),
        ord('u'): lambda: tui.select(tui.ch - 1),   # redundant back-nav
        ord('U'): lambda: tui.select(tui.ch - 1),
        ord('f'): tui.toggle_faulty,
        ord('F'): tui.toggle_faulty,
        ord('s'): tui.save,
    }

    while True:
        tui.draw(stdscr)
        k = stdscr.getch()
        if k in (ord('q'), ord('Q')):
            if tui.dirty:
                tui.save()
            if not tui.dirty:
                return
            # save() failed and left something unsaved — show the failure
            # and require a SECOND q/Q to quit anyway, so a locked file
            # can't cause silent data loss on exit.
            tui.draw(stdscr)
            tui.msg += "  — press q again to quit WITHOUT saving, or fix the issue and press s"
            tui.draw(stdscr)
            k2 = stdscr.getch()
            if k2 in (ord('q'), ord('Q')):
                return
            continue
        if k in (ord('t'), ord('T')):
            tui.test(stdscr)
        elif k in actions:
            actions[k]()


def run_calibrate(link, cfg, config_path, led_refs=None, strip_led_counts=None):
    locale.setlocale(locale.LC_ALL, "")
    link.verbose = False                 # keep board chatter off the TUI
    try:
        curses.wrapper(_cal_main, link, cfg, config_path, led_refs or {}, strip_led_counts or {})
    except curses.error as ex:
        print(f"TUI failed ({ex}). Try a larger terminal window.")
    print(f"Config: {os.path.abspath(config_path)}")
    link.send("X")
    link.send("LX")   # clear crosshair/status LEDs too, not just the servo


# ============= GLOBAL CALIBRATE (all 9 boards, whole 12x12) ==============
#
# Same per-channel workflow as CalTUI, but one global 12x12 selector.
# Arrow keys choose the physical grid cell, which immediately selects its
# board/channel. Tab/Shift-Tab can still walk EVERY channel in one continuous
# sequence (see build_global_sequence()). Jogging is on A/D and Z/X (plus
# comma/period fine adjustment), leaving arrows exclusively for cell selection.

class GlobalCalTUI:
    def __init__(self, link, configs, config_paths, sequence, led_cfg, strip_led_counts):
        self.link = link
        self.configs = configs                # addr -> cfg dict
        self.config_paths = config_paths       # addr -> path
        self.sequence = sequence               # [(addr, ch, row_or_None, col_or_None), ...]
        self.pos_i = 0
        self.dirty = {addr: False for addr in configs}
        self.msg = "Arrow keys select a cell; A/D and Z/X jog its servo."
        self._active_hw_addr = None

        self.led_cfg = led_cfg
        self._strip_led_counts = strip_led_counts
        self._lit_pos = None       # (row, col) currently showing the white crosshair
        self._cell_lookup = {(r, c): (a, ch) for a, ch, r, c in sequence if r is not None}

        self.count = JOG_START
        self._paint_all_status()
        self._ensure_board_and_led()

    # ---- current position ---------------------------------------------
    def _cur(self):
        return self.sequence[self.pos_i]

    @property
    def addr(self):
        return self._cur()[0]

    @property
    def ch(self):
        return self._cur()[1]

    def _cfg(self):
        return self.configs[self.addr]

    def _servo(self):
        return self._cfg()["servos"].get(str(self.ch), {})

    def _is_done(self, addr, ch):
        s = self.configs[addr]["servos"].get(str(ch), {})
        return all(k in s for k in POSITION_KEYS)

    def _is_faulty(self, addr, ch):
        return bool(self.configs[addr]["servos"].get(str(ch), {}).get("faulty"))

    def _status_color(self, addr, ch):
        """RED wins over GREEN — same rule as CalTUI._status_color()."""
        if self._is_faulty(addr, ch):
            return STATUS_RED
        if self._is_done(addr, ch):
            return STATUS_GREEN
        return None

    def _neutral_of(self):
        return self._servo().get("neutral", JOG_START)

    # ---- LED cross-reference -------------------------------------------
    def _led_ref_at(self, row, col):
        if row is None:
            return None
        c = self.led_cfg.get("cells", {}).get(f"{row},{col}")
        return (c["strip"], c["index"]) if c else None

    def _restore_led(self, row, col, addr, ch):
        ref = self._led_ref_at(row, col)
        if not ref:
            return
        strip, idx = ref
        r, g, b = self._status_color(addr, ch) or (0, 0, 0)
        self.link.send(f"LP {strip} {idx} {r} {g} {b}")
        time.sleep(max(0.03, self._strip_led_counts.get(strip, 150) * 0.0003))

    def _paint_all_status(self):
        for addr, ch, row, col in self.sequence:
            if row is not None and self._status_color(addr, ch):
                self._restore_led(row, col, addr, ch)

    def _light_current_led(self):
        addr, ch, row, col = self._cur()
        if self._lit_pos is not None and self._lit_pos != (row, col):
            lrow, lcol = self._lit_pos
            laddr, lch = self._cell_lookup.get((lrow, lcol), (None, None))
            if laddr is not None:
                self._restore_led(lrow, lcol, laddr, lch)
        ref = self._led_ref_at(row, col)
        if not ref:
            self._lit_pos = None
            return
        strip, idx = ref
        self.link.send(f"LP {strip} {idx} 255 255 255")
        time.sleep(max(0.03, self._strip_led_counts.get(strip, 150) * 0.0003))
        self._lit_pos = (row, col)

    # ---- board switching + jogging -------------------------------------
    def _ensure_board(self, addr):
        if addr != self._active_hw_addr:
            self.link.send(f"A {addr}")
            time.sleep(0.3)
            self._active_hw_addr = addr

    def _ensure_board_and_led(self):
        self._ensure_board(self.addr)
        self.count = self._neutral_of()
        self._light_current_led()

    def move(self, count):
        count = max(HARD_MIN, min(HARD_MAX, int(count)))
        self.count = count
        self.link.send(f"P {self.ch} {count}")
        if count < SOFT_MIN or count > SOFT_MAX:
            self.msg = f"! {count} outside typical SG90 band {SOFT_MIN}-{SOFT_MAX} - confirm this servo really needs that"
        else:
            self.msg = f"{self.addr} ch {self.ch}  ->  {count}"

    def jog(self, delta):
        self.move(self.count + delta)

    def move_cell(self, dr, dc):
        """Select a neighboring *physical* grid cell.

        A grid cell is the source of truth here: use its entry in
        servo_grid_config.json to resolve both PCA9685 address and channel,
        then select that board before any subsequent jog. Cells intentionally
        left unmapped (missing/failed servo) cannot be calibrated, so leave
        the current channel selected and say why rather than accidentally
        jogging the previous cell's servo under a new cursor.
        """
        _, _, row, col = self._cur()
        # Tab can visit an unmapped channel at the end of the global sequence.
        # In that case, resume arrow navigation from the nearest useful anchor.
        if row is None:
            row, col = 0, 0
        target = (max(0, min(GRID_ROWS - 1, row + dr)),
                  max(0, min(GRID_COLS - 1, col + dc)))
        mapped = self._cell_lookup.get(target)
        if mapped is None:
            self.msg = (f"global ({target[0]},{target[1]}) has no servo-grid mapping — "
                        "current servo remains selected")
            return

        addr, ch = mapped
        if (addr, ch) == (self.addr, self.ch):
            return
        self.link.send(f"O {self.ch}")
        # Keep pos_i in sync so saving/status/testing all operate on the
        # newly selected cell rather than merely changing a visual cursor.
        for i, item in enumerate(self.sequence):
            if item[:2] == (addr, ch):
                self.pos_i = i
                break
        self._ensure_board_and_led()
        self.msg = f"selected global ({target[0]},{target[1]}) -> {addr} ch {ch} — jog to energize it"

    def step(self, delta):
        old_addr, old_ch = self.addr, self.ch
        self.link.send(f"O {old_ch}")
        self.pos_i = (self.pos_i + delta) % len(self.sequence)
        self._ensure_board_and_led()
        addr, ch, row, col = self._cur()
        loc = f"global ({row},{col})" if row is not None else "NOT grid-mapped yet"
        done_note = " — already DONE" if self._is_done(addr, ch) else ""
        fault_note = "  — FLAGGED FAULTY" if self._is_faulty(addr, ch) else ""
        self.msg = f"{addr} ch {ch}  {loc}{done_note}{fault_note} — jog to energize it"

    def tag(self, key):
        addr, ch = self.addr, self.ch
        cfg = self._cfg()
        cfg["servos"].setdefault(str(ch), {})[key] = self.count
        self.dirty[addr] = True
        extra = ""
        s = cfg["servos"][str(ch)]
        if all(k in s for k in POSITION_KEYS):
            r, n, e = (s[k] for k in POSITION_KEYS)
            if not ((r <= n <= e) or (r >= n >= e)):
                extra = "  (note: rec/neu/ext not in order)"
            extra += "  [LED will show GREEN once you move off this channel]"
        self.msg = f"captured {addr} ch {ch} {key} = {self.count}{extra}"

    def toggle_faulty(self):
        """Flag/un-flag the current (addr, ch) as faulty (RED). Mirrors
        CalTUI.toggle_faulty() — red wins over green in _status_color()."""
        addr, ch = self.addr, self.ch
        s = self._cfg()["servos"].setdefault(str(ch), {})
        s["faulty"] = not s.get("faulty")
        self.dirty[addr] = True
        if s["faulty"]:
            self.msg = f"{addr} ch {ch} FLAGGED FAULTY  [LED will show RED once you move off this cell]"
        else:
            self.msg = f"un-flagged {addr} ch {ch} faulty"
        row, col = self._cur()[2], self._cur()[3]
        if self._lit_pos != (row, col):
            self._restore_led(row, col, addr, ch)

    def goto(self, key):
        s = self._servo()
        if key in s:
            self.move(s[key])
        else:
            self.msg = f"{self.addr} ch {self.ch} has no {key} captured yet"

    def release(self):
        self.link.send(f"O {self.ch}")
        self.msg = f"{self.addr} ch {self.ch} released (limp)"

    def save(self):
        pending = [addr for addr, is_dirty in self.dirty.items() if is_dirty]
        saved, failed = [], []
        for addr in pending:
            try:
                save_config(self.configs[addr], self.config_paths[addr])
            except OSError as ex:
                # One board's file being locked/permission-denied on the
                # host must not abort the whole save — keep going so every
                # OTHER dirty board still gets written, and keep this one's
                # dirty flag set so a retry of 's' doesn't skip it.
                failed.append((addr, ex))
                continue
            self.dirty[addr] = False
            saved.append(addr)
        parts = []
        if saved:
            parts.append(f"saved: {', '.join(saved)}")
        if failed:
            detail = ", ".join(f"{addr} ({ex})" for addr, ex in failed)
            parts.append(f"! FAILED (still unsaved): {detail}")
        self.msg = "  |  ".join(parts) if parts else "nothing to save"

    def test(self, stdscr):
        s = self._servo()
        if not all(k in s for k in POSITION_KEYS):
            self.msg = "capture all three positions before testing"
            return
        for key in ("recessed", "neutral", "extended", "neutral"):
            self.move(s[key])
            self.msg = f"test: {self.addr} ch {self.ch} -> {key} ({s[key]})"
            self.draw(stdscr)
            curses.napms(700)

    # ---- drawing --------------------------------------------------------
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

        total = len(self.sequence)
        done = sum(1 for addr, ch, row, col in self.sequence if self._is_done(addr, ch))
        dirty_addrs = [a for a, d in self.dirty.items() if d]

        put(0, 1, "PCA9685 SERVO CALIBRATION — GLOBAL (all 9 boards)", bold)
        put(0, max(50, w - 16), f"[{done}/{total} done]", ok_a if done == total else 0)
        put(1, 1, f"*UNSAVED*: {', '.join(dirty_addrs)}" if dirty_addrs else "all boards saved",
            warn_a if dirty_addrs else ok_a)

        addr, ch, row, col = self._cur()
        loc = f"global ({row},{col})" if row is not None else "NOT grid-mapped yet"
        led_state = "LED white (live)" if self._led_ref_at(row, col) else "no LED mapped"
        put(3, 2, f">> {addr} CH {ch:>2}   {loc}   {self.count:>4} us   [{led_state}]", cur_a)

        s = self._servo()
        lo, hi, width = SOFT_MIN, SOFT_MAX, 44

        def posx(v):
            v = min(max(v, lo), hi)
            return int((v - lo) / (hi - lo) * (width - 1))

        track = list("." * width)
        for key, letter in (("recessed", "R"), ("neutral", "N"), ("extended", "E")):
            if key in s:
                track[posx(s[key])] = letter
        track[posx(self.count)] = "|"
        put(4, 4, str(lo))
        put(4, 8, "".join(track))
        put(4, 8 + width + 1, str(hi))

        def cell(key):
            return f"{key[:3]}={s.get(key, '----'):>4}" if key in s else f"{key[:3]}= ----"
        put(5, 8, "    ".join(cell(k) for k in POSITION_KEYS))

        put(7, 2, "global 12x12 progress  (@ = current, # = done, X = faulty, o = grid-tagged not done, . = no grid tag):", bold)
        for r in range(GRID_ROWS):
            row_chars = []
            for c in range(GRID_COLS):
                is_cursor = (row == r and col == c)
                tup = self._cell_lookup.get((r, c))
                if tup:
                    a, tch = tup
                    if is_cursor:
                        mark = "@"
                    elif self._is_faulty(a, tch):
                        mark = "X"
                    elif self._is_done(a, tch):
                        mark = "#"
                    else:
                        mark = "o"
                else:
                    mark = "@" if is_cursor else "."
                row_chars.append(mark)
            put(8 + r, 4, " ".join(row_chars), cur_a if any(x == "@" for x in row_chars) else 0)

        put(h - 5, 1, self.msg[: w - 2], warn_a if self.msg.startswith("!") else ok_a)
        put(h - 3, 1, "arrows: select mapped cell  A/D +/-10us  Z/X +/-50us  , . +/-2us  r n e: tag  1 2 3: goto", 0)
        put(h - 2, 1, "Tab: next channel   Shift-Tab/u: previous   space: release   t: test   f: faulty   s: save all   q: quit", 0)
        put(h - 1, 1, "LED: white = current   green = fully captured   red = flagged faulty   off = not calibrated / no LED mapped", 0)
        stdscr.refresh()


def _global_cal_main(stdscr, link, configs, config_paths, sequence, led_cfg, strip_led_counts):
    curses.curs_set(0)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
    stdscr.keypad(True)
    tui = GlobalCalTUI(link, configs, config_paths, sequence, led_cfg, strip_led_counts)

    actions = {
        curses.KEY_RIGHT: lambda: tui.move_cell(0, +1),
        curses.KEY_LEFT:  lambda: tui.move_cell(0, -1),
        curses.KEY_UP:    lambda: tui.move_cell(-1, 0),
        curses.KEY_DOWN:  lambda: tui.move_cell(+1, 0),
        ord('a'): lambda: tui.jog(-10),
        ord('A'): lambda: tui.jog(-10),
        ord('d'): lambda: tui.jog(+10),
        ord('D'): lambda: tui.jog(+10),
        ord('z'): lambda: tui.jog(-50),
        ord('Z'): lambda: tui.jog(-50),
        ord('x'): lambda: tui.jog(+50),
        ord('X'): lambda: tui.jog(+50),
        ord('.'): lambda: tui.jog(+2),
        ord(','): lambda: tui.jog(-2),
        ord('r'): lambda: tui.tag("recessed"),
        ord('n'): lambda: tui.tag("neutral"),
        ord('e'): lambda: tui.tag("extended"),
        ord('1'): lambda: tui.goto("recessed"),
        ord('2'): lambda: tui.goto("neutral"),
        ord('3'): lambda: tui.goto("extended"),
        ord(' '): tui.release,
        9:        lambda: tui.step(+1),               # Tab
        curses.KEY_BTAB: lambda: tui.step(-1),
        ord('u'): lambda: tui.step(-1),             # redundant back-nav
        ord('U'): lambda: tui.step(-1),
        ord('f'): tui.toggle_faulty,
        ord('F'): tui.toggle_faulty,
        ord('s'): tui.save,
    }

    while True:
        tui.draw(stdscr)
        k = stdscr.getch()
        if k in (ord('q'), ord('Q')):
            if any(tui.dirty.values()):
                tui.save()
            if not any(tui.dirty.values()):
                return
            # one or more boards failed to save — require a SECOND q/Q to
            # quit anyway, so a locked/permission-denied file can't cause
            # silent data loss on exit (mirrors _cal_main's guard above).
            tui.draw(stdscr)
            tui.msg += "  — press q again to quit WITHOUT saving, or fix the issue and press s"
            tui.draw(stdscr)
            k2 = stdscr.getch()
            if k2 in (ord('q'), ord('Q')):
                return
            continue
        if k in (ord('t'), ord('T')):
            tui.test(stdscr)
        elif k in actions:
            actions[k]()


def run_global_calibrate(link, configs, config_paths, sequence, led_cfg, strip_led_counts):
    locale.setlocale(locale.LC_ALL, "")
    link.verbose = False
    try:
        curses.wrapper(_global_cal_main, link, configs, config_paths, sequence, led_cfg, strip_led_counts)
    except curses.error as ex:
        print(f"TUI failed ({ex}). Try a larger terminal window.")
    for path in config_paths.values():
        print(f"Config: {os.path.abspath(path)}")
    link.send("X")
    link.send("LX")


# ================================ DRIVE ==================================
def move_named(link, cfg, ch, pos_key):
    s = cfg["servos"].get(str(ch))
    if not s:
        print(f"   ! channel {ch} not in config"); return False
    if pos_key not in s:
        print(f"   ! channel {ch} has no '{pos_key}' saved"); return False
    count = clamp_warn(s[pos_key])
    link.send(f"P {ch} {count}")
    print(f"   → ch {ch} {pos_key} ({count})")
    return True


def parse_assignments(items, cfg):
    """Parse 'ch:pos' pairs (e.g. '12:extended 13:neu') -> [(ch, pos_key), ...].
    Validates channel is in config and the named position is saved."""
    out = []
    for item in items:
        if ":" not in item:
            raise ValueError(f"'{item}' must be CH:POSITION, e.g. 12:extended")
        ch_s, pos_s = item.split(":", 1)
        ch = int(ch_s)
        pos = pos_s.lower()
        if pos not in POS_ALIASES:
            raise ValueError(f"'{pos_s}' must be one of {list(POS_ALIASES)}")
        out.append((ch, POS_ALIASES[pos]))
    return out


def run_park(link, cfg, assignments, settle, interval):
    """Drive each servo to its named position, release power (limp), then
    periodically re-assert and release again. Friction holds the light load
    between refreshes; nothing is ever left energized or stalled.
    interval <= 0 means: park once and exit (servos left released)."""
    def assert_and_release():
        for ch, pos_key in assignments:
            if move_named(link, cfg, ch, pos_key):
                time.sleep(0.05)
        time.sleep(settle)            # let them arrive before cutting power
        for ch, _ in assignments:
            link.send(f"O {ch}")
        print(f"   released {len(assignments)} servo(s) (limp)")

    assert_and_release()
    if interval <= 0:
        return
    print(f"Holding: re-asserting every {interval:g}s. Ctrl-C to stop.")
    try:
        while True:
            time.sleep(interval)
            assert_and_release()
    except KeyboardInterrupt:
        print("\nStopped (servos left released).")


DRIVE_HELP = """\
   DRIVE COMMANDS  (positions: recessed|neutral|extended, or rec|neu|ext)
     <ch> <pos>      move channel to a named position   e.g.  3 extended
     all <pos>       move every configured channel to <pos>
     off <ch>        release a channel (limp)
     e / d / X       enable / disable / all-off
     list            show configured channels & positions
     help / q        help / quit
"""


def run_drive(link, cfg):
    if not cfg["servos"]:
        print("   ! config has no servos — run 'calibrate' first.")
    print(DRIVE_HELP)
    while True:
        try:
            raw = input("[drive] > ").strip()
        except EOFError:
            raw = "q"
        if not raw:
            continue
        p = raw.lower().split()
        cmd = p[0]
        if cmd.isdigit() and len(p) == 2 and p[1] in POS_ALIASES:
            move_named(link, cfg, int(cmd), POS_ALIASES[p[1]])
        elif cmd == "all" and len(p) == 2 and p[1] in POS_ALIASES:
            for ch_s in sorted(cfg["servos"], key=int):
                move_named(link, cfg, int(ch_s), POS_ALIASES[p[1]])
                time.sleep(0.05)
        elif cmd == "off" and len(p) == 2 and p[1].isdigit():
            link.send(f"O {int(p[1])}"); print(f"   ch {p[1]} released")
        elif cmd == "e":
            link.send("E")
        elif cmd == "d":
            link.send("D")
        elif cmd == "x":
            link.send("X")
        elif cmd == "list":
            for ch_s in sorted(cfg["servos"], key=int):
                s = cfg["servos"][ch_s]
                print(f"   ch {ch_s}: " + "  ".join(f"{k[:3]}={s.get(k, '—')}"
                                                    for k in POSITION_KEYS))
        elif cmd in ("help", "?"):
            print(DRIVE_HELP)
        elif cmd in ("quit", "q", "exit"):
            return
        else:
            print("   ? unknown — type 'help'")


# =============================== SWEEP ===================================
def run_sweep(link, cfg, ch, lo, hi, period):
    """Continuously sweep one channel between lo..hi microseconds until Ctrl-C.
    A clean way to confirm a single servo moves smoothly end-to-end.

    If this channel already has a calibrated envelope, that ALWAYS wins over
    the requested lo/hi — ranges vary wildly per servo, so a generic band can
    be wrong (too narrow to see movement, or wide enough to slam a stop) for
    any given channel."""
    eff_lo, eff_hi, calibrated = resolve_sweep_bounds(cfg, ch, lo, hi)
    if calibrated and (eff_lo, eff_hi) != (lo, hi):
        print(f"   note: ch {ch} is calibrated to {eff_lo}-{eff_hi}us — "
              f"using that instead of the requested {lo}-{hi}us.")
    lo, hi = eff_lo, eff_hi
    lo = max(HARD_MIN, min(HARD_MAX, lo))
    hi = max(HARD_MIN, min(HARD_MAX, hi))
    if lo > hi:
        lo, hi = hi, lo
    steps = 40
    dwell = max(0.01, period / (2 * steps))
    print(f"Sweeping channel {ch} between {lo} and {hi} "
          f"({period:.1f}s/cycle). Watch that one servo. Ctrl-C to stop.")
    try:
        while True:
            for i in range(steps + 1):                  # lo -> hi
                link.send(f"P {ch} {int(lo + (hi - lo) * i / steps)}")
                time.sleep(dwell)
            for i in range(steps + 1):                  # hi -> lo
                link.send(f"P {ch} {int(hi - (hi - lo) * i / steps)}")
                time.sleep(dwell)
    except KeyboardInterrupt:
        print("\nStopped.")


def parse_channels(spec):
    """Parse a channel spec like 'all', '12-15', '0,3,5', '12-15,2' -> sorted list."""
    if spec is None or spec.lower() == "all":
        return list(range(NUM_CHANNELS))
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            for ch in range(int(a), int(b) + 1):
                out.add(ch)
        elif part:
            out.add(int(part))
    chans = sorted(c for c in out if 0 <= c < NUM_CHANNELS)
    if not chans:
        raise ValueError(f"no valid channels in '{spec}'")
    return chans


def _sweep_cycle(link, bounds, period):
    """One back-and-forth sweep of the given channels (together), where
    each channel moves within its OWN (lo, hi) from `bounds` (a dict
    ch -> (lo, hi)). Moving in lockstep by normalized position (0..1)
    keeps channels with very different calibrated spans visually
    synchronized without ever pushing one outside its safe range."""
    steps = 30
    dwell = max(0.01, period / (2 * steps))

    def send_frac(frac):
        for ch, (lo, hi) in bounds.items():
            us = int(lo + (hi - lo) * frac)
            link.send(f"P {ch} {us}")
            time.sleep(0.003)          # pace so we don't overflow the Uno's RX buffer

    for i in range(steps + 1):                          # lo -> hi
        send_frac(i / steps)
        time.sleep(dwell)
    for i in range(steps + 1):                          # hi -> lo
        send_frac(1 - i / steps)
        time.sleep(dwell)


def run_sweep_all(link, cfg, channels, lo, hi, period, each):
    """Sweep the given channels through their full range until Ctrl-C.

    Each channel uses its OWN calibrated envelope when it has one; the
    requested --lo/--hi only apply as a fallback for channels with no
    calibration yet (e.g. fresh bring-up before anything's been jogged).

    each=False: all listed channels move together (load test).
    each=True : one channel at a time, announced, so you can see exactly
                which servos respond and which don't (per-channel diagnostic).
    """
    bounds = {}
    any_uncalibrated = False
    for ch in channels:
        b_lo, b_hi, calibrated = resolve_sweep_bounds(cfg, ch, lo, hi)
        b_lo = max(HARD_MIN, min(HARD_MAX, b_lo))
        b_hi = max(HARD_MIN, min(HARD_MAX, b_hi))
        if b_lo > b_hi:
            b_lo, b_hi = b_hi, b_lo
        bounds[ch] = (b_lo, b_hi)
        any_uncalibrated = any_uncalibrated or not calibrated

    if any_uncalibrated:
        print(f"   note: channel(s) without calibration use the generic "
              f"{lo}-{hi}us band; calibrated channels use their own recorded range.")

    label = ",".join(str(c) for c in channels)
    try:
        if each:
            print(f"Per-channel sweep of channels [{label}], each within its own "
                  f"safe range. Watch which servo moves as each is announced. Ctrl-C to stop.")
            while True:
                for ch in channels:
                    lo_c, hi_c = bounds[ch]
                    print(f"  >> testing channel {ch} ({lo_c}-{hi_c}us) ...", flush=True)
                    _sweep_cycle(link, {ch: bounds[ch]}, period)
                    link.send(f"O {ch}")                # release so it's obvious when the next starts
                    time.sleep(0.3)
        else:
            print(f"Sweeping channels [{label}] together, each within its own "
                  f"safe range ({period:.1f}s/cycle). Ctrl-C to stop.")
            while True:
                _sweep_cycle(link, bounds, period)
    except KeyboardInterrupt:
        print("\nStopped.")


# ================================ MAIN ===================================
def main():
    ap = argparse.ArgumentParser(description="PCA9685 servo calibrate/drive tool")
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--config", default=None,
                    help="config JSON path; if omitted, uses servo_config_<i2c-address>.json")
    ap.add_argument("--i2c-address", type=parse_i2c_address, default=None,
                    help="PCA9685 I2C address, e.g. 0x40 or 64 (default: config or 0x40)")
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("calibrate", help="interactive calibration (whole 12x12 table if "
                                      "--i2c-address AND --config are both omitted)")
    d = sub.add_parser("drive", help="drive servos by named position")
    d.add_argument("channel", nargs="?", type=int, help="one-shot: channel")
    d.add_argument("position", nargs="?", help="one-shot: rec/neu/ext")
    d.add_argument("--release", action="store_true",
                   help="after reaching the position, release the servo (limp)")
    pk = sub.add_parser("park", help="move servos to named positions, cut power, refresh periodically")
    pk.add_argument("assignments", nargs="+", metavar="CH:POS",
                    help="e.g. 12:extended 13:neutral 14:recessed")
    pk.add_argument("--settle", type=float, default=0.6,
                    help="seconds to wait for arrival before cutting power (default 0.6)")
    pk.add_argument("--interval", type=float, default=0.0,
                    help="re-assert every N seconds (0 = park once and exit; default 0)")
    sw = sub.add_parser("sweep", help="continuously sweep one channel (diagnostic)")
    sw.add_argument("channel", type=int, help="channel to sweep (0-15)")
    sw.add_argument("--lo", type=int, default=1000,
                    help="fallback low pulse width us if uncalibrated (default 1000)")
    sw.add_argument("--hi", type=int, default=2000,
                    help="fallback high pulse width us if uncalibrated (default 2000)")
    sw.add_argument("--period", type=float, default=2.0, help="seconds per full cycle")
    swa = sub.add_parser("sweepall", help="sweep channels through full range (diagnostic)")
    swa.add_argument("--channels", default="all",
                     help="channels to sweep: 'all', '12-15', '0,3,5' (default all)")
    swa.add_argument("--each", action="store_true",
                     help="sweep one channel at a time (announced) to diagnose which respond")
    swa.add_argument("--lo", type=int, default=SOFT_MIN,
                     help=f"fallback low us if uncalibrated (default {SOFT_MIN})")
    swa.add_argument("--hi", type=int, default=SOFT_MAX,
                     help=f"fallback high us if uncalibrated (default {SOFT_MAX})")
    swa.add_argument("--period", type=float, default=3.0, help="seconds per full cycle")
    args = ap.parse_args()

    global_mode = (args.mode == "calibrate" and args.i2c_address is None and args.config is None)
    if global_mode:
        servo_grid_cfg = load_json_default(SERVO_GRID_CONFIG_PATH, {"cells": {}})
        led_cfg = load_json_default(LED_CONFIG_PATH, {"cells": {}, "strips": {}})
        sequence = build_global_sequence(servo_grid_cfg)
        mapped_n = sum(1 for a, ch, r, c in sequence if r is not None)

        configs, config_paths = {}, {}
        for addr in BOARD_ORDER:
            addr_int = int(addr, 16)
            path = find_config_for_address(addr_int)
            cfg = load_config(path)
            cfg["i2c_address"] = addr
            if not os.path.exists(path):
                save_config(cfg, path)
            configs[addr] = cfg
            config_paths[addr] = path
        strip_led_counts = {int(s): int(v.get("led_count", 150))
                             for s, v in led_cfg.get("strips", {}).items()}

        print(f"GLOBAL calibrate: {mapped_n}/{len(sequence)} channels are grid-mapped "
              f"(walked by (row,col) first; {len(sequence) - mapped_n} unmapped channel(s) "
              f"come after, grouped by board).")

        port = args.port or autodetect_port()
        if not port:
            sys.exit("No serial port found. Pass --port /dev/cu.usbmodemXXXX")
        print(f"Connecting to {port} @ {args.baud} …")
        link = Link(port, args.baud)
        link.open_wait()
        for strip, count in strip_led_counts.items():
            link.send(f"LN {strip} {count}")
            time.sleep(0.05)
        try:
            run_global_calibrate(link, configs, config_paths, sequence, led_cfg, strip_led_counts)
        finally:
            link.close()
        return

    if args.config:
        cfg = load_config(args.config)
        config_path = args.config
        i2c_address = (args.i2c_address if args.i2c_address is not None
                       else parse_i2c_address(cfg.get("i2c_address", "0x40")))
    else:
        i2c_address = args.i2c_address if args.i2c_address is not None else 0x40
        config_path = find_config_for_address(i2c_address)
        cfg = load_config(config_path)
    cfg["i2c_address"] = format_i2c_address(i2c_address)
    if not args.config and not os.path.exists(config_path):
        save_config(cfg, config_path)

    port = args.port or autodetect_port()
    if not port:
        sys.exit("No serial port found. Pass --port /dev/cu.usbmodemXXXX")

    print(f"Connecting to {port} @ {args.baud} …")
    link = Link(port, args.baud)
    link.open_wait()
    print(f"Using PCA9685 I2C address {format_i2c_address(i2c_address)}")
    print(f"Using config {config_path}")
    link.set_i2c_address(i2c_address)

    one_shot = (args.mode == "drive" and args.channel is not None
                and args.position is not None)
    try:
        if args.mode == "calibrate":
            # Cross-reference this board's channels against
            # servo_grid_config.json + led_grid_config.json (the mapping
            # from servo_grid_cal_tool.py) so the calibration TUI can show
            # which physical tile each channel is, and mark fully
            # calibrated channels GREEN. Purely cosmetic — missing/empty
            # grid files, or channels with no grid tag yet, just mean no
            # LED feedback for those channels, never an error.
            led_cfg = load_json_default(LED_CONFIG_PATH, {"cells": {}, "strips": {}})
            servo_grid_cfg = load_json_default(SERVO_GRID_CONFIG_PATH, {"cells": {}})
            led_refs = build_channel_led_refs(i2c_address, servo_grid_cfg, led_cfg)
            strip_led_counts = {int(s): int(v.get("led_count", 150))
                                 for s, v in led_cfg.get("strips", {}).items()}
            mapped_n = sum(1 for v in led_refs.values() if v)
            print(f"LED cross-reference: {mapped_n}/{NUM_CHANNELS} channels on "
                  f"{format_i2c_address(i2c_address)} have a known LED position.")
            for strip, count in strip_led_counts.items():
                link.send(f"LN {strip} {count}")
                time.sleep(0.05)
            run_calibrate(link, cfg, config_path, led_refs, strip_led_counts)
        elif args.mode == "sweep":
            run_sweep(link, cfg, args.channel, args.lo, args.hi, args.period)
        elif args.mode == "park":
            run_park(link, cfg, parse_assignments(args.assignments, cfg),
                     args.settle, args.interval)
        elif args.mode == "sweepall":
            run_sweep_all(link, cfg, parse_channels(args.channels), args.lo, args.hi,
                          args.period, args.each)
        elif one_shot:
            pos = args.position.lower()
            if pos not in POS_ALIASES:
                print(f"   ! position must be one of {list(POS_ALIASES)}")
            else:
                move_named(link, cfg, args.channel, POS_ALIASES[pos])
                time.sleep(0.5)   # let the move register before we close
                if args.release:
                    link.send(f"O {args.channel}")
                    print(f"   ch {args.channel} released (limp)")
        else:
            run_drive(link, cfg)
    finally:
        # Leave servos holding their last commanded position on exit.
        link.close()


if __name__ == "__main__":
    main()
