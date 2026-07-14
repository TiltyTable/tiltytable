#!/usr/bin/env python3
import argparse
import math
import os
import select
import sys
import time
from pathlib import Path

import numpy as np
from pyk4a import (
    ColorResolution,
    Config,
    ImageFormat,
    K4AException,
    K4ATimeoutException,
    PyK4A,
    connected_device_count,
)

from live_capture_viewer import (
    COLOR_RESOLUTIONS,
    DEPTH_ENGINE_DISPLAY,
    DEPTH_MODES,
    FPS_VALUES,
    get_depth,
    get_latest_capture,
    set_display,
)


# Servos controlled by this script. These are PCA9685 channels on the Arduino.
SERVO_CHANNELS = (0, 1, 2, 3)

# Edit these constants to choose the depth pixel used for each servo channel.
# Coordinates are in the Kinect depth image unless you pass --aligned-depth.
SERVO_DEPTH_PIXELS = {
    0: (150, 365),  # channel 0
    1: (225, 365),  # channel 1
    2: (300, 365),  # channel 2
    3: (375, 365),  # channel 3
}

# Keep this at 1 to read exactly each SERVO_DEPTH_PIXELS coordinate. Use 3 or 5
# to median a tiny neighborhood around each pixel if exact pixels are noisy.
DEPTH_PIXEL_WINDOW = 1
MIN_VALID_DEPTH_PIXELS = 1


SERVO_DIR = Path(__file__).resolve().parent / "arduino" / "archive" / "pca9685_serial_servo"
sys.path.insert(0, str(SERVO_DIR))

from servo_write import BAUD_RATES, configure_serial, wait_for_ready  # noqa: E402


DEPTH_COLOR_RESOLUTIONS = {
    **COLOR_RESOLUTIONS,
    "off": ColorResolution.OFF,
}


def read_response_quick(fd, max_seconds, idle_seconds=0.004):
    if max_seconds <= 0:
        return ""

    end = time.monotonic() + max_seconds
    chunks = []
    saw_data = False

    while time.monotonic() < end:
        timeout = min(idle_seconds if saw_data else 0.01, end - time.monotonic())
        ready, _, _ = select.select([fd], [], [], max(0.0, timeout))
        if not ready:
            if saw_data:
                break
            continue

        chunk = os.read(fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
        saw_data = True

    return b"".join(chunks).decode("utf-8", errors="replace")


class ServoController:
    def __init__(
        self,
        port,
        baud,
        ready_timeout,
        response_wait,
        response_idle,
        dry_run=False,
        verbose=False,
    ):
        self.port = port
        self.baud = baud
        self.ready_timeout = ready_timeout
        self.response_wait = response_wait
        self.response_idle = response_idle
        self.dry_run = dry_run
        self.verbose = verbose
        self.fd = None

    def open(self):
        if self.dry_run:
            print("Servo dry run enabled; serial port will not be opened.")
            return

        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_SYNC)
        configure_serial(self.fd, self.baud)
        startup = wait_for_ready(self.fd, self.ready_timeout)
        if self.verbose and startup.strip():
            print_prefixed("arduino: ", startup.strip())

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def write_angle(self, channel, angle):
        command = f"a {channel} {angle:.3f}\n"
        if self.dry_run:
            print(f"servo dry run: {command.strip()}")
            return ""

        if self.fd is None:
            raise RuntimeError("serial port is not open")

        os.write(self.fd, command.encode("ascii"))
        response = read_response_quick(self.fd, self.response_wait, self.response_idle).strip()
        if self.verbose and response:
            print_prefixed("arduino: ", response)
        if "ERR" in response:
            raise RuntimeError(response)
        return response


