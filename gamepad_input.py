#!/usr/bin/env python3
"""Capture raw Linux input events from a USB gamepad/joystick.

Stdlib-only, no python-evdev/pygame dependency (matches capture_usb_mouse.py's
approach). Reads /dev/input/event* directly: EV_ABS for analog sticks
(calibrated via the EVIOCGABS ioctl so raw counts normalize to -1..1) and
EV_KEY for buttons (edge-triggered press detection).

Run directly to discover a pad's axis/button codes before wiring up
stewart_ball_control.py's --axis-x-code / --reset-button-code / etc.:

    python3 gamepad_input.py --list
    python3 gamepad_input.py --list-buttons
    python3 gamepad_input.py /dev/input/eventN
"""

from __future__ import annotations

import argparse
import fcntl
import os
import select
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path


EVENT_ROOT = Path("/dev/input")
PROC_INPUT_DEVICES = Path("/proc/bus/input/devices")

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

# Linux input_event is timeval + unsigned short + unsigned short + signed int.
INPUT_EVENT = struct.Struct("llHHI")

# input_absinfo: value, minimum, maximum, fuzz, flat, resolution (all s32).
INPUT_ABSINFO = struct.Struct("iiiiii")

ABS_CODES = {
    0x00: "ABS_X",
    0x01: "ABS_Y",
    0x02: "ABS_Z",
    0x03: "ABS_RX",
    0x04: "ABS_RY",
    0x05: "ABS_RZ",
    0x10: "ABS_HAT0X",
    0x11: "ABS_HAT0Y",
}

# Standard gamepad/joystick button range (display-only; button selection is
# always by raw integer code since the exact pad model isn't known here).
BTN_CODES = {
    0x130: "BTN_SOUTH (A)",
    0x131: "BTN_EAST (B)",
    0x133: "BTN_NORTH (X)",
    0x134: "BTN_WEST (Y)",
    0x136: "BTN_TL",
    0x137: "BTN_TR",
    0x138: "BTN_TL2",
    0x139: "BTN_TR2",
    0x13a: "BTN_SELECT",
    0x13b: "BTN_START",
    0x13c: "BTN_MODE",
    0x13d: "BTN_THUMBL",
    0x13e: "BTN_THUMBR",
}

# _IOC(dir, type, nr, size) per <asm-generic/ioctl.h>; EVIOCGABS(axis) reads
# a struct input_absinfo for the given ABS axis code.
_IOC_READ = 2


def _ioc(direction: int, type_: int, nr: int, size: int) -> int:
    return (direction << 30) | (type_ << 8) | nr | (size << 16)


def _eviocgabs(axis: int) -> int:
    return _ioc(_IOC_READ, ord("E"), 0x40 + axis, INPUT_ABSINFO.size)


@dataclass
class AxisCalibration:
    minimum: int
    maximum: int
    flat: int

    def normalize(self, raw: int) -> float:
        center = (self.minimum + self.maximum) / 2.0
        span = (self.maximum - self.minimum) / 2.0
        if span <= 0:
            return 0.0
        value = raw - center
        if abs(value) <= self.flat:
            return 0.0
        value = max(-span, min(span, value))
        return value / span


