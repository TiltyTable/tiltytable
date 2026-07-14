#!/usr/bin/env python3
"""
tilt_table_cli.py — one interactive session that drives BOTH the LED grid
and the servo grid by global (row, col) coordinate, using the mappings
built by led_cal_tool.py (led_grid_config.json) and servo_grid_cal_tool.py
(servo_grid_config.json). This is the runtime/operational tool; the two
*_cal_tool.py scripts remain the ones you use to (re)build those mappings.

Interactive only — no one-shot mode, so the serial link (and the servo
heartbeat that keeps the firmware's stuck-on watchdog from firing) stays
open for the whole session instead of paying the ~2s Uno reset cost per
command.

COMMANDS  (row/col are global grid coords 0-11)
    <row> <col> led <r> <g> <b>       set that cell's LED to RGB (0-255)
    <row> <col> led off               turn off that cell's LED
    <row> <col> servo <pos>           move that cell's servo (rec/neu/ext)
    <row> <col> servo off             release that cell's servo (limp)
    <row> <col> off                   LED off AND servo released
    <row> <col> <r> <g> <b> <pos>     combined: LED color + servo position
    row <n> led <r> <g> <b>           set every mapped LED in row n
    row <n> servo <pos>               move every mapped servo in row n
    col <n> led <r> <g> <b>           set every mapped LED in column n
    col <n> servo <pos>               move every mapped servo in column n
    all off                           every mapped LED off + every mapped servo released
    all led <r> <g> <b>               set every mapped LED
    demo [period]                     random-cell demo, one action at a time — see DEMO MODE
                                         below (Ctrl-C to stop)
    list                              coverage summary + 12x12 map (L/S/B/.)
    help / q                          help / quit

Positions accepted: recessed/neutral/extended, or rec/neu/ext, or r/n/x.
SAFETY MARGIN: every "recessed"/"extended" move (including inside the demo)
is only ever driven to SAFETY_MARGIN (80%) of the calibrated distance from
that channel's own neutral point — never the full captured extreme.
"neutral" itself is unaffected (it's a single point, not an extreme).

SERVO POWER: every named servo move pulses PWM, waits briefly for arrival,
then RELEASES the channel (`O`). Servos are never left energized across the
REPL prompt — stalled holds burn them out. Demo mode already ends each
servo action with a release.

DEMO MODE: `demo` picks ONE random action every `period` seconds (default
1.2s) — either a servo or an LED, chosen at random — instead of driving
many cells together. Each servo action is a single self-contained cycle:
move to a random extreme (recessed or extended, margin-limited), jiggle
in place a few times, then RELEASE — so a servo is never left energized
across the sleep between actions, unlike the old module-sweep demo (which
held whole boards extended for the ~seconds it slept, and left them stuck
if you Ctrl-C'd mid-hold — that demo has been removed). Servo candidates
are grid-mapped cells from servo_grid_config.json that also have a
calibrated envelope in that board's servo_config_0x4X.json. LED actions
pick a random tagged cell from led_grid_config.json, set it to a random
color, and leave it lit — colors accumulate across the table over the
demo's run rather than resetting each cycle. Run "all off" to clear them
when you're done. Both grids share the same global (row, col) space.

SAFETY: on connect, every mapped LED is forced off and EVERY channel on
EVERY board is released (not just channels tagged in
servo_grid_config.json) — opening the serial port doesn't reliably reset
every board (notably the Uno R4), so state left over from a previous
session/script can still be showing/energized. The same full release
runs again on exit, and once more if the demo is Ctrl-C'd (defense in
depth — the demo's own per-action move+jiggle+release already leaves
nothing energized between actions, but this covers the rare case of an
interrupt landing mid-action). A background heartbeat runs for the whole
session so servo_calib.ino's 5s stuck-on watchdog never fires during
normal use, only if this process dies or the port drops.
"""

import argparse
import glob
import json
import os
import random
import sys
import threading
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is required:  pip3 install pyserial")

