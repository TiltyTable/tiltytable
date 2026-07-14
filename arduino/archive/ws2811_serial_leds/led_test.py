#!/usr/bin/env python3
"""Interactive test tool for the WS2812 serial LED strip.

This talks to the `ws2811_serial_leds.ino` sketch over the USB serial port
using the same `set` / `frame` / `clear` line protocol. It is the single,
canonical script for exercising the strip: run it with no test name to play a
full self-test sequence, or pick an individual test to debug wiring.

Examples
--------
    # Full automatic self-test (rgb -> wipe -> chase -> rainbow -> blink -> off)
    python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0

    # Light the whole strip solid red, dimmed to 30%
    python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 all red --brightness 76

    # Find a single LED by index
    python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 index 17 cyan

    # Verify color order / count physical LEDs one at a time
    python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 rgb
    python3 arduino/ws2811_serial_leds/led_test.py --port /dev/ttyACM0 count
"""
import argparse
import os
import select
import signal
import sys
import termios
import time


DEFAULT_COUNT = 47

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


# ---------------------------------------------------------------------------
# Pretty terminal output
# ---------------------------------------------------------------------------
class Style:
    enabled = sys.stdout.isatty()

    @staticmethod
    def _wrap(code, text):
        if not Style.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    @staticmethod
    def bold(text):
        return Style._wrap("1", text)

    @staticmethod
    def dim(text):
        return Style._wrap("2", text)

    @staticmethod
    def cyan(text):
        return Style._wrap("36", text)

    @staticmethod
    def green(text):
        return Style._wrap("32", text)

    @staticmethod
    def red(text):
        return Style._wrap("31", text)

    @staticmethod
    def swatch(rgb):
        r, g, b = rgb
        if not Style.enabled:
            return f"({r},{g},{b})"
        return f"\033[48;2;{r};{g};{b}m  \033[0m"


def info(text):
    print(text, flush=True)


def step(text):
    print(Style.cyan("> ") + Style.bold(text), flush=True)


# ---------------------------------------------------------------------------
# Serial transport (stdlib termios, no pyserial dependency)
# ---------------------------------------------------------------------------
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
        ready, _, _ = select.select([fd], [], [], 0.02)
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


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
def clamp(value, low=0, high=255):
    return max(low, min(high, value))


def parse_color(text):
    """Parse 'red', '#ff0078', or '255,0,120' / '255 0 120' into an (r,g,b)."""
    key = text.strip().lower()
    if key in NAMED_COLORS:
        return NAMED_COLORS[key]

    if key.startswith("#"):
        key = key[1:]
    if len(key) == 6 and all(c in "0123456789abcdef" for c in key):
        return (int(key[0:2], 16), int(key[2:4], 16), int(key[4:6], 16))

    parts = [p for p in text.replace(",", " ").split() if p]
    if len(parts) == 3:
        try:
            rgb = tuple(int(p) for p in parts)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid color: {text!r}")
        if all(0 <= v <= 255 for v in rgb):
            return rgb

    raise argparse.ArgumentTypeError(
        f"invalid color {text!r}: use a name, #rrggbb, or 'r,g,b'"
    )


def scale(rgb, brightness):
    if brightness >= 255:
        return rgb
    factor = brightness / 255.0
    return tuple(clamp(round(channel * factor)) for channel in rgb)


def wheel(pos):
    """Map 0-255 to a smooth rainbow color."""
    pos = pos % 256
    if pos < 85:
        return (255 - pos * 3, pos * 3, 0)
    if pos < 170:
        pos -= 85
        return (0, 255 - pos * 3, pos * 3)
    pos -= 170
    return (pos * 3, 0, 255 - pos * 3)


# ---------------------------------------------------------------------------
# Strip controller
# ---------------------------------------------------------------------------
class Strip:
    def __init__(self, fd, count, brightness, verbose=False):
        self.fd = fd
        self.count = count
        self.brightness = brightness
        self.verbose = verbose

    def _send(self, command):
        os.write(self.fd, (command + "\n").encode("ascii"))
        response = read_available(self.fd, 0.15)
        if self.verbose and response.strip():
            for line in response.strip().splitlines():
                info(Style.dim("  arduino: " + line.strip()))
        if "ERR" in response:
            info(Style.red("  arduino error: " + response.strip()))

    def set_one(self, index, rgb):
        r, g, b = scale(rgb, self.brightness)
        self._send(f"set {index} {r} {g} {b}")

    def frame(self, colors):
        """colors: list of (r,g,b) of length self.count."""
        values = []
        for rgb in colors:
            r, g, b = scale(rgb, self.brightness)
            values.extend((r, g, b))
        self._send("frame " + " ".join(str(v) for v in values))

    def fill(self, rgb):
        self.frame([rgb] * self.count)

    def clear(self):
        self._send("clear")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_all(strip, color, **_):
    step(f"solid fill {Style.swatch(color)} {color}")
    strip.fill(color)


