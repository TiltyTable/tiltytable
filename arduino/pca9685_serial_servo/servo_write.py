#!/usr/bin/env python3
import argparse
import math
import os
import select
import sys
import termios
import time
import tty


BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def configure_serial(fd, baud):
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


def read_available(fd, seconds):
    end = time.time() + seconds
    chunks = []

    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            continue
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)

    return b"".join(chunks).decode("utf-8", errors="replace")


def wait_for_ready(fd, seconds):
    end = time.time() + seconds
    text = ""

    while time.time() < end:
        text += read_available(fd, 0.1)
        if "READY" in text:
            return text

    return text


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def send_command(fd, command):
    os.write(fd, command.encode("ascii"))


def load_calibration(path):
    calibration = []
    seen = set()

    with open(path, "r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 2:
                raise ValueError(f"{path}:{line_number}: expected servo_index,neutral_angle")

            try:
                channel = int(parts[0])
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: servo_index must be an integer") from exc

            if channel < 0 or channel > 15:
                raise ValueError(f"{path}:{line_number}: servo_index must be 0-15")
            if channel in seen:
                raise ValueError(f"{path}:{line_number}: duplicate servo_index {channel}")
            seen.add(channel)

            try:
                angle = float(parts[1])
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: neutral_angle must be a number or nan") from exc

            calibration.append((channel, angle))

    return calibration


def validate_calibration_angles(calibration, min_angle, max_angle):
    for channel, angle in calibration:
        if not math.isnan(angle) and (angle < min_angle or angle > max_angle):
            raise ValueError(f"servo_index {channel}: neutral_angle must be {min_angle:g}-{max_angle:g} or nan")


def apply_calibration(fd, calibration):
    send_command(fd, "off\n")
    print("all channels off", flush=True)

    for channel, angle in calibration:
        if math.isnan(angle):
            send_command(fd, f"off {channel}\n")
            print_off(channel)
            continue

        send_command(fd, f"a {channel} {angle:g}\n")
        print_position(channel, angle)


def calibration_angles(calibration, fallback_angle):
    angles = [fallback_angle for _ in range(16)]
    enabled = [True for _ in range(16)]

    for channel, angle in calibration:
        if math.isnan(angle):
            enabled[channel] = False
        else:
            angles[channel] = angle
            enabled[channel] = True

    return angles, enabled


def read_key():
    char = os.read(sys.stdin.fileno(), 1)
    if char != b"\x1b":
        return char.decode("utf-8", errors="replace")

    ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0.05)
    if not ready:
        return "escape"

    rest = os.read(sys.stdin.fileno(), 1)
    if rest == b"[":
        while True:
            ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0.05)
            if not ready:
                break
            rest += os.read(sys.stdin.fileno(), 1)
            if 0x40 <= rest[-1] <= 0x7E:
                break

    arrows = {b"[A": "up", b"[B": "down", b"[C": "right", b"[D": "left"}
    if rest in arrows:
        return arrows[rest]

    shifted_arrows = {
        b"[1;2A": "shift-up",
        b"[1;2B": "shift-down",
        b"[1;2C": "shift-right",
        b"[1;2D": "shift-left",
    }
    if rest in shifted_arrows:
        return shifted_arrows[rest]

    return "escape"


def print_position(channel, angle):
    print(f"channel {channel:02d} angle {angle:g} deg", flush=True)


def print_off(channel):
    print(f"channel {channel:02d} off", flush=True)