GRID_ROWS, GRID_COLS = 12, 12
NUM_CHANNELS = 16
HARD_MIN, HARD_MAX = 0, 3000
POSITION_KEYS = ("recessed", "neutral", "extended")
POS_ALIASES = {
    "rec": "recessed", "recessed": "recessed", "r": "recessed",
    "neu": "neutral", "neutral": "neutral", "n": "neutral",
    "ext": "extended", "extended": "extended", "x": "extended",
}
BOARD_ORDER = ["0x40", "0x41", "0x42", "0x43", "0x44", "0x45", "0x46", "0x47", "0x48"]
HEARTBEAT_INTERVAL_S = 2.0

# Global safety rule: "recessed"/"extended" moves are only ever driven to
# this fraction of the calibrated distance from a channel's own neutral
# point toward that extreme — never the full captured distance. Applies
# everywhere this tool moves a servo by named position, including inside
# the demo. "neutral" is unaffected — it's a single point, not an extreme.
SAFETY_MARGIN = 0.8

# After commanding a position, wait this long then release (limp). Never
# leave module servos energized — they stall and burn out under hold.
SERVO_SETTLE_S = 0.45


def margin_target(s, pos_key):
    """Return (microsecond_target, margin_applied) for pos_key, applying
    SAFETY_MARGIN to recessed/extended. Needs a calibrated "neutral" to
    use as the pivot for "80% of the distance FROM neutral" — if this
    channel has no neutral saved, margin_applied is False and the raw
    calibrated value is returned unmodified (so behavior degrades to
    exactly what it was before this rule existed, never something more
    aggressive), and the caller should surface that as a note rather than
    silently skip the safety margin."""
    target = s[pos_key]
    if pos_key == "neutral" or "neutral" not in s:
        return target, (pos_key == "neutral")
    neutral = s["neutral"]
    return neutral + SAFETY_MARGIN * (target - neutral), True
LED_CONFIG_DEFAULT = "led_grid_config.json"
SERVO_GRID_CONFIG_DEFAULT = "servo_grid_config.json"
SERVO_CONFIG_GLOB = "servo_config_0x{:02x}.json"


def autodetect_port():
    cands = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return cands[0] if cands else None


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


# --------------------------------------------------------------------------
class Link:
    """Same shape as servo_tool.py's Link — background reader + heartbeat so
    the firmware's stuck-on watchdog never fires while this session runs."""

    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self._stop = False
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
                if text.startswith("ERR") or text.startswith("WATCHDOG"):
                    print(f"   !! board: {text}")

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


