#!/usr/bin/env python3
import argparse
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


def read_key():
    char = os.read(sys.stdin.fileno(), 1)
    if char != b"\x1b":
        return char.decode("utf-8", errors="replace")

    ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0.05)
    if not ready:
        return "escape"

    rest = os.read(sys.stdin.fileno(), 2)
    if rest == b"[A":
        return "up"
    if rest == b"[B":
        return "down"
    if rest == b"[C":
        return "right"
    if rest == b"[D":
        return "left"
    return "escape"


def print_position(channel, angle):
    print(f"channel {channel:02d} angle {angle:g} deg", flush=True)


def print_off(channel):
    print(f"channel {channel:02d} off", flush=True)


def run_interactive(fd, args):
    channel = clamp(args.channel if args.channel is not None else 0, 0, 15)
    start_angle = clamp(args.position if args.position is not None else args.start_angle, args.min_angle, args.max_angle)
    angles = [start_angle for _ in range(16)]

    print("Interactive servo control")
    print("Left/Right: previous/next channel | Up/Down: angle +/- step | q: quit")
    print_position(channel, angles[channel])

    old_attrs = termios.tcgetattr(sys.stdin.fileno())
    try:
        tty.setcbreak(sys.stdin.fileno())
        while True:
            key = read_key()
            selected = False
            moved = False

            if key in ("q", "Q", "\x03"):
                break
            if key == "left":
                previous_channel = channel
                channel = (channel - 1) % 16
                send_command(fd, f"off {previous_channel}\n")
                print_off(previous_channel)
                selected = True
            elif key == "right":
                previous_channel = channel
                channel = (channel + 1) % 16
                send_command(fd, f"off {previous_channel}\n")
                print_off(previous_channel)
                selected = True
            elif key == "up":
                angles[channel] = clamp(angles[channel] + args.step, args.min_angle, args.max_angle)
                moved = True
            elif key == "down":
                angles[channel] = clamp(angles[channel] - args.step, args.min_angle, args.max_angle)
                moved = True

            if moved:
                send_command(fd, f"a {channel} {angles[channel]:g}\n")
                print_position(channel, angles[channel])
            elif selected:
                print_position(channel, angles[channel])
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
    parser.add_argument("--interactive", action="store_true", help="control one servo at a time with arrow keys")
    parser.add_argument("--start-angle", type=float, default=90.0, help="initial angle for interactive mode")
    parser.add_argument("--step", type=float, default=5.0, help="degrees per up/down keypress in interactive mode")
    parser.add_argument("--min-angle", type=float, default=0.0, help="minimum angle in interactive mode")
    parser.add_argument("--max-angle", type=float, default=180.0, help="maximum angle in interactive mode")
    args = parser.parse_args()

    if args.position is None and not args.interactive:
        parser.error("position is required unless --interactive is used")
    if args.pulse_us and args.interactive:
        parser.error("--pulse-us cannot be used with --interactive")
    if args.interactive and not sys.stdin.isatty():
        parser.error("--interactive requires a terminal")

    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_SYNC)
    try:
        configure_serial(fd, args.baud)
        startup = wait_for_ready(fd, args.ready_timeout)
        if args.interactive:
            if startup.strip():
                print(startup.strip())
            run_interactive(fd, args)
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
