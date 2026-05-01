#!/usr/bin/env python3
import argparse
import os
import select
import sys
import termios
import time


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


def main():
    parser = argparse.ArgumentParser(description="Write an SG90 servo position through the Arduino/PCA9685 sketch.")
    parser.add_argument("position", type=float, help="angle in degrees, 0-180 by default")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Arduino serial port")
    parser.add_argument("--channel", type=int, default=None, help="PCA9685 channel, 0-15")
    parser.add_argument("--pulse-us", action="store_true", help="send position as a raw pulse width instead of degrees")
    parser.add_argument("--baud", type=int, default=115200, choices=sorted(BAUD_RATES), help="serial baud rate")
    parser.add_argument("--ready-timeout", type=float, default=4.0, help="time to wait for Arduino READY after opening port")
    args = parser.parse_args()

    verb = "u" if args.pulse_us else "a"
    if args.channel is None:
        command = f"{verb} {args.position:g}\n"
    else:
        command = f"{verb} {args.channel} {args.position:g}\n"

    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_SYNC)
    try:
        configure_serial(fd, args.baud)
        startup = wait_for_ready(fd, args.ready_timeout)
        os.write(fd, command.encode("ascii"))
        response = read_available(fd, 0.5).strip()
    finally:
        os.close(fd)

    if startup.strip():
        print(startup.strip())
    if response:
        print(response)


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