def print_prefixed(prefix, text):
    for line in text.splitlines():
        print(f"{prefix}{line}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Use Azure Kinect depth feedback to move PCA9685/Arduino servo "
            "channels 0-3 until their target depths are reached."
        )
    )
    parser.add_argument(
        "target_depth_mm",
        nargs=len(SERVO_CHANNELS),
        type=float,
        metavar="TARGET_MM",
        help="desired depth in millimeters for servo channels 0, 1, 2, and 3",
    )

    kinect = parser.add_argument_group("Azure Kinect")
    kinect.add_argument("--device-id", type=int, default=0, help="Azure Kinect device index")
    kinect.add_argument(
        "--color-resolution",
        choices=sorted(DEPTH_COLOR_RESOLUTIONS),
        default="off",
        help="color camera resolution; off is fastest when measuring raw depth pixels",
    )
    kinect.add_argument(
        "--depth-mode",
        choices=sorted(DEPTH_MODES),
        default="nfov_unbinned",
        help="depth camera mode",
    )
    kinect.add_argument(
        "--fps",
        choices=sorted(FPS_VALUES, key=int),
        default="30",
        help="camera frame rate",
    )
    kinect.add_argument(
        "--timeout-ms",
        type=int,
        default=1000,
        help="capture timeout in milliseconds",
    )
    kinect.add_argument(
        "--aligned-depth",
        action="store_true",
        help="measure depth transformed into the color camera view",
    )
    kinect.add_argument(
        "--depth-engine-display",
        default=DEPTH_ENGINE_DISPLAY,
        help="DISPLAY value used by the Azure Kinect depth engine; use '' to leave unchanged",
    )

    roi = parser.add_argument_group("Depth sample")
    roi.add_argument(
        "--roi-size",
        type=int,
        default=DEPTH_PIXEL_WINDOW,
        help="square sample window in pixels; 1 means exactly each configured pixel",
    )
    roi.add_argument(
        "--min-valid-pixels",
        type=int,
        default=MIN_VALID_DEPTH_PIXELS,
        help="minimum valid depth pixels required inside the sample window",
    )
    roi.add_argument(
        "--max-valid-depth",
        type=float,
        default=0.0,
        help="discard ROI depths above this many mm; 0 disables this filter",
    )
    roi.add_argument(
        "--sample-count",
        type=int,
        default=1,
        help="number of captures to median together for each control reading",
    )
    roi.add_argument(
        "--sample-interval",
        type=float,
        default=0.0,
        help="seconds between depth samples within one control reading",
    )

    servo = parser.add_argument_group("Servo")
    servo.add_argument("--port", default="/dev/ttyACM0", help="Arduino serial port")
    servo.add_argument(
        "--baud",
        type=int,
        default=115200,
        choices=sorted(BAUD_RATES),
        help="serial baud rate",
    )
    servo.add_argument("--start-angle", type=float, default=90.0, help="first angle command in degrees")
    servo.add_argument("--min-angle", type=float, default=10.0, help="minimum allowed servo angle")
    servo.add_argument("--max-angle", type=float, default=170.0, help="maximum allowed servo angle")
    servo.add_argument("--step-deg", type=float, default=2.0, help="servo angle step per correction")
    servo.add_argument(
        "--min-step-deg",
        type=float,
        default=0.25,
        help="smallest step after auto-reverse shrinkage",
    )
    servo.add_argument(
        "--reverse",
        action="store_true",
        help="invert the initial correction direction if your linkage moves the other way",
    )
    servo.add_argument(
        "--move-delay",
        type=float,
        default=0.03,
        help="seconds to wait after each servo command before reading depth again",
    )
    servo.add_argument(
        "--ready-timeout",
        type=float,
        default=4.0,
        help="time to wait for Arduino READY after opening the port",
    )
    servo.add_argument(
        "--response-wait",
        type=float,
        default=0.03,
        help="maximum seconds to wait for each Arduino command response",
    )
    servo.add_argument(
        "--response-idle",
        type=float,
        default=0.004,
        help="stop reading an Arduino response after this much serial idle time",
    )
    servo.add_argument(
        "--dry-run-servo",
        action="store_true",
        help="print servo commands without opening the serial port",
    )

    control = parser.add_argument_group("Control")
    control.add_argument("--tolerance-mm", type=float, default=2.0, help="stop inside this depth error")
    control.add_argument("--max-iterations", type=int, default=200, help="maximum control loop iterations")
    control.add_argument("--max-invalid", type=int, default=10, help="stop after this many invalid readings")
    control.add_argument(
        "--no-auto-reverse",
        dest="auto_reverse",
        action="store_false",
        help="do not reverse/shrink step when depth error gets worse",
    )
    control.add_argument(
        "--worse-margin-mm",
        type=float,
        default=25.0,
        help="extra error growth required before auto-reversing",
    )
    control.add_argument(
        "--step-shrink",
        type=float,
        default=0.5,
        help="multiply step by this when auto-reversing",
    )
    control.add_argument(
        "--status-every",
        type=int,
        default=1,
        help="print normal per-channel status every N iterations; 0 disables normal status lines",
    )
    control.add_argument("--verbose", action="store_true", help="print Arduino startup and responses")
    parser.set_defaults(auto_reverse=True)

    args = parser.parse_args()
    validate_args(parser, args)
    return args


