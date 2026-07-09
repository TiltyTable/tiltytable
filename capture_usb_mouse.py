#!/usr/bin/env python3
"""Capture raw Linux input events from a USB mouse-like device.

This script uses only the Python standard library. It reads /dev/input/event*
records directly, so it works even when python-evdev is not installed.
"""

from __future__ import annotations

import argparse
import os
import select
import struct
import sys
import termios
import time
from pathlib import Path


EVENT_ROOT = Path("/dev/input")
PROC_INPUT_DEVICES = Path("/proc/bus/input/devices")

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
EV_MSC = 0x04

REL_CODES = {
    0x00: "REL_X",
    0x01: "REL_Y",
    0x02: "REL_Z",
    0x06: "REL_HWHEEL",
    0x08: "REL_WHEEL",
    0x0B: "REL_WHEEL_HI_RES",
    0x0C: "REL_HWHEEL_HI_RES",
}

KEY_CODES = {
    0x110: "BTN_LEFT",
    0x111: "BTN_RIGHT",
    0x112: "BTN_MIDDLE",
    0x113: "BTN_SIDE",
    0x114: "BTN_EXTRA",
    0x115: "BTN_FORWARD",
    0x116: "BTN_BACK",
    0x117: "BTN_TASK",
}

EVENT_TYPES = {
    EV_SYN: "EV_SYN",
    EV_KEY: "EV_KEY",
    EV_REL: "EV_REL",
    EV_ABS: "EV_ABS",
    EV_MSC: "EV_MSC",
}

# Linux input_event is timeval + unsigned short + unsigned short + signed int.
# Native layout handles 64-bit Jetson kernels and other Linux ABIs correctly.
INPUT_EVENT = struct.Struct("llHHI")

BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


class StewartController:
    def __init__(
        self,
        port: str | None,
        baud: int,
        heave_mm: float,
        dry_run: bool,
        response_wait_s: float,
        verbose: bool,
    ) -> None:
        self.port = port
        self.baud = baud
        self.heave_mm = heave_mm
        self.dry_run = dry_run
        self.response_wait_s = response_wait_s
        self.verbose = verbose
        self.fd: int | None = None

    def open(self) -> None:
        if self.dry_run:
            print("Dry run: serial port will not be opened.")
            return
        if not self.port:
            return

        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_SYNC)
        configure_serial(self.fd, self.baud)
        startup = read_available(self.fd, 1.0).strip()
        if startup and self.verbose:
            print_prefixed("arduino: ", startup)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def can_send(self) -> bool:
        return self.dry_run or self.fd is not None

    def send(self, command: str) -> str:
        line = command.rstrip() + "\n"
        if self.dry_run or not self.port:
            print(f"arduino <= {line.strip()}")
            return ""
        if self.fd is None:
            raise RuntimeError("serial port is not open")

        os.write(self.fd, line.encode("ascii"))
        response = read_available(self.fd, self.response_wait_s).strip()
        if response and self.verbose:
            print_prefixed("arduino: ", response)
        if "ERR" in response:
            raise RuntimeError(response)
        return response

    def fire(self, command: str) -> None:
        """Send a command without waiting for a response."""
        line = command.rstrip() + "\n"
        if self.dry_run or not self.port:
            print(f"arduino <= {line.strip()}")
            return
        if self.fd is None:
            raise RuntimeError("serial port is not open")
        os.write(self.fd, line.encode("ascii"))

    def enable(self) -> None:
        self.send("enable")

    def disable(self) -> None:
        self.send("disable")

    def zero(self) -> None:
        # Alias for firmware `calibrate` (cranks straight up = max heave).
        self.send("calibrate")

    def calibrate(self) -> None:
        self.send("calibrate")

    def pose(self, roll_deg: float, pitch_deg: float) -> None:
        self.send(f"pose {roll_deg:.3f} {pitch_deg:.3f} {self.heave_mm:.3f}")

    def velocity(self, roll_deg_s: float, pitch_deg_s: float, heave_mm_s: float = 0.0) -> None:
        self.send(f"vel {roll_deg_s:.3f} {pitch_deg_s:.3f} {heave_mm_s:.3f}")


