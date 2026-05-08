#!/usr/bin/env python3
import argparse
import os
import select
import sys
import termios
import time


LED_COUNT = 16

BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}

NAMED_COLORS = {
    "off": (0, 0, 0),
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "orange": (255, 80, 0),
    "yellow": (255, 255, 0),
    "green": (0, 255, 0),
    "cyan": (0, 255, 255),
    "blue": (0, 0, 255),
    "purple": (180, 0, 255),
    "pink": (255, 0, 120),
    "white": (255, 255, 255),
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


def parse_color(parts, path, line_number):
    if len(parts) == 1:
        color_name = parts[0].lower()
        if color_name in NAMED_COLORS:
            return NAMED_COLORS[color_name]
        if color_name.startswith("#") and len(color_name) == 7:
            try:
                return (
                    int(color_name[1:3], 16),
                    int(color_name[3:5], 16),
                    int(color_name[5:7], 16),
                )
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: invalid hex color") from exc

    if len(parts) != 3:
        raise ValueError(f"{path}:{line_number}: expected r,g,b, #rrggbb, or color name")

    try:
        color = tuple(int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"{path}:{line_number}: RGB values must be integers") from exc

    if any(value < 0 or value > 255 for value in color):
        raise ValueError(f"{path}:{line_number}: RGB values must be 0-255")

    return color


def load_colors(path):
    colors = [(0, 0, 0)] * LED_COUNT
    next_led = 0
    seen = set()

    with open(path, "r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if line.startswith("#") and not (len(line) >= 7 and line[1:7].isalnum()):
                continue
            if " #" in line:
                line = line.split(" #", 1)[0].strip()
            if "\t#" in line:
                line = line.split("\t#", 1)[0].strip()
            if not line:
                continue

            parts = [part.strip() for part in line.replace(",", " ").split()]
            if len(parts) in (2, 4) and parts[0].isdigit():
                led = int(parts[0])
                color_parts = parts[1:]
            else:
                led = next_led
                color_parts = parts

            if led < 0 or led >= LED_COUNT:
                raise ValueError(f"{path}:{line_number}: LED index must be 0-{LED_COUNT - 1}")
            if led in seen:
                raise ValueError(f"{path}:{line_number}: duplicate LED index {led}")

            colors[led] = parse_color(color_parts, path, line_number)
            seen.add(led)
            next_led = led + 1 if len(parts) in (2, 4) and parts[0].isdigit() else next_led + 1

    if not seen:
        raise ValueError(f"{path}: no LED colors found")

    return colors


def build_frame_command(colors):
    values = []
    for r, g, b in colors:
        values.extend((r, g, b))
    return "frame " + " ".join(str(value) for value in values) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Send 16 LED colors to the Arduino WS2811 serial controller.")
    parser.add_argument("--port", required=True, help="Arduino serial port, like /dev/ttyACM0 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200, choices=BAUD_RATES.keys())
    parser.add_argument("--file", default="arduino/ws2811_serial_leds/led_colors.txt", help="Color text file")
    parser.add_argument("--no-reset-wait", action="store_true", help="Do not wait for Arduino READY after opening serial")
    args = parser.parse_args()

    colors = load_colors(args.file)
    command = build_frame_command(colors)

    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_serial(fd, args.baud)
        if not args.no_reset_wait:
            ready_text = wait_for_ready(fd, 3.0)
            if ready_text.strip():
                print(ready_text.strip(), flush=True)

        os.write(fd, command.encode("ascii"))
        response = read_available(fd, 1.0).strip()
        print(response if response else "sent frame", flush=True)
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