def validate_args(parser, args):
    if any(target <= 0 for target in args.target_depth_mm):
        parser.error("all target depths must be greater than 0")
    for channel in SERVO_CHANNELS:
        pixel = SERVO_DEPTH_PIXELS.get(channel)
        if not isinstance(pixel, tuple) or len(pixel) != 2:
            parser.error(f"SERVO_DEPTH_PIXELS[{channel}] must be an (x, y) tuple")
        x, y = pixel
        if not isinstance(x, int) or not isinstance(y, int):
            parser.error(f"SERVO_DEPTH_PIXELS[{channel}] must contain integer pixel coordinates")
        if x < 0 or y < 0:
            parser.error(f"SERVO_DEPTH_PIXELS[{channel}] cannot contain negative pixel coordinates")
    if args.aligned_depth and args.color_resolution == "off":
        parser.error("--aligned-depth requires --color-resolution to be a real color resolution, not off")
    if args.device_id < 0:
        parser.error("--device-id must be 0 or greater")
    if args.timeout_ms <= 0:
        parser.error("--timeout-ms must be greater than 0")
    if args.roi_size <= 0:
        parser.error("--roi-size must be greater than 0")
    if args.min_valid_pixels <= 0:
        parser.error("--min-valid-pixels must be greater than 0")
    if args.min_valid_pixels > args.roi_size * args.roi_size:
        parser.error("--min-valid-pixels cannot exceed --roi-size squared")
    if args.sample_count <= 0:
        parser.error("--sample-count must be greater than 0")
    if args.sample_interval < 0:
        parser.error("--sample-interval cannot be negative")
    if not 0 <= args.min_angle < args.max_angle <= 180:
        parser.error("--min-angle and --max-angle must satisfy 0 <= min < max <= 180")
    if not args.min_angle <= args.start_angle <= args.max_angle:
        parser.error("--start-angle must be inside --min-angle and --max-angle")
    if args.step_deg <= 0:
        parser.error("--step-deg must be greater than 0")
    if args.min_step_deg <= 0 or args.min_step_deg > args.step_deg:
        parser.error("--min-step-deg must be greater than 0 and no larger than --step-deg")
    if args.move_delay < 0:
        parser.error("--move-delay cannot be negative")
    if args.ready_timeout < 0:
        parser.error("--ready-timeout cannot be negative")
    if args.response_wait < 0:
        parser.error("--response-wait cannot be negative")
    if args.response_idle < 0:
        parser.error("--response-idle cannot be negative")
    if args.tolerance_mm <= 0:
        parser.error("--tolerance-mm must be greater than 0")
    if args.max_iterations <= 0:
        parser.error("--max-iterations must be greater than 0")
    if args.max_invalid <= 0:
        parser.error("--max-invalid must be greater than 0")
    if args.status_every < 0:
        parser.error("--status-every cannot be negative")
    if args.worse_margin_mm < 0:
        parser.error("--worse-margin-mm cannot be negative")
    if not 0 < args.step_shrink <= 1:
        parser.error("--step-shrink must be in the range (0, 1]")


def clamp(value, low, high):
    return max(low, min(high, value))


def roi_bounds(depth_shape, center_x, center_y, window_size):
    height, width = depth_shape[:2]

    if not 0 <= center_x < width:
        raise ValueError(f"depth pixel x={center_x} is outside depth image width {width}")
    if not 0 <= center_y < height:
        raise ValueError(f"depth pixel y={center_y} is outside depth image height {height}")

    half = window_size // 2
    x0 = clamp(center_x - half, 0, width)
    y0 = clamp(center_y - half, 0, height)
    x1 = clamp(x0 + window_size, 0, width)
    y1 = clamp(y0 + window_size, 0, height)
    x0 = clamp(x1 - window_size, 0, width)
    y0 = clamp(y1 - window_size, 0, height)
    return int(x0), int(y0), int(x1), int(y1)