def configure_serial(fd: int, baud: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
    attrs[3] = 0
    attrs[4] = BAUD_RATES[baud]
    attrs[5] = BAUD_RATES[baud]
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 5
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def read_available(fd: int, seconds: float) -> str:
    end = time.time() + seconds
    chunks: list[bytes] = []

    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.02)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        if not chunk:
            break
        chunks.append(chunk)

    return b"".join(chunks).decode("utf-8", errors="replace")


def print_prefixed(prefix: str, text: str) -> None:
    for line in text.splitlines():
        print(f"{prefix}{line}")


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

    by_id = EVENT_ROOT / "by-id"
    if by_id.exists():
        print("\nStable by-id links:")
        for link in sorted(by_id.iterdir()):
            target = link.resolve()
            print(f"  {link} -> {target}")


def find_mouse_device() -> str | None:
    by_id = EVENT_ROOT / "by-id"
    if by_id.exists():
        mouse_links = sorted(path for path in by_id.iterdir() if "mouse" in path.name.lower())
        if mouse_links:
            return str(mouse_links[0])

    for device in parse_proc_input_devices():
        name = device.get("name", "").lower()
        handlers = device.get("handlers", "")
        if "mouse" not in name:
            continue
        for part in handlers.split():
            if part.startswith("event"):
                return event_path_from_handler(part)
    return None


def code_name(event_type: int, code: int) -> str:
    if event_type == EV_REL:
        return REL_CODES.get(code, f"REL_{code}")
    if event_type == EV_KEY:
        return KEY_CODES.get(code, f"KEY_OR_BTN_{code}")
    return str(code)


def read_events(device_path: str, args: argparse.Namespace) -> None:
    fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)

    print(f"Reading {device_path}")
    if args.port or args.dry_run:
        print(
            "Velocity mapping: up/down -> roll rate, left/right -> pitch rate. "
            f"scale={args.velocity_scale:g} deg/s per count"
        )
    print("Move/click the device. Press Ctrl+C to stop.\n")

    position_x = 0
    position_y = 0
    pending_dx = 0
    pending_dy = 0
    last_velocity_sent = 0.0
    last_input_time = 0.0
    velocity_interval_s = 1.0 / args.velocity_rate_hz
    stop_timeout_s = args.stop_timeout_ms / 1000.0
    velocity_active = False
    controller = StewartController(
        args.port,
        args.baud,
        args.heave,
        args.dry_run,
        args.response_wait,
        args.verbose,
    )

    try:
        controller.open()
        if args.enable:
            controller.enable()
        if args.zero_on_start:
            controller.zero()
        if args.center:
            controller.pose(args.initial_roll, args.initial_pitch)
            last_velocity_sent = time.monotonic()

        serial_fd = controller.fd
        while True:
            timeout_s = next_velocity_timeout(
                last_velocity_sent,
                last_input_time,
                velocity_interval_s,
                stop_timeout_s,
                velocity_active,
            )
            watch = [fd] if serial_fd is None else [fd, serial_fd]
            readable, _, _ = select.select(watch, [], [], timeout_s)
            if serial_fd is not None and serial_fd in readable:
                try:
                    os.read(serial_fd, 4096)
                except OSError:
                    pass
            if fd not in readable:
                last_velocity_sent, velocity_active, pending_dx, pending_dy = maybe_send_velocity(
                    controller,
                    args,
                    pending_dx,
                    pending_dy,
                    velocity_active,
                    last_velocity_sent,
                    last_input_time,
                    force=False,
                )
                continue

            try:
                data = os.read(fd, INPUT_EVENT.size * 64)
            except BlockingIOError:
                continue

            now = time.strftime("%H:%M:%S")
            for offset in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
                sec, usec, event_type, code, value = INPUT_EVENT.unpack_from(data, offset)
                type_name = EVENT_TYPES.get(event_type, f"EV_{event_type}")
                name = code_name(event_type, code)

                if event_type == EV_REL and code == 0x00:
                    delta = signed32(value)
                    position_x += delta
                    pending_dx += delta
                    last_input_time = time.monotonic()
                elif event_type == EV_REL and code == 0x01:
                    delta = signed32(value)
                    position_y += delta
                    pending_dy += delta
                    last_input_time = time.monotonic()

                value = signed32(value)
                if event_type == EV_SYN:
                    last_velocity_sent, velocity_active, pending_dx, pending_dy = maybe_send_velocity(
                        controller,
                        args,
                        pending_dx,
                        pending_dy,
                        velocity_active,
                        last_velocity_sent,
                        last_input_time,
                        force=False,
                    )
                    print(f"{now} {sec}.{usec:06d} {type_name:<7} pos=({position_x:>5},{position_y:>5})")
                else:
                    print(f"{now} {sec}.{usec:06d} {type_name:<7} {name:<18} value={value}")
    finally:
        try:
            if controller.can_send():
                controller.velocity(0.0, 0.0, 0.0)
            if args.disable_on_exit and controller.can_send():
                controller.disable()
        finally:
            controller.close()
        os.close(fd)