# --------------------------------------------------------------------------
class TiltTable:
    """Wraps the two grid mappings + per-board servo calibration into
    (row, col)-addressed LED/servo control over one Link."""

    def __init__(self, link, led_cfg, servo_grid_cfg, servo_configs):
        self.link = link
        self.led_cfg = led_cfg
        self.servo_grid_cfg = servo_grid_cfg
        self.servo_configs = servo_configs   # addr -> {"servos": {...}}
        self._active_addr = None
        self._strip_led_counts = {
            int(s): int(v.get("led_count", 50))
            for s, v in led_cfg.get("strips", {}).items()
        }

    def apply_led_counts(self):
        """Resize every strip's NeoPixel buffer to its real led_count. This
        MUST run once per connection: opening the serial port resets the
        Uno (DTR auto-reset), which resets each strip back to
        servo_calib.ino's compiled default length (8 pixels) regardless of
        what a previous session set it to. Without this, LP commands to
        any pixel index >= 8 — i.e. almost every tagged cell — silently
        land out of range, which looks exactly like "servos move but LEDs
        never light" (led_cal_tool.py already does this in its own
        __init__; this tool was missing the equivalent call)."""
        for strip, count in self._strip_led_counts.items():
            self.link.send(f"LN {strip} {count}")
            time.sleep(0.05)

    # ---- lookups ----------------------------------------------------
    def led_at(self, row, col):
        c = self.led_cfg.get("cells", {}).get(f"{row},{col}")
        return (c["strip"], c["index"]) if c else None

    def servo_at(self, row, col):
        c = self.servo_grid_cfg.get("cells", {}).get(f"{row},{col}")
        return (c["address"], c["channel"]) if c else None

    def cells_in_row(self, row):
        return [(row, c) for c in range(GRID_COLS)]

    def cells_in_col(self, col):
        return [(r, col) for r in range(GRID_ROWS)]

    def all_cells(self):
        return [(r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)]

    # ---- low-level sends ---------------------------------------------
    def _ensure_board(self, addr):
        if addr != self._active_addr:
            self.link.send(f"A {addr}")
            time.sleep(0.3)
            self._active_addr = addr

    def set_led(self, row, col, rgb):
        loc = self.led_at(row, col)
        if not loc:
            return False
        strip, idx = loc
        r, g, b = rgb
        self.link.send(f"LP {strip} {idx} {r} {g} {b}")
        count = self._strip_led_counts.get(strip, 50)
        time.sleep(max(0.03, count * 0.0003))   # same show()-collision pacing as led_cal_tool.py
        return True

    def move_servo(self, row, col, pos_key):
        loc = self.servo_at(row, col)
        if not loc:
            return False
        addr, ch = loc
        s = self.servo_configs.get(addr, {}).get("servos", {}).get(str(ch))
        if not s or pos_key not in s:
            print(f"   ! ({row},{col}) -> {addr} ch {ch} has no '{pos_key}' calibrated in "
                  f"servo_config_{addr}.json — run servo_tool.py calibrate for that board first.")
            return False
        target, margin_applied = margin_target(s, pos_key)
        if pos_key != "neutral" and not margin_applied:
            print(f"   note: ({row},{col}) -> {addr} ch {ch} has no 'neutral' saved — can't "
                  f"apply the {int(SAFETY_MARGIN * 100)}% safety margin, driving the full "
                  f"calibrated '{pos_key}' value instead.")
        us = max(HARD_MIN, min(HARD_MAX, int(round(target))))
        self._ensure_board(addr)
        self.link.send(f"P {ch} {us}")
        time.sleep(SERVO_SETTLE_S)
        self.link.send(f"O {ch}")   # never leave energized
        return True

    def release_servo(self, row, col):
        loc = self.servo_at(row, col)
        if not loc:
            return False
        addr, ch = loc
        self._ensure_board(addr)
        self.link.send(f"O {ch}")
        return True

    def release_all_mapped_servos(self):
        for key in self.servo_grid_cfg.get("cells", {}):
            row, col = (int(x) for x in key.split(","))
            self.release_servo(row, col)

    def release_every_channel_on_every_board(self):
        """Grid-INDEPENDENT safety net: release (limp) all 16 channels on
        all 9 boards, full stop — regardless of what (if anything)
        servo_grid_config.json has tagged. This exists because run_demo_*
        functions move servos by direct board+channel addressing,
        bypassing the (row, col) grid entirely (deliberately — see the
        DEMO MODE note above). release_all_mapped_servos() only knows
        about grid-tagged cells, so with that grid empty or sparse it
        would release nothing a demo had actually energized. Always safe
        to call: releasing an uncalibrated or unused channel is a no-op
        on the hardware, never a move."""
        for addr in BOARD_ORDER:
            self.link.send(f"A {addr}")
            time.sleep(0.05)
            self._active_addr = addr
            for ch in range(NUM_CHANNELS):
                self.link.send(f"O {ch}")

    def all_off(self):
        for row, col in self.all_cells():
            self.set_led(row, col, (0, 0, 0))
        self.release_every_channel_on_every_board()


# ================================ COMMANDS ================================
# Split on "SAFETY:" (with the colon) rather than "SAFETY" alone, since the
# COMMANDS block itself now mentions "SAFETY MARGIN:" and would otherwise
# get truncated at that earlier, unrelated occurrence.
HELP = __doc__.split("COMMANDS")[1].split("SAFETY:")[0]
HELP = "COMMANDS" + HELP