def measure_pixel_depth(depth_mm, args, pixel):
    if depth_mm is None:
        return None, 0, 0, None
    if depth_mm.ndim != 2:
        raise ValueError(f"expected a 2D depth image, got shape {depth_mm.shape}")

    x0, y0, x1, y1 = roi_bounds(depth_mm.shape, pixel[0], pixel[1], args.roi_size)
    sample = depth_mm[y0:y1, x0:x1].astype(np.float32, copy=False)
    valid = np.isfinite(sample) & (sample > 0)
    if args.max_valid_depth > 0:
        valid &= sample <= args.max_valid_depth

    valid_count = int(np.count_nonzero(valid))
    total_count = int(sample.size)
    if valid_count < args.min_valid_pixels:
        return None, valid_count, total_count, (x0, y0, x1, y1)

    return float(np.median(sample[valid])), valid_count, total_count, (x0, y0, x1, y1)


def read_depth_frame(k4a, args):
    capture = get_latest_capture(k4a, args.timeout_ms)
    return get_depth(capture, args.aligned_depth)


def sample_channel_depths(k4a, args):
    depth_samples = {channel: [] for channel in SERVO_CHANNELS}
    last_results = {
        channel: (None, 0, 0, None)
        for channel in SERVO_CHANNELS
    }

    for sample_index in range(args.sample_count):
        depth_mm = read_depth_frame(k4a, args)
        for channel in SERVO_CHANNELS:
            result = measure_pixel_depth(depth_mm, args, SERVO_DEPTH_PIXELS[channel])
            depth, _, _, _ = result
            last_results[channel] = result
            if depth is not None:
                depth_samples[channel].append(depth)

        if sample_index + 1 < args.sample_count and args.sample_interval > 0:
            time.sleep(args.sample_interval)

    results = {}
    for channel in SERVO_CHANNELS:
        _, valid_count, total_count, bounds = last_results[channel]
        if depth_samples[channel]:
            depth = float(np.median(depth_samples[channel]))
        else:
            depth = None
        results[channel] = (depth, valid_count, total_count, bounds)
    return results


def make_kinect_config(args):
    return Config(
        color_resolution=DEPTH_COLOR_RESOLUTIONS[args.color_resolution],
        color_format=ImageFormat.COLOR_BGRA32,
        depth_mode=DEPTH_MODES[args.depth_mode],
        camera_fps=FPS_VALUES[args.fps],
        synchronized_images_only=args.aligned_depth,
    )


def print_loop_status(iteration, channel, angle, depth, target, valid_count, total_count, bounds, step):
    error = depth - target
    if bounds is None:
        sample_text = "unknown"
    elif bounds[2] - bounds[0] == 1 and bounds[3] - bounds[1] == 1:
        sample_text = f"pixel=({bounds[0]},{bounds[1]})"
    else:
        sample_text = f"window=x[{bounds[0]}:{bounds[2]}] y[{bounds[1]}:{bounds[3]}]"
    print(
        f"{iteration:03d} "
        f"ch={channel} "
        f"angle={angle:7.3f} deg "
        f"depth={depth:8.1f} mm "
        f"target={target:8.1f} mm "
        f"error={error:+8.1f} mm "
        f"valid={valid_count}/{total_count} "
        f"sample={sample_text} "
        f"step={step:.3f}"
    )


