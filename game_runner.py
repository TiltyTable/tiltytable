#!/usr/bin/env python3
"""
game_runner.py — apply a tile-map JSON to the physical 12x12 module grid.

Map format (see maps/): keys A1..L12 → cells with
  { "value": 1|0|-1, "color": "#RRGGBB", "dynamic": optional }

  value  1 → wall   (servo extended, margin-limited)
  value  0 → floor  (servo neutral)
  value -1 → pit    (servo recessed, margin-limited)
  color    → LED (mapped through calibration/led_palette.json +
             per-tile direct RGB overrides in led_color_cal.json)

  dynamic.intervalSeconds + dynamic.pattern[] → oscillate value/color
  forever (re-pulse each step; firmware auto-limps ~3s after each P).

  dynamic.type = "delayed_trap" → path tile blinks trapColor with accelerating
  LED cadence, then recesses to value -1 after armDelaySeconds + warnDurationSeconds.

  blinkUntilPlay: true → recessed until host/arcade calls begin_play (arcade only).

Usage:
  .venv/bin/python3 game_runner.py maps/tile-map-….json
  .venv/bin/python3 game_runner.py maps/dynamic-tile-map-….json
  .venv/bin/python3 game_runner.py maps/….json --once          # static apply, exit
  .venv/bin/python3 game_runner.py maps/….json --leds-only
  .venv/bin/python3 game_runner.py maps/….json --dry-run
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import threading
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is required: pip3 install pyserial")

ROOT = os.path.dirname(os.path.abspath(__file__))
CAL_DIR = os.path.join(ROOT, "calibration")
sys.path.insert(0, CAL_DIR)
from led_color import (  # noqa: E402
    load_cal,
    load_palette,
    nearest_palette_name,
    resolve_hex_or_name,
    hex_to_rgb,
    rgb_to_hex,
)

GRID_ROWS, GRID_COLS = 12, 12
BOARD_ORDER = [f"0x{a:02x}" for a in range(0x40, 0x49)]
HARD_MIN, HARD_MAX = 0, 3000
SAFETY_MARGIN = 0.8
SERVO_SETTLE_S = 0.45
HEARTBEAT_INTERVAL_S = 2.0

# Map value → calibrated named position
VALUE_TO_POS = {1: "extended", 0: "neutral", -1: "recessed"}

CELL_KEY_RE = re.compile(r"^([A-L])(\d{1,2})$", re.I)


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


def load_json(path):
    with open(path) as f:
        return json.load(f)


def hex_to_rgb(color: str):
    c = color.strip().lstrip("#")
    if len(c) != 6:
        raise ValueError(f"bad color {color!r}")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))


def cell_key_to_row_col(key: str):
    m = CELL_KEY_RE.match(key.strip())
    if not m:
        raise ValueError(f"bad cell key {key!r} (want A1..L12)")
    col = ord(m.group(1).upper()) - ord("A")
    row = int(m.group(2)) - 1
    if not (0 <= row < GRID_ROWS and 0 <= col < GRID_COLS):
        raise ValueError(f"cell {key!r} out of range")
    return row, col


def margin_us(servo: dict, pos_key: str) -> int:
    target = servo[pos_key]
    if pos_key == "neutral" or "neutral" not in servo:
        us = target
    else:
        n = servo["neutral"]
        us = n + SAFETY_MARGIN * (target - n)
    return max(HARD_MIN, min(HARD_MAX, int(round(us))))


def parse_map(raw: dict):
    """Return (static_cells, dynamic_cells).

    static_cells: list of dicts with row,col,value,color,rgb
    dynamic_cells: same + interval_s, pattern (list of {value,color,rgb}), step index
    """
    static, dynamic = [], []
    for key, cell in raw.items():
        if not isinstance(cell, dict) or "value" not in cell:
            continue
        row, col = cell_key_to_row_col(key)
        value = int(cell["value"])
        if value not in VALUE_TO_POS:
            raise ValueError(f"{key}: value {value} not in {{-1,0,1}}")
        color = cell.get("color", "#000000")
        rgb = hex_to_rgb(color)
        entry = {
            "key": key,
            "row": row,
            "col": col,
            "value": value,
            "color": color,
            "rgb": rgb,
        }
        dyn = cell.get("dynamic")
        if dyn:
            dyn_type = str(dyn.get("type", "cycle"))
            if dyn_type == "delayed_trap":
                entry["dyn_type"] = "delayed_trap"
                entry["arm_delay_s"] = float(dyn.get("armDelaySeconds", 4.0))
                entry["warn_duration_s"] = float(dyn.get("warnDurationSeconds", 6.0))
                entry["interval_s"] = float(dyn.get("initialIntervalSeconds", 1.2))
                entry["min_interval_s"] = float(dyn.get("minIntervalSeconds", 0.12))
                entry["trap_color"] = dyn.get("trapColor", "#FF0000")
                entry["floor_color"] = dyn.get("floorColor", color)
                entry["trapped"] = False
                entry["blink_on"] = False
                entry["next_t"] = 0.0
                if entry["arm_delay_s"] < 0 or entry["warn_duration_s"] <= 0:
                    raise ValueError(f"{key}: delayed_trap delays must be positive")
                if entry["interval_s"] <= 0 or entry["min_interval_s"] <= 0:
                    raise ValueError(f"{key}: delayed_trap intervals must be > 0")
                dynamic.append(entry)
                continue
            interval = float(dyn["intervalSeconds"])
            pattern = []
            for step in dyn["pattern"]:
                v = int(step["value"])
                if v not in VALUE_TO_POS:
                    raise ValueError(f"{key} pattern value {v} invalid")
                c = step.get("color", "#000000")
                pattern.append({"value": v, "color": c, "rgb": hex_to_rgb(c)})
            if not pattern:
                raise ValueError(f"{key}: empty dynamic pattern")
            if interval <= 0:
                raise ValueError(f"{key}: intervalSeconds must be > 0")
            entry["dyn_type"] = "cycle"
            entry["interval_s"] = interval
            entry["pattern"] = pattern
            entry["step"] = 0
            # Align step to current value if present in pattern
            for i, p in enumerate(pattern):
                if p["value"] == value and p["color"].lower() == color.lower():
                    entry["step"] = i
                    break
            dynamic.append(entry)
        else:
            static.append(entry)
    return static, dynamic


def tick_dynamic_cells(
    dynamic: list[dict],
    now: float,
    play_started: float | None = None,
) -> list[dict]:
    """Advance dynamic tiles; return cells that changed this tick."""
    updates: list[dict] = []
    for cell in dynamic:
        dyn_type = cell.get("dyn_type", "cycle")
        if dyn_type == "delayed_trap":
            if cell.get("trapped"):
                continue
            if play_started is None:
                continue
            elapsed = now - play_started
            arm_delay = cell["arm_delay_s"]
            warn_dur = cell["warn_duration_s"]
            if elapsed < arm_delay:
                continue
            if elapsed >= arm_delay + warn_dur:
                cell["trapped"] = True
                cell["value"] = -1
                cell["color"] = cell["trap_color"]
                cell["rgb"] = hex_to_rgb(cell["trap_color"])
                updates.append(cell)
                continue
            if now < cell.get("next_t", 0.0):
                continue
            progress = min(1.0, (elapsed - arm_delay) / warn_dur)
            interval = cell["interval_s"] - progress * (
                cell["interval_s"] - cell["min_interval_s"]
            )
            cell["blink_on"] = not cell.get("blink_on", False)
            cell["value"] = 0
            cell["color"] = cell["trap_color"] if cell["blink_on"] else cell["floor_color"]
            cell["rgb"] = hex_to_rgb(cell["color"])
            cell["next_t"] = now + max(cell["min_interval_s"], interval)
            updates.append(cell)
            continue
        if now < cell.get("next_t", 0.0):
            continue
        cell["step"] = (cell["step"] + 1) % len(cell["pattern"])
        step = cell["pattern"][cell["step"]]
        cell["value"] = step["value"]
        cell["color"] = step["color"]
        cell["rgb"] = step["rgb"]
        cell["next_t"] = now + cell["interval_s"]
        updates.append(cell)
    return updates


class Link:
    def __init__(self, port, baud, dry_run=False):
        self.dry_run = dry_run
        self._stop = False
        self._send_lock = threading.Lock()
        self._active_addr = None
        if dry_run:
            self.ser = None
            return
        self.ser = serial.Serial(port, baud, timeout=0.2)
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
                if text.startswith(("ERR", "WATCHDOG", "HOLD")):
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
        if self.dry_run:
            print(f"   [dry] {cmd}")
            return
        with self._send_lock:
            self.ser.write((cmd + "\n").encode())
            self.ser.flush()

    def open_wait(self):
        if self.dry_run:
            return
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self.send("E")

    def close(self):
        self._stop = True
        time.sleep(0.15)
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass

    def select_board(self, addr: str):
        if addr == self._active_addr:
            return
        self.send(f"A {addr}")
        if not self.dry_run:
            time.sleep(0.3)
        self._active_addr = addr


class Table:
    def __init__(
        self,
        link,
        led_cfg,
        servo_grid_cfg,
        servo_configs,
        palette=None,
        led_cal=None,
        module_start_delay_s=0.0,
    ):
        self.link = link
        self.led_cfg = led_cfg
        self.servo_grid_cfg = servo_grid_cfg
        self.servo_configs = servo_configs
        self.palette = palette if palette is not None else load_palette()
        self.led_cal = led_cal if led_cal is not None else load_cal()
        self.module_start_delay_s = max(0.0, float(module_start_delay_s))
        self._strip_led_counts = {
            int(s): int(v.get("led_count", 50))
            for s, v in led_cfg.get("strips", {}).items()
        }

    def apply_led_counts(self):
        for strip, count in self._strip_led_counts.items():
            self.link.send(f"LN {strip} {count}")
            if not self.link.dry_run:
                time.sleep(0.05)

    def all_off(self):
        self.link.send("LX")
        for addr in BOARD_ORDER:
            self.link.select_board(addr)
            for ch in range(16):
                self.link.send(f"O {ch}")
            if not self.link.dry_run:
                time.sleep(0.02)

    def led_at(self, row, col):
        c = self.led_cfg.get("cells", {}).get(f"{row},{col}")
        return (c["strip"], c["index"]) if c else None

    def servo_at(self, row, col):
        c = self.servo_grid_cfg.get("cells", {}).get(f"{row},{col}")
        return (c["address"], int(c["channel"])) if c else None

    def set_led(self, row, col, rgb):
        loc = self.led_at(row, col)
        if not loc:
            return False
        strip, idx = loc
        r, g, b = rgb
        self.link.send(f"LP {strip} {idx} {r} {g} {b}")
        return True

    def pulse_servo(self, row, col, value):
        loc = self.servo_at(row, col)
        if not loc:
            return False
        addr, ch = loc
        pos_key = VALUE_TO_POS[value]
        s = self.servo_configs.get(addr, {}).get("servos", {}).get(str(ch))
        if not s or pos_key not in s:
            print(f"   ! ({row},{col}) {addr} ch {ch} missing '{pos_key}'")
            return False
        us = margin_us(s, pos_key)
        self.link.select_board(addr)
        self.link.send(f"P {ch} {us}")
        return True

    def unstick_cell(
        self,
        row,
        col,
        *,
        lift_fraction=0.15,
        lift_s=0.18,
        neutral_s=0.10,
    ):
        """Briefly lift one tile, return it to neutral, then release."""
        loc = self.servo_at(row, col)
        if not loc:
            return False
        addr, ch = loc
        servo = self.servo_configs.get(addr, {}).get("servos", {}).get(str(ch))
        if not servo or "neutral" not in servo or "extended" not in servo:
            return False
        neutral_us = int(servo["neutral"])
        extended_us = int(servo["extended"])
        lift_us = round(
            neutral_us + max(0.0, min(1.0, lift_fraction)) * (
                extended_us - neutral_us
            )
        )
        self.link.select_board(addr)
        try:
            self.link.send(f"P {ch} {lift_us}")
            if not self.link.dry_run:
                time.sleep(max(0.0, lift_s))
            self.link.send(f"P {ch} {neutral_us}")
            if not self.link.dry_run:
                time.sleep(max(0.0, neutral_s))
        finally:
            self.link.send(f"O {ch}")
        return True

    def fill_all_leds(self, rgb):
        """Fill every configured strip with one raw RGB color."""
        r, g, b = (max(0, min(255, int(value))) for value in rgb)
        for strip in sorted(self._strip_led_counts):
            self.link.send(f"L {strip} {r} {g} {b}")

    def fill_all_leds_calibrated(self, color):
        """Fill the grid through each cell's palette calibration."""
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                rgb = self.cell_led_rgb(
                    {
                        "row": row,
                        "col": col,
                        "color": color,
                        "rgb": (0, 0, 0),
                    }
                )
                self.set_led(row, col, rgb)

    def average_led_rgb(self, color):
        samples = []
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                samples.append(
                    self.cell_led_rgb(
                        {"row": row, "col": col, "color": color, "rgb": (0, 0, 0)}
                    )
                )
        return tuple(
            round(sum(sample[channel] for sample in samples) / len(samples))
            for channel in range(3)
        )

    def cell_led_rgb(self, cell):
        """Map JSON color → LED RGB. Black/off stays unlit."""
        color = cell.get("color", "#000000")
        try:
            raw = hex_to_rgb(color if color.startswith("#") else f"#{color}")
            hex_norm = rgb_to_hex(raw)
        except ValueError:
            raw = cell.get("rgb", (0, 0, 0))
            hex_norm = rgb_to_hex(raw) if isinstance(raw, (list, tuple)) else "#000000"

        aliases = {
            k.upper(): v
            for k, v in self.palette.get("export_hex_aliases", {}).items()
        }
        name = aliases.get(hex_norm)
        if name is None and hex_norm == "#000000":
            name = "off"
        if name is None:
            name = nearest_palette_name(self.palette, raw)

        # Inactive / black tiles: no LED fill
        if name == "off" or hex_norm == "#000000":
            return (0, 0, 0)

        try:
            return resolve_hex_or_name(
                self.palette, self.led_cal, color,
                cell["row"], cell["col"],
            )
        except ValueError:
            return tuple(cell.get("rgb", (0, 0, 0)))

    def apply_cells(self, cells, leds_only=False):
        """Apply LED + servo for each cell. Batches servos by board.

        LED colors from the map hex are mapped through the game palette
        (export aliases / nearest name) then per-tile RGB gains so e.g.
        pit red looks consistent across diffuser tiles. Black/off
        tiles are left uncolored (LED off).
        """
        for cell in cells:
            rgb = self.cell_led_rgb(cell)
            ok = self.set_led(cell["row"], cell["col"], rgb)
            if not ok:
                print(f"   ! {cell['key']}: no LED mapping")

        if leds_only:
            return

        by_board = {}
        for cell in cells:
            loc = self.servo_at(cell["row"], cell["col"])
            if not loc:
                print(f"   ! {cell['key']}: no servo mapping")
                continue
            addr, ch = loc
            by_board.setdefault(addr, []).append(cell)

        module_started = False
        for addr in BOARD_ORDER:
            group = by_board.get(addr)
            if not group:
                continue
            if (
                module_started
                and self.module_start_delay_s > 0.0
                and not self.link.dry_run
            ):
                time.sleep(self.module_start_delay_s)
            self.link.select_board(addr)
            module_started = True
            for cell in group:
                loc = self.servo_at(cell["row"], cell["col"])
                _, ch = loc
                pos_key = VALUE_TO_POS[cell["value"]]
                s = self.servo_configs.get(addr, {}).get("servos", {}).get(str(ch))
                if not s or pos_key not in s:
                    print(f"   ! {cell['key']}: {addr} ch {ch} missing '{pos_key}'")
                    continue
                us = margin_us(s, pos_key)
                self.link.send(f"P {ch} {us}")
                if not self.link.dry_run:
                    time.sleep(0.01)
            if not self.link.dry_run:
                time.sleep(SERVO_SETTLE_S)
            for cell in group:
                loc = self.servo_at(cell["row"], cell["col"])
                if loc:
                    self.link.send(f"O {loc[1]}")