def maybe_send_velocity(
    controller: StewartController,
    args: argparse.Namespace,
    pending_dx: int,
    pending_dy: int,
    velocity_active: bool,
    last_velocity_sent: float,
    last_input_time: float,
    force: bool,
) -> tuple[float, bool, int, int]:
    if not (args.port or args.dry_run):
        return last_velocity_sent, False, 0, 0

    now = time.monotonic()
    velocity_interval_s = 1.0 / args.velocity_rate_hz
    if not force and now - last_velocity_sent < velocity_interval_s:
        return last_velocity_sent, velocity_active, pending_dx, pending_dy

    dx = consume_axis_counts(pending_dx, args.input_deadband)
    dy = consume_axis_counts(pending_dy, args.input_deadband)
    if dx or dy:
        roll_rate = -dy * args.velocity_scale * args.roll_sign
        pitch_rate = dx * args.velocity_scale * args.pitch_sign
        roll_rate = clamp(roll_rate, -args.max_roll_rate, args.max_roll_rate)
        pitch_rate = clamp(pitch_rate, -args.max_pitch_rate, args.max_pitch_rate)
        controller.velocity(roll_rate, pitch_rate, 0.0)
        print(f"velocity roll={roll_rate:7.3f} deg/s pitch={pitch_rate:7.3f} deg/s")
        return now, True, pending_dx - dx, pending_dy - dy

    stopped_long_enough = last_input_time == 0 or now - last_input_time >= args.stop_timeout_ms / 1000.0
    if velocity_active and stopped_long_enough:
        controller.velocity(0.0, 0.0, 0.0)
        print("velocity roll=  0.000 deg/s pitch=  0.000 deg/s")
        return now, False, 0, 0

    return last_velocity_sent, velocity_active, pending_dx, pending_dy


def next_velocity_timeout(
    last_velocity_sent: float,
    last_input_time: float,
    velocity_interval_s: float,
    stop_timeout_s: float,
    velocity_active: bool,
) -> float:
    now = time.monotonic()
    waits = [velocity_interval_s - (now - last_velocity_sent)]
    if velocity_active and last_input_time > 0:
        waits.append(stop_timeout_s - (now - last_input_time))
    return max(0.0, min(1.0, max(waits)))


def consume_axis_counts(value: int, deadband: int) -> int:
    if deadband <= 0 or abs(value) > deadband:
        return value
    return 0


def signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture data from a Linux USB mouse/input device.")
    parser.add_argument("device", nargs="?", help="Event device path, for example /dev/input/event7")
    parser.add_argument("--list", action="store_true", help="List input devices and exit")
    parser.add_argument("--port", default=None, help="Arduino serial port, for example /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200, choices=sorted(BAUD_RATES), help="Arduino baud rate")
    parser.add_argument("--dry-run", action="store_true", help="Print Stewart commands without opening serial")
    parser.add_argument(
        "--zero",
        "--calibrate",
        action="store_true",
        dest="zero",
        help="Send 'calibrate' (cranks-up = max heave) and exit",
    )
    parser.add_argument(
        "--zero-on-start",
        "--calibrate-on-start",
        action="store_true",
        dest="zero_on_start",
        help="Send 'calibrate' before starting mouse control (cranks must already be straight up)",
    )
    parser.add_argument("--enable", action="store_true", help="Send 'enable' to the Stewart controller on startup")
    parser.add_argument(
        "--disable-on-exit",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Send 'disable' when the script exits",
    )
    parser.add_argument("--center", action="store_true", help="Send the initial pose before reading mouse movement")
    parser.add_argument("--initial-roll", type=float, default=0.0, help="Starting roll target in degrees")
    parser.add_argument("--initial-pitch", type=float, default=0.0, help="Starting pitch target in degrees")
    parser.add_argument("--heave", type=float, default=0.0, help="Fixed heave target in millimeters")
    parser.add_argument("--velocity-scale", type=float, default=0.5, help="Degrees/second per mouse count")
    parser.add_argument("--velocity-rate-hz", type=float, default=20.0, help="Maximum velocity command rate")
    parser.add_argument("--stop-timeout-ms", type=float, default=50.0, help="Send zero velocity after this much input silence")
    parser.add_argument("--input-deadband", type=int, default=2, help="Ignore accumulated mouse counts at or below this size")
    parser.add_argument("--max-roll-rate", type=float, default=15.0, help="Clamp roll velocity to +/- this many deg/s")
    parser.add_argument("--max-pitch-rate", type=float, default=15.0, help="Clamp pitch velocity to +/- this many deg/s")
    parser.add_argument("--roll-sign", type=float, choices=(-1.0, 1.0), default=1.0, help="Flip roll direction")
    parser.add_argument("--pitch-sign", type=float, choices=(-1.0, 1.0), default=1.0, help="Flip pitch direction")
    parser.add_argument("--response-wait", type=float, default=0.02, help="Seconds to read Arduino response after each command")
    parser.add_argument("--verbose", action="store_true", help="Print Arduino responses")
    args = parser.parse_args()

    if args.list:
        list_devices()
        return 0

    if args.zero:
        if not args.port and not args.dry_run:
            parser.error("--zero needs --port, or use --dry-run to preview the command")
        controller = StewartController(
            args.port,
            args.baud,
            args.heave,
            args.dry_run,
            args.response_wait,
            args.verbose,
        )
        try:
            controller.open()
            controller.zero()
        finally:
            controller.close()
        return 0

    device = args.device or find_mouse_device()
    if not device:
        print("No mouse-like event device found. Run with --list, then pass /dev/input/eventN.", file=sys.stderr)
        return 2

    try:
        if args.velocity_rate_hz <= 0:
            parser.error("--velocity-rate-hz must be greater than zero")
        if args.stop_timeout_ms < 0:
            parser.error("--stop-timeout-ms must be zero or greater")
        if args.input_deadband < 0:
            parser.error("--input-deadband must be zero or greater")
        if args.velocity_scale < 0:
            parser.error("--velocity-scale must be zero or greater")
        if args.max_roll_rate < 0:
            parser.error("--max-roll-rate must be zero or greater")
        if args.max_pitch_rate < 0:
            parser.error("--max-pitch-rate must be zero or greater")
        read_events(device, args)
    except PermissionError:
        print(f"Permission denied opening {device}. Try: sudo {sys.executable} {sys.argv[0]} {device}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"control error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
