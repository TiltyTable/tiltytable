#!/usr/bin/env python3
"""Run the calibrated servos for green LED cells, one cell at a time.

Cells are visited from (0, 0) in row-major order.  For each eligible cell,
the servo is commanded to recessed, extended, then neutral, with 100 ms
between commands.  A cell is eligible when it has both LED and servo-grid
tags and its persistent LED color is not red or yellow.

Run from the repository root or from this directory:

    python3 calibration/run_green_cell_sequence.py

The script uses the same serial protocol and servo timeout behavior as
servo_tool.py.  Ctrl-C releases all servo channels before exiting.
"""

import argparse
import json
import os
import sys
import time

try:
    from servo_tool import (
        BOARD_ORDER,
        LED_CONFIG_PATH,
        SERVO_GRID_CONFIG_PATH,
        Link,
        autodetect_port,
        find_config_for_address,
        format_i2c_address,
        load_config,
        parse_i2c_address,
    )
except ImportError:
    # Support execution from the repository root as well as calibration/.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from servo_tool import (
        BOARD_ORDER,
        LED_CONFIG_PATH,
        SERVO_GRID_CONFIG_PATH,
        Link,
        autodetect_port,
        find_config_for_address,
        format_i2c_address,
        load_config,
        parse_i2c_address,
    )


CALIBRATION_DIR = os.path.dirname(os.path.abspath(__file__))
POSITION_KEYS = ("recessed", "extended", "neutral")
STEP_DELAY_S = 0.500


def load_json(path):
    with open(path) as f:
        return json.load(f)


def resolve_path(path):
    if os.path.isabs(path) or os.path.exists(path):
        return path
    return os.path.join(CALIBRATION_DIR, path)


def green_cells(led_cfg, servo_grid_cfg, configs):
    """Return (row, col, address, channel, servo) in row-major order."""
    colors = led_cfg.get("cell_colors", {})
    legacy_yellow = set(led_cfg.get("yellow_cells", []))
    out = []
    for key in led_cfg.get("cells", {}):
        row, col = (int(part) for part in key.split(","))
        if colors.get(key) in {"red", "yellow"} or key in legacy_yellow:
            continue
        mapping = servo_grid_cfg.get("cells", {}).get(key)
        if not mapping:
            print(f"skip ({row},{col}): no servo-grid mapping", file=sys.stderr)
            continue
        try:
            address = format_i2c_address(parse_i2c_address(mapping.get("address", "")))
        except (TypeError, ValueError, argparse.ArgumentTypeError):
            address = ""
        channel = mapping.get("channel")
        if address not in configs or channel is None:
            print(f"skip ({row},{col}): invalid servo mapping", file=sys.stderr)
            continue
        servo = configs[address].get("servos", {}).get(str(channel), {})
        missing = [position for position in POSITION_KEYS if position not in servo]
        if missing:
            print(f"skip ({row},{col}): missing {', '.join(missing)}", file=sys.stderr)
            continue
        out.append((row, col, address, int(channel), servo))
    return sorted(out, key=lambda item: (item[0], item[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=None)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--delay", type=float, default=STEP_DELAY_S,
                        help="seconds between positions (default: 0.100)")
    parser.add_argument("--led-config", default=LED_CONFIG_PATH)
    parser.add_argument("--servo-grid-config", default=SERVO_GRID_CONFIG_PATH)
    args = parser.parse_args()

    led_cfg = load_json(resolve_path(args.led_config))
    servo_grid_cfg = load_json(resolve_path(args.servo_grid_config))

    configs = {}
    for address_text in BOARD_ORDER:
        address = parse_i2c_address(address_text)
        config_path = find_config_for_address(address)
        if not os.path.isabs(config_path):
            config_path = resolve_path(config_path)
        configs[address_text] = load_config(config_path)

    cells = green_cells(led_cfg, servo_grid_cfg, configs)
    print(f"green-cell sequence: {len(cells)} cell(s), row-major from (0,0)")
    for row, col, address, channel, _ in cells:
        print(f"  ({row},{col}) -> {address} ch {channel}")
    if not cells:
        return

    port = args.port or autodetect_port()
    if not port:
        parser.error("no serial port found; pass --port /dev/ttyACM0")

    link = Link(port, args.baud, verbose=False)
    try:
        link.open_wait()
        active_address = None
        for row, col, address, channel, servo in cells:
            if address != active_address:
                link.send(f"A {address}")
                time.sleep(0.3)
                active_address = address
            print(f"({row},{col}) {address} ch {channel}")
            for position in POSITION_KEYS:
                value = servo[position]
                print(f"  {position}: {value} us")
                link.send(f"P {channel} {value}")
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\nInterrupted; releasing all servo channels.")
    finally:
        try:
            link.send("X")
        finally:
            link.close()


if __name__ == "__main__":
    main()