def load_table_configs():
    led_path = os.path.join(CAL_DIR, "led_grid_config.json")
    sg_path = os.path.join(CAL_DIR, "servo_grid_config.json")
    led_cfg = load_json(led_path)
    servo_grid_cfg = load_json(sg_path)
    servo_configs = {}
    for addr in BOARD_ORDER:
        path = os.path.join(CAL_DIR, f"servo_config_{addr}.json")
        if not os.path.exists(path):
            # try uppercase hex as saved on disk
            alt = os.path.join(CAL_DIR, f"servo_config_0x{int(addr, 16):02X}.json")
            path = alt if os.path.exists(alt) else path
        if os.path.exists(path):
            servo_configs[addr] = load_json(path)
        else:
            # also try lowercase filename used in repo
            lo = os.path.join(CAL_DIR, f"servo_config_0x{int(addr, 16):02x}.json")
            servo_configs[addr] = load_json(lo) if os.path.exists(lo) else {"servos": {}}
    # Normalize address keys in servo_grid cells to lowercase 0xNN
    return led_cfg, servo_grid_cfg, servo_configs


def normalize_addr(addr: str) -> str:
    return f"0x{int(str(addr), 0):02x}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("map", help="path to tile-map JSON")
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--once", action="store_true",
                    help="apply initial state only (no dynamic loop)")
    ap.add_argument("--leds-only", action="store_true",
                    help="drive LEDs only (no servo motion)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print actions, no serial")
    args = ap.parse_args()

    map_path = args.map
    if not os.path.isabs(map_path) and not os.path.exists(map_path):
        alt = os.path.join(ROOT, map_path)
        if os.path.exists(alt):
            map_path = alt
    raw = load_json(map_path)
    static, dynamic = parse_map(raw)
    print(f"Map {map_path}: {len(static)} static, {len(dynamic)} dynamic cell(s)")

    led_cfg, servo_grid_cfg, servo_configs = load_table_configs()
    # Normalize servo grid addresses to lowercase for lookup
    cells = {}
    for k, v in servo_grid_cfg.get("cells", {}).items():
        if not v:
            continue
        cells[k] = {"address": normalize_addr(v["address"]), "channel": v["channel"]}
    servo_grid_cfg = {**servo_grid_cfg, "cells": cells}
    servo_configs = {normalize_addr(a): cfg for a, cfg in servo_configs.items()}

    port = args.port or autodetect_port()
    if not args.dry_run and not port:
        sys.exit("No serial port; pass --port /dev/arduino-modules")

    link = Link(port or "dry", args.baud, dry_run=args.dry_run)
    table = Table(link, led_cfg, servo_grid_cfg, servo_configs)
    try:
        print(f"Connecting to {port or '(dry-run)'} …")
        link.open_wait()
        if not args.dry_run:
            print("Resizing LED strips …")
            table.apply_led_counts()
            print("Clearing LEDs + releasing all servos …")
            table.all_off()

        initial = static + dynamic
        print(f"Applying {len(initial)} cell(s) …")
        table.apply_cells(initial, leds_only=args.leds_only)
        print("Initial state applied.")

        if args.once or not dynamic:
            if not dynamic:
                print("No dynamic tiles — done.")
            return

        print(f"Dynamic loop: {len(dynamic)} tile(s). Ctrl-C to stop.")
        now = time.monotonic()
        play_started = now
        for d in dynamic:
            if d.get("dyn_type", "cycle") == "cycle":
                d["next_t"] = now + d["interval_s"]

        while True:
            now = time.monotonic()
            updates = tick_dynamic_cells(dynamic, now, play_started=play_started)
            if updates:
                for d in updates:
                    if d.get("dyn_type") == "delayed_trap":
                        print(f"   {d['key']} -> delayed_trap value={d['value']} {d['color']}")
                    else:
                        print(
                            f"   {d['key']} -> value={d['value']} {d['color']} "
                            f"(step {d['step'] + 1}/{len(d['pattern'])})"
                        )
                table.apply_cells(updates, leds_only=args.leds_only)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        try:
            if not args.dry_run:
                # Keep LEDs lit after --once so you can inspect the map;
                # always release servos so they don't stall.
                if args.once or not dynamic:
                    print("Releasing servos (LEDs left as displayed) …")
                    table.link.send("O")
                else:
                    print("Releasing all servos + clearing LEDs …")
                    table.all_off()
        finally:
            link.close()


if __name__ == "__main__":
    main()