def run_interactive(fd, args, calibration=None):
    channel = clamp(args.channel if args.channel is not None else 0, 0, 15)
    start_angle = clamp(args.position if args.position is not None else args.start_angle, args.min_angle, args.max_angle)
    if calibration is None:
        angles = [start_angle for _ in range(16)]
        enabled = [True for _ in range(16)]
    else:
        angles, enabled = calibration_angles(calibration, start_angle)

    print("Interactive servo control")
    print("Left/Right: previous/next channel | Up/Down: angle +/- step | Shift+Up/Down: +/- step/10 | q: quit")
    if enabled[channel]:
        print_position(channel, angles[channel])
    else:
        print_off(channel)

    old_attrs = termios.tcgetattr(sys.stdin.fileno())
    try:
        tty.setcbreak(sys.stdin.fileno())
        while True:
            key = read_key()
            selected = False
            moved = False

            if key in ("q", "Q", "\x03"):
                break
            if key in ("left", "shift-left"):
                previous_channel = channel
                channel = (channel - 1) % 16
                send_command(fd, f"off {previous_channel}\n")
                print_off(previous_channel)
                selected = True
            elif key in ("right", "shift-right"):
                previous_channel = channel
                channel = (channel + 1) % 16
                send_command(fd, f"off {previous_channel}\n")
                print_off(previous_channel)
                selected = True
            elif key in ("up", "shift-up"):
                step = args.step / 10 if key == "shift-up" else args.step
                angles[channel] = clamp(angles[channel] + step, args.min_angle, args.max_angle)
                moved = True
            elif key in ("down", "shift-down"):
                step = args.step / 10 if key == "shift-down" else args.step
                angles[channel] = clamp(angles[channel] - step, args.min_angle, args.max_angle)
                moved = True

            if moved:
                send_command(fd, f"a {channel} {angles[channel]:g}\n")
                enabled[channel] = True
                print_position(channel, angles[channel])
            elif selected:
                if enabled[channel]:
                    print_position(channel, angles[channel])
                else:
                    print_off(channel)
    finally:
        send_command(fd, f"off {channel}\n")
        print_off(channel)
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attrs)


def main():
    parser = argparse.ArgumentParser(description="Write an SG90 servo position through the Arduino/PCA9685 sketch.")
    parser.add_argument("position", type=float, nargs="?", help="angle in degrees, 0-180 by default")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Arduino serial port")
    parser.add_argument("--channel", type=int, default=None, help="PCA9685 channel, 0-15")
    parser.add_argument("--pulse-us", action="store_true", help="send position as a raw pulse width instead of degrees")
    parser.add_argument("--baud", type=int, default=115200, choices=sorted(BAUD_RATES), help="serial baud rate")
    parser.add_argument("--ready-timeout", type=float, default=4.0, help="time to wait for Arduino READY after opening port")
    parser.add_argument(
        "--calib",
        metavar="FILE",
        help="CSV calibration file with servo_index,neutral_angle; nan angles are left off",
    )
    parser.add_argument("--interactive", action="store_true", help="control one servo at a time with arrow keys")
    parser.add_argument("--start-angle", type=float, default=90.0, help="initial angle for interactive mode")
    parser.add_argument("--step", type=float, default=5.0, help="degrees per up/down keypress in interactive mode")
    parser.add_argument("--min-angle", type=float, default=0.0, help="minimum angle in interactive mode")
    parser.add_argument("--max-angle", type=float, default=180.0, help="maximum angle in interactive mode")
    args = parser.parse_args()

    if args.position is None and not args.interactive and not args.calib:
        parser.error("position is required unless --interactive or --calib is used")
    if args.pulse_us and (args.interactive or args.calib):
        parser.error("--pulse-us cannot be used with --interactive or --calib")
    if args.channel is not None and args.calib and not args.interactive:
        parser.error("--channel can only be used with --calib in --interactive mode")
    if args.interactive and not sys.stdin.isatty():
        parser.error("--interactive requires a terminal")

    calibration = None
    if args.calib:
        try:
            calibration = load_calibration(args.calib)
            validate_calibration_angles(calibration, args.min_angle, args.max_angle)
        except ValueError as exc:
            parser.error(str(exc))

    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_SYNC)
    try:
        configure_serial(fd, args.baud)
        startup = wait_for_ready(fd, args.ready_timeout)
        if args.interactive:
            if startup.strip():
                print(startup.strip())
            if calibration is not None:
                apply_calibration(fd, calibration)
            run_interactive(fd, args, calibration)
        elif calibration is not None:
            apply_calibration(fd, calibration)
        else:
            verb = "u" if args.pulse_us else "a"
            if args.channel is None:
                command = f"{verb} {args.position:g}\n"
            else:
                command = f"{verb} {args.channel} {args.position:g}\n"
            send_command(fd, command)
    finally:
        os.close(fd)

    if not args.interactive and startup.strip():
        print(startup.strip())


if __name__ == "__main__":
    try:
        main()
    except PermissionError as exc:
        print(f"serial permission error: {exc}", file=sys.stderr)
        print("Try: sudo usermod -a -G dialout $USER, then log out and back in.", file=sys.stderr)
        print("Temporary test: sudo chmod a+rw /dev/ttyACM0", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"serial error: {exc}", file=sys.stderr)
        sys.exit(1)