def test_off(strip, **_):
    step("clearing strip")
    strip.clear()


def test_index(strip, index, color, **_):
    if not 0 <= index < strip.count:
        info(Style.red(f"index {index} out of range 0-{strip.count - 1}"))
        return
    step(f"lighting LED {index} {Style.swatch(color)} {color}")
    strip.clear()
    strip.set_one(index, color)


def test_wipe(strip, color, delay, **_):
    step(f"color wipe {Style.swatch(color)} {color}")
    pixels = [(0, 0, 0)] * strip.count
    for i in range(strip.count):
        pixels[i] = color
        strip.frame(pixels)
        time.sleep(delay)


def test_chase(strip, color, delay, cycles, **_):
    step(f"chase {Style.swatch(color)} {color} ({cycles}x)")
    for _ in range(cycles):
        for i in range(strip.count):
            pixels = [(0, 0, 0)] * strip.count
            pixels[i] = color
            # small comet tail for visibility
            if i - 1 >= 0:
                pixels[i - 1] = scale(color, 90)
            strip.frame(pixels)
            time.sleep(delay)
    strip.clear()


def test_rainbow(strip, delay, cycles, **_):
    step(f"rainbow ({cycles}x)")
    for c in range(cycles):
        for offset in range(0, 256, 4):
            pixels = [
                wheel((i * 256 // strip.count + offset)) for i in range(strip.count)
            ]
            strip.frame(pixels)
            time.sleep(delay)
    strip.clear()


def test_blink(strip, color, cycles, **_):
    step(f"blink {Style.swatch(color)} {color} ({cycles}x)")
    for _ in range(cycles):
        strip.fill(color)
        time.sleep(0.25)
        strip.clear()
        time.sleep(0.25)


def test_every(strip, color, nth, offset, rest=(0, 0, 0), **_):
    if nth < 1:
        info(Style.red("step must be >= 1"))
        return
    indices = list(range(offset, strip.count, nth))
    step(
        f"every {nth} (offset {offset}): {len(indices)} lit "
        f"{Style.swatch(color)} {color}, rest {Style.swatch(rest)} {rest}"
    )
    pixels = [rest] * strip.count
    for i in indices:
        pixels[i] = color
    strip.frame(pixels)
    info(Style.dim("  lit indices: " + ", ".join(str(i) for i in indices)))


def test_ruler(strip, **_):
    step("ruler: red = every 10th LED, blue = every 5th, dim white between")
    pixels = []
    for i in range(strip.count):
        if i % 10 == 0:
            pixels.append((80, 0, 0))
        elif i % 5 == 0:
            pixels.append((0, 0, 80))
        else:
            pixels.append((6, 6, 6))
    strip.frame(pixels)
    reds = [i for i in range(strip.count) if i % 10 == 0]
    info(Style.dim("  red ticks at indices: " + ", ".join(str(i) for i in reds)))
    info(
        Style.dim(
            "  count the LIT LEDs: find the last red tick, then count LEDs after it.\n"
            "  total = (last lit index) + 1. Indices beyond the strip stay dark."
        )
    )


def test_rgb(strip, **_):
    step("color-order check (verify the strip shows the named color)")
    for name in ("red", "green", "blue", "white"):
        rgb = NAMED_COLORS[name]
        info(f"  should be {Style.swatch(rgb)} {Style.bold(name)}")
        strip.fill(rgb)
        time.sleep(0.9)
    strip.clear()
    info(
        Style.dim(
            "  If red/green look swapped, change NEO_GRB <-> NEO_RGB in the sketch."
        )
    )


def test_count(strip, delay, **_):
    step("counting LEDs one at a time")
    for i in range(strip.count):
        strip.clear()
        strip.set_one(i, (255, 255, 255))
        info(f"  LED {i}")
        time.sleep(delay)
    strip.clear()
    info(Style.dim(f"  expected {strip.count} LEDs"))


def test_auto(strip, color, delay, **_):
    step("running full self-test sequence")
    test_rgb(strip)
    test_wipe(strip, color=NAMED_COLORS["green"], delay=delay)
    test_chase(strip, color=color, delay=delay, cycles=2)
    test_rainbow(strip, delay=delay, cycles=2)
    test_blink(strip, color=NAMED_COLORS["white"], cycles=3)
    test_off(strip)
    info(Style.green("self-test complete"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        description="Test the WS2812 serial LED strip.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run with no test name to play the full self-test sequence.",
    )
    parser.add_argument(
        "--port", required=True, help="serial port, e.g. /dev/ttyACM0 or /dev/ttyUSB0"
    )
    parser.add_argument("--baud", type=int, default=115200, choices=BAUD_RATES.keys())
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_COUNT,
        help=f"number of LEDs (must match the sketch, default {DEFAULT_COUNT})",
    )
    parser.add_argument(
        "--brightness",
        type=int,
        default=255,
        help="0-255 master brightness applied in software (default 255)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.04,
        help="seconds between animation steps (default 0.04)",
    )
    parser.add_argument(
        "--cycles", type=int, default=2, help="repeat count for animations (default 2)"
    )
    parser.add_argument(
        "--no-reset-wait",
        action="store_true",
        help="do not wait for the Arduino READY banner",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print Arduino replies")

    sub = parser.add_subparsers(dest="test")

    p = sub.add_parser("all", help="fill the whole strip with one color")
    p.add_argument("color", nargs="?", type=parse_color, default=NAMED_COLORS["white"])

    sub.add_parser("off", help="turn the strip off")

    p = sub.add_parser("index", help="light a single LED")
    p.add_argument("index", type=int)
    p.add_argument("color", nargs="?", type=parse_color, default=NAMED_COLORS["white"])

    p = sub.add_parser("wipe", help="fill the strip one LED at a time")
    p.add_argument("color", nargs="?", type=parse_color, default=NAMED_COLORS["green"])

    p = sub.add_parser("chase", help="a dot runs along the strip")
    p.add_argument("color", nargs="?", type=parse_color, default=NAMED_COLORS["cyan"])

    sub.add_parser("rainbow", help="animated rainbow across the strip")

    p = sub.add_parser("blink", help="blink the whole strip")
    p.add_argument("color", nargs="?", type=parse_color, default=NAMED_COLORS["white"])

    p = sub.add_parser("every", help="light every Nth LED (e.g. every 3rd)")
    p.add_argument("color", nargs="?", type=parse_color, default=NAMED_COLORS["red"])
    p.add_argument("--step", type=int, default=3, help="spacing between lit LEDs (default 3)")
    p.add_argument("--offset", type=int, default=0, help="index of the first lit LED (default 0)")
    p.add_argument("--rest", type=parse_color, default=NAMED_COLORS["off"], help="color for the non-selected LEDs (default off)")

    sub.add_parser("rgb", help="cycle red/green/blue/white to verify color order")
    sub.add_parser("count", help="light each LED in turn to count them")
    sub.add_parser("ruler", help="color-coded ticks to read off the LED count at a glance")
    sub.add_parser("auto", help="run the full self-test sequence (default)")

    return parser


def run_test(strip, args):
    name = args.test or "auto"
    common = dict(delay=args.delay, cycles=args.cycles)

    if name == "all":
        test_all(strip, color=args.color)
    elif name == "off":
        test_off(strip)
    elif name == "index":
        test_index(strip, index=args.index, color=args.color)
    elif name == "wipe":
        test_wipe(strip, color=args.color, **common)
    elif name == "chase":
        test_chase(strip, color=args.color, **common)
    elif name == "rainbow":
        test_rainbow(strip, **common)
    elif name == "blink":
        test_blink(strip, color=args.color, cycles=args.cycles)
    elif name == "every":
        test_every(strip, color=args.color, nth=args.step, offset=args.offset, rest=args.rest)
    elif name == "ruler":
        test_ruler(strip)
    elif name == "rgb":
        test_rgb(strip)
    elif name == "count":
        test_count(strip, delay=max(args.delay, 0.12))
    else:  # auto
        test_auto(strip, color=NAMED_COLORS["cyan"], delay=args.delay)


def main():
    args = build_parser().parse_args()

    if not 0 <= args.brightness <= 255:
        info(Style.red("brightness must be 0-255"))
        return 1
    if args.count <= 0:
        info(Style.red("count must be positive"))
        return 1

    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    strip = Strip(fd, args.count, args.brightness, verbose=args.verbose)

    def cleanup(*_):
        try:
            strip.clear()
        finally:
            os.close(fd)
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)

    try:
        configure_serial(fd, args.baud)
        if not args.no_reset_wait:
            info(Style.dim("waiting for Arduino..."))
            ready = wait_for_ready(fd, 3.0)
            if "READY" not in ready:
                info(Style.red("warning: no READY banner (wrong port or sketch?)"))
            # The sketch keeps streaming its help banner after READY; drain it
            # and flush input so the first command isn't mixed with the banner.
            read_available(fd, 0.4)
            termios.tcflush(fd, termios.TCIFLUSH)

        info(
            Style.bold(
                f"strip: {args.count} LEDs @ {args.baud} baud, brightness {args.brightness}"
            )
        )
        run_test(strip, args)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