def parse_selector(tokens):
    """tokens[0:] -> (selector, rest) where selector is one of
    ('cell', r, c) / ('row', n) / ('col', n) / ('all',)."""
    if not tokens:
        return None, tokens
    if tokens[0] == "row" and len(tokens) >= 2 and tokens[1].lstrip("-").isdigit():
        return ("row", int(tokens[1])), tokens[2:]
    if tokens[0] == "col" and len(tokens) >= 2 and tokens[1].lstrip("-").isdigit():
        return ("col", int(tokens[1])), tokens[2:]
    if tokens[0] == "all":
        return ("all",), tokens[1:]
    if len(tokens) >= 2 and tokens[0].lstrip("-").isdigit() and tokens[1].lstrip("-").isdigit():
        return ("cell", int(tokens[0]), int(tokens[1])), tokens[2:]
    return None, tokens


def resolve_cells(table, selector):
    kind = selector[0]
    if kind == "cell":
        return [(selector[1], selector[2])]
    if kind == "row":
        return table.cells_in_row(selector[1])
    if kind == "col":
        return table.cells_in_col(selector[1])
    if kind == "all":
        return table.all_cells()
    return []


def run_action(table, cells, rest):
    if not rest:
        print("   ! missing action — try 'led <r g b|off>', 'servo <pos|off>', or 'off'")
        return

    verb = rest[0].lower()

    if verb == "off":
        led_n = sum(1 for r, c in cells if table.set_led(r, c, (0, 0, 0)))
        srv_n = sum(1 for r, c in cells if table.release_servo(r, c))
        print(f"   off: {led_n}/{len(cells)} LEDs cleared, {srv_n}/{len(cells)} servos released")
        return

    if verb == "led":
        if len(rest) >= 2 and rest[1].lower() == "off":
            n = sum(1 for r, c in cells if table.set_led(r, c, (0, 0, 0)))
            print(f"   led off: {n}/{len(cells)} cells had an LED mapping")
            return
        if len(rest) == 4 and all(_is_int(x) for x in rest[1:4]):
            rgb = tuple(_clamp255(int(x)) for x in rest[1:4])
            n = sum(1 for r, c in cells if table.set_led(r, c, rgb))
            print(f"   led {rgb}: {n}/{len(cells)} cells had an LED mapping")
            return
        print("   ! usage: <selector> led <r> <g> <b>   or   <selector> led off")
        return

    if verb == "servo":
        if len(rest) < 2:
            print("   ! usage: <selector> servo <rec|neu|ext|off>")
            return
        pos = rest[1].lower()
        if pos in ("off", "release"):
            n = sum(1 for r, c in cells if table.release_servo(r, c))
            print(f"   servo off: {n}/{len(cells)} cells had a servo mapping")
            return
        if pos not in POS_ALIASES:
            print(f"   ! position must be one of {sorted(set(POS_ALIASES.values()))} (or off)")
            return
        n = sum(1 for r, c in cells if table.move_servo(r, c, POS_ALIASES[pos]))
        print(f"   servo -> {POS_ALIASES[pos]}: {n}/{len(cells)} cells had a servo mapping")
        return

    # combined shorthand: <r> <g> <b> <pos>
    if len(rest) == 4 and all(_is_int(x) for x in rest[0:3]) and rest[3].lower() in POS_ALIASES:
        rgb = tuple(_clamp255(int(x)) for x in rest[0:3])
        pos = POS_ALIASES[rest[3].lower()]
        led_n = sum(1 for r, c in cells if table.set_led(r, c, rgb))
        srv_n = sum(1 for r, c in cells if table.move_servo(r, c, pos))
        print(f"   combined {rgb} + {pos}: {led_n}/{len(cells)} LEDs set, {srv_n}/{len(cells)} servos moved")
        return

    print(f"   ? unrecognized action {rest!r} — type 'help'")


def _is_int(x):
    try:
        int(x); return True
    except ValueError:
        return False


def _clamp255(v):
    return max(0, min(255, v))