class GamepadReader:
    """Non-blocking reader for one /dev/input/eventN gamepad device."""

    # Axes queried for calibration at open() time.
    _CALIBRATED_AXES = (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11)

    def __init__(self, device_path: str):
        self.device_path = device_path
        self.fd: int | None = None
        self.axes: dict[int, AxisCalibration] = {}
        self.axis_value: dict[int, float] = {}
        self.button_state: dict[int, bool] = {}
        self._button_edges: list[int] = []

    def open(self) -> None:
        self.fd = os.open(self.device_path, os.O_RDONLY | os.O_NONBLOCK)
        for axis in self._CALIBRATED_AXES:
            calib = self._query_axis(axis)
            if calib is not None:
                self.axes[axis] = calib
                self.axis_value[axis] = 0.0

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def fileno(self) -> int:
        assert self.fd is not None
        return self.fd

    def _query_axis(self, axis: int) -> AxisCalibration | None:
        assert self.fd is not None
        try:
            buf = fcntl.ioctl(self.fd, _eviocgabs(axis), bytes(INPUT_ABSINFO.size))
        except OSError:
            return None
        value, minimum, maximum, _fuzz, flat, _resolution = INPUT_ABSINFO.unpack(buf)
        if minimum == 0 and maximum == 0:
            return None
        return AxisCalibration(minimum=minimum, maximum=maximum, flat=flat)

    def poll(self) -> None:
        """Drain and apply all currently available events (non-blocking)."""
        assert self.fd is not None
        try:
            data = os.read(self.fd, INPUT_EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            raise

        for offset in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
            _sec, _usec, etype, code, value = INPUT_EVENT.unpack_from(data, offset)
            if etype == EV_ABS:
                calib = self.axes.get(code)
                if calib is not None:
                    self.axis_value[code] = calib.normalize(_signed32(value))
            elif etype == EV_KEY:
                value = _signed32(value)
                if value == 2:
                    continue  # autorepeat, not a fresh transition
                pressed = value == 1
                was_pressed = self.button_state.get(code, False)
                self.button_state[code] = pressed
                if pressed and not was_pressed:
                    self._button_edges.append(code)

    def consume_edges(self) -> list[int]:
        edges = self._button_edges
        self._button_edges = []
        return edges


def _signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def parse_proc_input_devices() -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    if not PROC_INPUT_DEVICES.exists():
        return devices

    current: dict[str, str] = {}
    for line in PROC_INPUT_DEVICES.read_text(errors="replace").splitlines():
        if not line.strip():
            if current:
                devices.append(current)
                current = {}
            continue

        prefix, _, value = line.partition(":")
        value = value.strip()
        if prefix == "N":
            current["name"] = value.removeprefix('Name="').removesuffix('"')
        elif prefix == "H":
            current["handlers"] = value.removeprefix("Handlers=").strip()
        elif prefix == "P":
            current["phys"] = value.removeprefix("Phys=").strip()
        elif prefix == "I":
            current["id"] = value.strip()

    if current:
        devices.append(current)
    return devices


def event_path_from_handler(handler: str) -> str:
    return str(EVENT_ROOT / handler)


def list_devices() -> None:
    print("Input devices:")
    for device in parse_proc_input_devices():
        handlers = device.get("handlers", "")
        event_handlers = [part for part in handlers.split() if part.startswith("event")]
        if not event_handlers:
            continue
        paths = ", ".join(event_path_from_handler(handler) for handler in event_handlers)
        print(f"  {paths}")
        print(f"    name: {device.get('name', '(unknown)')}")
        if device.get("phys"):
            print(f"    phys: {device['phys']}")
        is_joystick = "js" in handlers.split()
        print(f"    joystick handler: {is_joystick}")

    by_id = EVENT_ROOT / "by-id"
    if by_id.exists():
        print("\nStable by-id links:")
        for link in sorted(by_id.iterdir()):
            target = link.resolve()
            print(f"  {link} -> {target}")


def find_gamepad_device() -> str | None:
    by_id = EVENT_ROOT / "by-id"
    if by_id.exists():
        pad_links = sorted(
            path for path in by_id.iterdir()
            if "joystick" in path.name.lower() or "gamepad" in path.name.lower()
        )
        if pad_links:
            return str(pad_links[0])

    for device in parse_proc_input_devices():
        name = device.get("name", "").lower()
        handlers = device.get("handlers", "")
        handler_parts = handlers.split()
        looks_like_pad = "js" in handler_parts or "gamepad" in name or "joystick" in name or "controller" in name
        if not looks_like_pad:
            continue
        for part in handler_parts:
            if part.startswith("event"):
                return event_path_from_handler(part)
    return None


def _code_name(event_type: int, code: int) -> str:
    if event_type == EV_ABS:
        return ABS_CODES.get(code, f"ABS_{code}")
    if event_type == EV_KEY:
        return BTN_CODES.get(code, f"BTN_OR_KEY_{code:#x}")
    return str(code)


def _print_loop(device_path: str, buttons_only: bool) -> None:
    reader = GamepadReader(device_path)
    reader.open()
    print(f"Reading {device_path}")
    if buttons_only:
        print("Button-only mode: move sticks separately to avoid ambiguity. Ctrl+C to stop.\n")
    else:
        print("Move sticks / press buttons. Ctrl+C to stop.\n")

    try:
        while True:
            readable, _, _ = select.select([reader.fileno()], [], [], 0.5)
            if not readable:
                continue
            reader.poll()
            now = time.strftime("%H:%M:%S")
            for code in reader.consume_edges():
                name = _code_name(EV_KEY, code)
                print(f"{now} BUTTON PRESS {name:<20} code={code:#04x}")
            if not buttons_only:
                for code, value in sorted(reader.axis_value.items()):
                    name = _code_name(EV_ABS, code)
                    print(f"\r{now} {name:<10} code={code:#04x} value={value:+.2f}   ", end="", flush=True)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        reader.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture data from a Linux USB gamepad/joystick device.")
    parser.add_argument("device", nargs="?", help="Event device path, for example /dev/input/event7")
    parser.add_argument("--list", action="store_true", help="List input devices and exit")
    parser.add_argument(
        "--list-buttons", action="store_true",
        help="Print only button-press transitions (ignore stick axes) to make button codes easy to identify",
    )
    args = parser.parse_args()

    if args.list:
        list_devices()
        return 0

    device = args.device or find_gamepad_device()
    if not device:
        print("No gamepad-like event device found. Run with --list, then pass /dev/input/eventN.", file=sys.stderr)
        return 2

    try:
        _print_loop(device, buttons_only=args.list_buttons)
    except PermissionError:
        print(f"Permission denied opening {device}. Try: sudo {sys.executable} {sys.argv[0]} {device}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