def control_loop(k4a, servo, args):
    targets = dict(zip(SERVO_CHANNELS, args.target_depth_mm))
    angles = {channel: args.start_angle for channel in SERVO_CHANNELS}
    steps = {channel: args.step_deg for channel in SERVO_CHANNELS}
    directions = {
        channel: -1.0 if args.reverse else 1.0
        for channel in SERVO_CHANNELS
    }
    last_abs_errors = {channel: None for channel in SERVO_CHANNELS}
    invalid_streaks = {channel: 0 for channel in SERVO_CHANNELS}
    best = {
        channel: {"angle": None, "depth": None, "error": None}
        for channel in SERVO_CHANNELS
    }

    print("Moving servo channels 0-3 to start angle " f"{args.start_angle:.3f} deg.")
    for channel in SERVO_CHANNELS:
        servo.write_angle(channel, angles[channel])
    if args.move_delay > 0:
        time.sleep(args.move_delay)

    for iteration in range(1, args.max_iterations + 1):
        show_status = args.status_every > 0 and iteration % args.status_every == 0
        try:
            readings = sample_channel_depths(k4a, args)
        except K4ATimeoutException:
            readings = {
                channel: (None, 0, 0, None)
                for channel in SERVO_CHANNELS
            }

        all_within_tolerance = True
        moves = []

        for channel in SERVO_CHANNELS:
            depth, valid_count, total_count, bounds = readings[channel]
            target = targets[channel]

            if depth is None:
                invalid_streaks[channel] += 1
                all_within_tolerance = False
                print(
                    f"{iteration:03d} ch={channel} no valid depth "
                    f"valid={valid_count}/{total_count} "
                    f"invalid_streak={invalid_streaks[channel]}/{args.max_invalid}"
                )
                if invalid_streaks[channel] >= args.max_invalid:
                    print(
                        f"Stopping because channel {channel} had too many "
                        "consecutive invalid depth readings."
                    )
                    return 2
                continue

            invalid_streaks[channel] = 0
            error = depth - target
            abs_error = abs(error)
            if show_status:
                print_loop_status(
                    iteration,
                    channel,
                    angles[channel],
                    depth,
                    target,
                    valid_count,
                    total_count,
                    bounds,
                    steps[channel],
                )

            if best[channel]["error"] is None or abs_error < abs(best[channel]["error"]):
                best[channel] = {
                    "angle": angles[channel],
                    "depth": depth,
                    "error": error,
                }

            if abs_error <= args.tolerance_mm:
                continue

            all_within_tolerance = False
            if (
                args.auto_reverse
                and last_abs_errors[channel] is not None
                and abs_error > last_abs_errors[channel] + args.worse_margin_mm
            ):
                directions[channel] *= -1.0
                new_step = max(args.min_step_deg, steps[channel] * args.step_shrink)
                if new_step < steps[channel]:
                    steps[channel] = new_step
                print(
                    f"{iteration:03d} ch={channel} depth error grew; reversing "
                    f"correction direction and using step {steps[channel]:.3f} deg."
                )

            delta = directions[channel] * math.copysign(steps[channel], error)
            next_angle = clamp(angles[channel] + delta, args.min_angle, args.max_angle)
            if math.isclose(next_angle, angles[channel], abs_tol=1e-9):
                print(
                    f"Stopping: channel {channel} is at angle limit "
                    f"{angles[channel]:.3f} deg with error {error:+.1f} mm. "
                    "The target depth may be outside this servo range."
                )
                return 3

            moves.append((channel, next_angle, abs_error))

        if all_within_tolerance:
            print(
                "Reached all targets: channels 0-3 are within "
                f"{args.tolerance_mm:.1f} mm of their requested depths."
            )
            return 0

        for channel, next_angle, abs_error in moves:
            servo.write_angle(channel, next_angle)
            angles[channel] = next_angle
            last_abs_errors[channel] = abs_error

        if moves and args.move_delay > 0:
            time.sleep(args.move_delay)

    print(f"Stopping after {args.max_iterations} iterations without reaching all tolerance bands.")
    for channel in SERVO_CHANNELS:
        if best[channel]["angle"] is None:
            continue
        print(
            f"Best channel {channel}: angle={best[channel]['angle']:.3f} deg "
            f"depth={best[channel]['depth']:.1f} mm "
            f"error={best[channel]['error']:+.1f} mm."
        )
    return 4


def main():
    args = parse_args()

    if args.depth_engine_display:
        set_display(args.depth_engine_display, "depth engine", quiet=not args.verbose)

    device_count = connected_device_count()
    if device_count <= args.device_id:
        print(
            f"No Azure Kinect device at index {args.device_id}; found {device_count} device(s).",
            file=sys.stderr,
        )
        return 1

    k4a = PyK4A(config=make_kinect_config(args), device_id=args.device_id)
    servo = ServoController(
        port=args.port,
        baud=args.baud,
        ready_timeout=args.ready_timeout,
        response_wait=args.response_wait,
        response_idle=args.response_idle,
        dry_run=args.dry_run_servo,
        verbose=args.verbose,
    )

    try:
        print("Starting Azure Kinect and servo control loop...")
        k4a.start()
        servo.open()
        return control_loop(k4a, servo, args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except PermissionError as exc:
        print(f"serial permission error: {exc}", file=sys.stderr)
        print("Try: sudo usermod -a -G dialout $USER, then log out and back in.", file=sys.stderr)
        print("Temporary test: sudo chmod a+rw /dev/ttyACM0", file=sys.stderr)
        return 1
    except (K4AException, RuntimeError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        servo.close()
        if k4a.is_running:
            k4a.stop()


if __name__ == "__main__":
    raise SystemExit(main())