def print_list(table):
    led_n = sum(1 for r, c in table.all_cells() if table.led_at(r, c))
    srv_n = sum(1 for r, c in table.all_cells() if table.servo_at(r, c))
    both_n = sum(1 for r, c in table.all_cells() if table.led_at(r, c) and table.servo_at(r, c))
    total = GRID_ROWS * GRID_COLS
    print(f"   LED mapping:   {led_n}/{total}")
    print(f"   servo mapping: {srv_n}/{total}")
    print(f"   both mapped:   {both_n}/{total}")
    print("   L=led only  S=servo only  B=both  .=neither  (rows top->bottom, cols left->right)")
    for r in range(GRID_ROWS):
        row_marks = []
        for c in range(GRID_COLS):
            has_led = bool(table.led_at(r, c))
            has_servo = bool(table.servo_at(r, c))
            row_marks.append("B" if has_led and has_servo else "L" if has_led else "S" if has_servo else ".")
        print("   " + " ".join(row_marks))


def _servo_demo_candidates(table):
    """(row, col, address, channel, servo_dict) for every grid-mapped cell
    whose channel has a calibrated neutral (safety-margin pivot) and at
    least one of recessed/extended. Uses servo_grid_config.json + the
    matching servo_config_0x4X.json envelope — same global (row, col)
    space as the LED map."""
    out = []
    for row, col in table.all_cells():
        loc = table.servo_at(row, col)
        if not loc:
            continue
        addr, ch = loc
        s = table.servo_configs.get(addr, {}).get("servos", {}).get(str(ch))
        if not s:
            continue
        if "neutral" in s and ("extended" in s or "recessed" in s):
            out.append((row, col, addr, int(ch), s))
    return out


def do_random_servo(table):
    """One self-contained cycle on ONE random grid-mapped calibrated cell:
    move to a random extreme (margin-limited), jiggle in place a few times
    so it reads as "alive" on camera, then RELEASE. Nothing is left
    energized once this function returns — no hold-across-a-sleep like the
    old module-wave demo had, which is what left servos stuck if
    interrupted mid-cycle."""
    candidates = _servo_demo_candidates(table)
    if not candidates:
        print("   ! no grid-mapped servo has both a calibrated neutral and a recessed/extended "
              "point yet — check servo_grid_config.json and run servo_tool.py calibrate.")
        return
    row, col, addr, ch, s = random.choice(candidates)
    pos_key = random.choice([k for k in ("extended", "recessed") if k in s])
    target, _ = margin_target(s, pos_key)
    target = max(HARD_MIN, min(HARD_MAX, int(round(target))))

    table._ensure_board(addr)
    table.link.send(f"P {ch} {target}")
    time.sleep(0.35)
    for _ in range(3):                       # jiggle in place
        for delta in (35, -35):
            us = max(HARD_MIN, min(HARD_MAX, target + delta))
            table.link.send(f"P {ch} {us}")
            time.sleep(0.06)
    table.link.send(f"P {ch} {target}")
    time.sleep(0.15)
    table.link.send(f"O {ch}")               # always released before returning
    print(f"   ({row},{col}) servo {addr} ch {ch} -> {pos_key} ({target}us), jiggled, released")


def do_random_led(table):
    """Light ONE random tagged cell from led_grid_config.json a random
    color and leave it lit. Colors accumulate across the table as the
    demo runs rather than resetting each cycle; run "all off" in the REPL
    to clear them."""
    cells = list(table.led_cfg.get("cells", {}).items())
    if not cells:
        print("   ! led_grid_config.json has no tagged cells — run led_cal_tool.py first.")
        return
    key, val = random.choice(cells)
    strip, idx = val["strip"], val["index"]
    rgb = tuple(random.randint(30, 255) for _ in range(3))
    table.link.send(f"LP {strip} {idx} {rgb[0]} {rgb[1]} {rgb[2]}")
    count = table._strip_led_counts.get(strip, 50)
    time.sleep(max(0.03, count * 0.0003))
    print(f"   cell {key} (strip {strip} idx {idx}) -> {rgb}, stays lit")


def run_demo_random(table, period=1.2):
    """Repeatedly perform ONE random action (servo or LED, 50/50) every
    `period` seconds, instead of driving many cells together. See DEMO
    MODE in this file's docstring for the full rationale."""
    print(f"Demo: one random action every ~{period:.1f}s (servo move+jiggle+release, or LED "
          f"random-color-and-stay). Ctrl-C to stop.")
    try:
        while True:
            if random.random() < 0.5:
                do_random_servo(table)
            else:
                do_random_led(table)
            time.sleep(period)
    except KeyboardInterrupt:
        print("\nDemo stopped — releasing every servo channel on every board (defense in "
              "depth; each action already released its own servo). LEDs left as-is — "
              "run 'all off' to clear them.")
        table.release_every_channel_on_every_board()


def run_repl(table):
    print(HELP)
    while True:
        try:
            raw = input("[tilt] > ").strip()
        except EOFError:
            raw = "q"
        if not raw:
            continue
        low = raw.lower()
        if low in ("q", "quit", "exit"):
            return
        if low in ("help", "?"):
            print(HELP)
            continue
        if low == "list":
            print_list(table)
            continue

        tokens = raw.split()
        if tokens[0].lower() == "demo":
            rest = tokens[1:]
            period = 1.2
            if rest:
                try:
                    period = float(rest[0])
                except ValueError:
                    print("   ! usage: demo [period]")
                    continue
            run_demo_random(table, period)
            continue

        selector, rest = parse_selector(tokens)
        if selector is None:
            print(f"   ! can't parse {raw!r} — expected '<row> <col> ...', 'row N ...', 'col N ...', or 'all ...'")
            continue
        cells = resolve_cells(table, selector)
        run_action(table, cells, rest)


# ================================ MAIN ===================================
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--led-config", default=LED_CONFIG_DEFAULT)
    ap.add_argument("--servo-grid-config", default=SERVO_GRID_CONFIG_DEFAULT)
    args = ap.parse_args()

    led_cfg = load_json(args.led_config, {"cells": {}, "strips": {}})
    servo_grid_cfg = load_json(args.servo_grid_config, {"cells": {}})
    if not led_cfg.get("cells"):
        print(f"   note: {args.led_config} has no tagged cells yet — run led_cal_tool.py first.")
    if not servo_grid_cfg.get("cells"):
        print(f"   note: {args.servo_grid_config} has no tagged cells yet — run servo_grid_cal_tool.py first.")

    servo_configs = {}
    for addr in BOARD_ORDER:
        path = SERVO_CONFIG_GLOB.format(int(addr, 16))
        servo_configs[addr] = load_json(path, {"servos": {}})

    port = args.port or autodetect_port()
    if not port:
        sys.exit("No serial port found. Pass --port /dev/cu.usbmodemXXXX")

    print(f"Connecting to {port} @ {args.baud} ...")
    link = Link(port, args.baud)
    link.open_wait()

    table = TiltTable(link, led_cfg, servo_grid_cfg, servo_configs)

    # Resize every strip's NeoPixel buffer BEFORE anything else — the Uno
    # just reset (DTR auto-reset on serial open) and comes back at the
    # firmware's default 8-pixel-per-strip length, so LP commands to any
    # tagged cell beyond index 7 would otherwise silently do nothing.
    print("Resizing LED strips to their configured lengths ...")
    table.apply_led_counts()

    # Force a known-safe starting state: opening the serial port doesn't
    # reliably reset every board (notably the R4), so state left over from
    # a previous session/script can still be showing/energized. all_off()
    # releases EVERY channel on EVERY board (not just grid-mapped ones) —
    # see release_every_channel_on_every_board() for why that matters.
    print("Clearing all mapped LEDs and releasing every servo channel on every board ...")
    table.all_off()

    try:
        run_repl(table)
    finally:
        print("Releasing every servo channel on every board before exit ...")
        table.release_every_channel_on_every_board()
        link.close()


if __name__ == "__main__":
    main()
