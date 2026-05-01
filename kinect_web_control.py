#!/usr/bin/env python3
import argparse
import json
import math
import mimetypes
import os
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import cv2
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

from depth_servo_control import (
    DEPTH_PIXEL_WINDOW,
    MIN_VALID_DEPTH_PIXELS,
    SERVO_CHANNELS,
    SERVO_DEPTH_PIXELS,
    ServoController,
)
from live_capture_viewer import (
    COLOR_RESOLUTIONS,
    DEPTH_ENGINE_DISPLAY,
    DEPTH_MODES,
    FPS_VALUES,
    color_to_bgr,
    depth_to_display,
    get_depth,
    set_display,
)
from servo_write import BAUD_RATES


ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
DEFAULT_CONFIG_PATH = ROOT_DIR / "web_control_config.json"
WEB_COLOR_RESOLUTIONS = {
    **COLOR_RESOLUTIONS,
    "off": ColorResolution.OFF,
}
SERVO_COLORS = {
    0: "#ffcc4d",
    1: "#ff6b6b",
    2: "#44d7b6",
    3: "#78a6ff",
}
DEFAULT_DEPTH_BOX_SIZE = 25


@dataclass(frozen=True)
class DepthReading:
    depth_mm: float | None
    valid_pixels: int
    total_pixels: int
    bounds: tuple[int, int, int, int] | None


class WebState:
    def __init__(self, config_path, default_box_size=DEFAULT_DEPTH_BOX_SIZE):
        self.config_path = Path(config_path)
        self.default_box_size = int(default_box_size)
        self.lock = threading.RLock()
        self.boxes = {
            channel: box_from_center_pixel(SERVO_DEPTH_PIXELS[channel], self.default_box_size)
            for channel in SERVO_CHANNELS
        }
        self.targets = {channel: 1000.0 for channel in SERVO_CHANNELS}
        self.angles = {channel: None for channel in SERVO_CHANNELS}
        self.reached = {channel: False for channel in SERVO_CHANNELS}
        self.last_control_error = {channel: None for channel in SERVO_CHANNELS}
        self.control_running = False
        self.control_message = "idle"
        self.control_error = ""
        self.load()

    def load(self):
        if not self.config_path.exists():
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Could not load {self.config_path}: {exc}", file=sys.stderr)
            return

        with self.lock:
            servos = data.get("servos", {})
            for channel in SERVO_CHANNELS:
                item = servos.get(str(channel), {})
                box = item.get("box")
                if isinstance(box, list) and len(box) == 4:
                    try:
                        self.boxes[channel] = normalize_box(box)
                    except (TypeError, ValueError):
                        pass
                else:
                    # Backward compatibility for older configs saved as a single pixel.
                    pixel = item.get("pixel")
                    if isinstance(pixel, list) and len(pixel) == 2:
                        try:
                            self.boxes[channel] = box_from_center_pixel(pixel, self.default_box_size)
                        except (TypeError, ValueError):
                            pass
                target = item.get("target_depth_mm")
                if isinstance(target, (int, float)) and target > 0:
                    self.targets[channel] = float(target)

    def save(self):
        with self.lock:
            data = {
                "servos": {
                    str(channel): {
                        "box": list(self.boxes[channel]),
                        "target_depth_mm": self.targets[channel],
                    }
                    for channel in SERVO_CHANNELS
                }
            }
        tmp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.config_path)

    def set_box(self, channel, box):
        with self.lock:
            self.boxes[channel] = normalize_box(box)
        self.save()

    def set_target(self, channel, target_depth_mm):
        with self.lock:
            self.targets[channel] = float(target_depth_mm)
        self.save()

    def snapshot_config(self):
        with self.lock:
            return {
                channel: {
                    "box": self.boxes[channel],
                    "target_depth_mm": self.targets[channel],
                }
                for channel in SERVO_CHANNELS
            }

    def set_angle(self, channel, angle):
        with self.lock:
            self.angles[channel] = float(angle)

    def set_channel_status(self, channel, reached=None, error=None):
        with self.lock:
            if reached is not None:
                self.reached[channel] = bool(reached)
            if error is not None:
                self.last_control_error[channel] = float(error)

    def set_control(self, running, message=None, error=""):
        with self.lock:
            self.control_running = bool(running)
            if message is not None:
                self.control_message = message
            self.control_error = error

    def to_json(self, camera, args):
        depth_mm = camera.get_depth_snapshot()
        depth_shape = camera.get_depth_shape()
        with self.lock:
            config = self.snapshot_config()
            angles = dict(self.angles)
            reached = dict(self.reached)
            control_errors = dict(self.last_control_error)
            control_running = self.control_running
            control_message = self.control_message
            control_error = self.control_error

        servos = []
        for channel in SERVO_CHANNELS:
            box = config[channel]["box"]
            target = config[channel]["target_depth_mm"]
            reading = measure_depth_in_box(
                depth_mm,
                box,
                args.min_valid_pixels,
                args.max_valid_depth,
            )
            current_error = None if reading.depth_mm is None else reading.depth_mm - target
            servos.append(
                {
                    "channel": channel,
                    "color": SERVO_COLORS[channel],
                    "box": {
                        "x": box[0],
                        "y": box[1],
                        "width": box[2],
                        "height": box[3],
                    },
                    "target_depth_mm": target,
                    "current_depth_mm": reading.depth_mm,
                    "current_error_mm": current_error,
                    "valid_pixels": reading.valid_pixels,
                    "total_pixels": reading.total_pixels,
                    "sample_bounds": reading.bounds,
                    "angle_deg": angles[channel],
                    "reached": reached[channel],
                    "control_error_mm": control_errors[channel],
                }
            )

        width = depth_shape[1] if depth_shape is not None else None
        height = depth_shape[0] if depth_shape is not None else None
        return {
            "servos": servos,
            "servo_channels": list(SERVO_CHANNELS),
            "depth_image": {"width": width, "height": height},
            "camera": camera.status_snapshot(),
            "control": {
                "running": control_running,
                "message": control_message,
                "error": control_error,
            },
            "settings": {
                "default_box_size": args.default_box_size,
                "min_valid_pixels": args.min_valid_pixels,
                "max_valid_depth": args.max_valid_depth,
                "tolerance_mm": args.tolerance_mm,
            },
        }


class KinectFrameHub:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Condition()
        self.stop_event = threading.Event()
        self.thread = None
        self.k4a = None
        self.seq = 0
        self.color_jpeg = None
        self.depth_jpeg = None
        self.depth_mm = None
        self.depth_shape = None
        self.status = "starting"
        self.error = ""
        self.fps = 0.0
        self._frame_count = 0
        self._fps_started_at = time.monotonic()
        self.placeholder_color = make_placeholder_jpeg(960, 540, "Waiting for Kinect color")
        self.placeholder_depth = make_placeholder_jpeg(640, 576, "Waiting for Kinect depth")

    def start(self):
        self.thread = threading.Thread(target=self._run, name="kinect-capture", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.lock.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self.k4a is not None and self.k4a.is_running:
            self.k4a.stop()

    def _set_status(self, status, error=""):
        with self.lock:
            self.status = status
            self.error = error
            self.lock.notify_all()

    def _run(self):
        args = self.args
        if args.depth_engine_display:
            set_display(args.depth_engine_display, "depth engine", quiet=not args.verbose)

        try:
            device_count = connected_device_count()
            if device_count <= args.device_id:
                self._set_status(
                    "error",
                    f"No Azure Kinect device at index {args.device_id}; found {device_count} device(s).",
                )
                return

            config = Config(
                color_resolution=WEB_COLOR_RESOLUTIONS[args.color_resolution],
                color_format=ImageFormat.COLOR_BGRA32,
                depth_mode=DEPTH_MODES[args.depth_mode],
                camera_fps=FPS_VALUES[args.fps],
                synchronized_images_only=args.color_resolution != "off" or args.aligned_depth,
            )
            self.k4a = PyK4A(config=config, device_id=args.device_id)
            self.k4a.start()
            self._set_status("running")

            while not self.stop_event.is_set():
                try:
                    capture = self.k4a.get_capture(timeout=args.timeout_ms)
                except K4ATimeoutException:
                    self._set_status("timeout", "Timed out waiting for a Kinect frame.")
                    continue

                depth_mm = get_depth(capture, args.aligned_depth)
                if depth_mm is None:
                    continue

                color_bgr = None
                if capture.color is not None:
                    color_bgr = color_to_bgr(capture.color)
                if color_bgr is None:
                    color_jpeg = self.placeholder_color
                else:
                    color_jpeg = encode_jpeg(color_bgr, args.jpeg_quality)

                depth_display = depth_to_display(depth_mm, args.max_depth)
                depth_jpeg = encode_jpeg(depth_display, args.jpeg_quality)

                now = time.monotonic()
                self._frame_count += 1
                elapsed = now - self._fps_started_at
                if elapsed >= 1.0:
                    self.fps = self._frame_count / elapsed
                    self._frame_count = 0
                    self._fps_started_at = now

                with self.lock:
                    self.seq += 1
                    self.color_jpeg = color_jpeg
                    self.depth_jpeg = depth_jpeg
                    self.depth_mm = depth_mm.copy()
                    self.depth_shape = depth_mm.shape[:2]
                    self.status = "running"
                    self.error = ""
                    self.lock.notify_all()
        except (K4AException, RuntimeError, ValueError, cv2.error) as exc:
            self._set_status("error", str(exc))
        finally:
            if self.k4a is not None and self.k4a.is_running:
                self.k4a.stop()

    def wait_for_jpeg(self, kind, last_seq, timeout=1.0):
        with self.lock:
            if self.seq == last_seq:
                self.lock.wait(timeout=timeout)
            seq = self.seq
            jpeg = self.color_jpeg if kind == "color" else self.depth_jpeg
            if jpeg is None:
                jpeg = self.placeholder_color if kind == "color" else self.placeholder_depth
            return seq, jpeg

    def get_depth_snapshot(self):
        with self.lock:
            if self.depth_mm is None:
                return None
            return self.depth_mm.copy()

    def get_depth_shape(self):
        with self.lock:
            return self.depth_shape

    def status_snapshot(self):
        with self.lock:
            return {
                "status": self.status,
                "error": self.error,
                "fps": self.fps,
                "frame_seq": self.seq,
            }


class ServoControlRunner:
    def __init__(self, state, camera, args):
        self.state = state
        self.camera = camera
        self.args = args
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, name="servo-control", daemon=True)
            self.thread.start()
            return True

    def stop(self):
        self.stop_event.set()
        with self.lock:
            thread = self.thread
        if thread is not None:
            thread.join(timeout=2.0)

    def _run(self):
        args = self.args
        controller = ServoController(
            port=args.servo_port,
            baud=args.baud,
            ready_timeout=args.ready_timeout,
            response_wait=args.response_wait,
            response_idle=args.response_idle,
            dry_run=args.dry_run_servo,
            verbose=args.verbose,
        )
        angles = {channel: args.start_angle for channel in SERVO_CHANNELS}
        steps = {channel: args.step_deg for channel in SERVO_CHANNELS}
        directions = {channel: -1.0 if args.reverse else 1.0 for channel in SERVO_CHANNELS}
        last_abs_errors = {channel: None for channel in SERVO_CHANNELS}
        invalid_streaks = {channel: 0 for channel in SERVO_CHANNELS}

        try:
            self.state.set_control(True, "opening serial")
            controller.open()
            for channel in SERVO_CHANNELS:
                controller.write_angle(channel, angles[channel])
                self.state.set_angle(channel, angles[channel])
            if args.move_delay > 0:
                time.sleep(args.move_delay)

            self.state.set_control(True, "running")
            while not self.stop_event.is_set():
                depth_mm = self.camera.get_depth_snapshot()
                if depth_mm is None:
                    self.state.set_control(True, "waiting for depth frame")
                    time.sleep(0.03)
                    continue

                configs = self.state.snapshot_config()
                moves = []
                reached_count = 0

                for channel in SERVO_CHANNELS:
                    box = configs[channel]["box"]
                    target = configs[channel]["target_depth_mm"]
                    reading = measure_depth_in_box(
                        depth_mm,
                        box,
                        args.min_valid_pixels,
                        args.max_valid_depth,
                    )

                    if reading.depth_mm is None:
                        invalid_streaks[channel] += 1
                        self.state.set_channel_status(channel, reached=False)
                        if invalid_streaks[channel] >= args.max_invalid:
                            self.state.set_control(
                                True,
                                f"channel {channel} has invalid depth; waiting",
                            )
                        continue

                    invalid_streaks[channel] = 0
                    error = reading.depth_mm - target
                    abs_error = abs(error)
                    self.state.set_channel_status(
                        channel,
                        reached=abs_error <= args.tolerance_mm,
                        error=error,
                    )

                    if abs_error <= args.tolerance_mm:
                        reached_count += 1
                        continue

                    if (
                        args.auto_reverse
                        and last_abs_errors[channel] is not None
                        and abs_error > last_abs_errors[channel] + args.worse_margin_mm
                    ):
                        directions[channel] *= -1.0
                        steps[channel] = max(args.min_step_deg, steps[channel] * args.step_shrink)

                    delta = directions[channel] * math.copysign(steps[channel], error)
                    next_angle = clamp(angles[channel] + delta, args.min_angle, args.max_angle)
                    if math.isclose(next_angle, angles[channel], abs_tol=1e-9):
                        continue

                    moves.append((channel, next_angle, abs_error))

                for channel, next_angle, abs_error in moves:
                    controller.write_angle(channel, next_angle)
                    angles[channel] = next_angle
                    last_abs_errors[channel] = abs_error
                    self.state.set_angle(channel, next_angle)

                self.state.set_control(True, f"running; {reached_count}/4 within tolerance")
                time.sleep(args.move_delay if moves else 0.03)
        except PermissionError as exc:
            self.state.set_control(False, "serial permission error", str(exc))
        except (RuntimeError, OSError, ValueError) as exc:
            self.state.set_control(False, "control stopped", str(exc))
        finally:
            controller.close()
            if self.stop_event.is_set():
                self.state.set_control(False, "stopped")


class KinectWebHandler(BaseHTTPRequestHandler):
    camera = None
    state = None
    control = None
    args = None
    static_dir = WEB_DIR

    def log_message(self, fmt, *args):
        if self.args and self.args.verbose:
            super().log_message(fmt, *args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.serve_static("index.html")
        elif path in ("/app.js", "/styles.css"):
            self.serve_static(path.lstrip("/"))
        elif path == "/api/state":
            self.send_json(self.state.to_json(self.camera, self.args))
        elif path == "/stream/color.mjpg":
            self.serve_mjpeg("color")
        elif path == "/stream/depth.mjpg":
            self.serve_mjpeg("depth")
        else:
            self.send_error(404, "not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.strip("/").split("/")
        try:
            data = self.read_json()
            if len(path) == 4 and path[0] == "api" and path[1] == "servos" and path[3] == "box":
                channel = self.parse_channel(path[2])
                x = int(data["x"])
                y = int(data["y"])
                width = int(data["width"])
                height = int(data["height"])
                box = normalize_box((x, y, width, height))
                self.validate_box(box)
                self.state.set_box(channel, box)
                self.send_json({"ok": True})
                return
            if len(path) == 4 and path[0] == "api" and path[1] == "servos" and path[3] == "pixel":
                # Backward-compatible helper: a pixel click becomes a default-sized box.
                channel = self.parse_channel(path[2])
                box = box_from_center_pixel((int(data["x"]), int(data["y"])), self.args.default_box_size)
                self.validate_box(box)
                self.state.set_box(channel, box)
                self.send_json({"ok": True})
                return
            if len(path) == 4 and path[0] == "api" and path[1] == "servos" and path[3] == "target":
                channel = self.parse_channel(path[2])
                target = float(data["target_depth_mm"])
                if target <= 0:
                    raise ValueError("target_depth_mm must be greater than 0")
                self.state.set_target(channel, target)
                self.send_json({"ok": True})
                return
            if path == ["api", "control", "start"]:
                started = self.control.start()
                self.send_json({"ok": True, "started": started})
                return
            if path == ["api", "control", "stop"]:
                self.control.stop()
                self.send_json({"ok": True})
                return
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        self.send_error(404, "not found")

    def serve_static(self, name):
        path = (self.static_dir / name).resolve()
        if not str(path).startswith(str(self.static_dir.resolve())) or not path.exists():
            self.send_error(404, "not found")
            return
        content = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def serve_mjpeg(self, kind):
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        last_seq = -1
        while True:
            seq, jpeg = self.camera.wait_for_jpeg(kind, last_seq)
            last_seq = seq
            try:
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                break

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def parse_channel(self, text):
        channel = int(text)
        if channel not in SERVO_CHANNELS:
            raise ValueError("channel must be one of 0, 1, 2, 3")
        return channel

    def validate_box(self, box):
        x, y, width, height = normalize_box(box)
        if x < 0 or y < 0:
            raise ValueError("box coordinates cannot be negative")
        if width * height < self.args.min_valid_pixels:
            raise ValueError(
                f"box area must be at least --min-valid-pixels ({self.args.min_valid_pixels})"
            )
        shape = self.camera.get_depth_shape()
        if shape is None:
            return
        image_height, image_width = shape
        if x + width > image_width or y + height > image_height:
            raise ValueError(
                f"box ({x}, {y}, {width}, {height}) is outside depth image "
                f"{image_width}x{image_height}"
            )


class KinectThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def clamp(value, low, high):
    return max(low, min(high, value))


def normalize_box(box):
    x, y, width, height = box
    x = int(round(float(x)))
    y = int(round(float(y)))
    width = int(round(float(width)))
    height = int(round(float(height)))
    if width <= 0 or height <= 0:
        raise ValueError("box width and height must be greater than 0")
    if x < 0 or y < 0:
        raise ValueError("box x and y cannot be negative")
    return x, y, width, height


def box_from_center_pixel(pixel, size):
    x, y = pixel
    size = max(1, int(size))
    half = size // 2
    return normalize_box((max(0, int(x) - half), max(0, int(y) - half), size, size))


def clip_box_to_depth(depth_shape, box):
    x, y, width, height = normalize_box(box)
    image_height, image_width = depth_shape[:2]
    x0 = clamp(x, 0, image_width)
    y0 = clamp(y, 0, image_height)
    x1 = clamp(x + width, 0, image_width)
    y1 = clamp(y + height, 0, image_height)
    if x1 <= x0 or y1 <= y0:
        return None
    return int(x0), int(y0), int(x1), int(y1)


def measure_depth_in_box(depth_mm, box, min_valid_pixels, max_valid_depth):
    if depth_mm is None:
        return DepthReading(None, 0, 0, None)
    bounds = clip_box_to_depth(depth_mm.shape, box)
    if bounds is None:
        return DepthReading(None, 0, 0, None)
    x0, y0, x1, y1 = bounds
    sample = depth_mm[y0:y1, x0:x1].astype(np.float32, copy=False)
    valid = np.isfinite(sample) & (sample > 0)
    if max_valid_depth > 0:
        valid &= sample <= max_valid_depth
    valid_count = int(np.count_nonzero(valid))
    total_count = int(sample.size)
    if valid_count < min_valid_pixels:
        return DepthReading(None, valid_count, total_count, bounds)
    return DepthReading(float(np.mean(sample[valid])), valid_count, total_count, bounds)


def encode_jpeg(image_bgr, quality):
    ok, encoded = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError("failed to encode JPEG frame")
    return encoded.tobytes()


def make_placeholder_jpeg(width, height, text):
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (20, 32, 30)
    cv2.putText(img, text, (32, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (220, 230, 220), 2, cv2.LINE_AA)
    return encode_jpeg(img, 85)


def parse_args():
    parser = argparse.ArgumentParser(description="Web UI for Azure Kinect depth boxes and four-servo depth targets.")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host")
    parser.add_argument("--http-port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="JSON file for saved boxes and targets")

    kinect = parser.add_argument_group("Azure Kinect")
    kinect.add_argument("--device-id", type=int, default=0, help="Azure Kinect device index")
    kinect.add_argument("--color-resolution", choices=sorted(WEB_COLOR_RESOLUTIONS), default="720p")
    kinect.add_argument("--depth-mode", choices=sorted(DEPTH_MODES), default="nfov_unbinned")
    kinect.add_argument("--fps", choices=sorted(FPS_VALUES, key=int), default="30")
    kinect.add_argument("--timeout-ms", type=int, default=1000)
    kinect.add_argument("--max-depth", type=int, default=4000, help="depth colormap display range in millimeters")
    kinect.add_argument("--aligned-depth", action="store_true", help="show depth transformed into the color camera view")
    kinect.add_argument("--depth-engine-display", default=DEPTH_ENGINE_DISPLAY, help="DISPLAY for the Azure Kinect depth engine")
    kinect.add_argument("--jpeg-quality", type=int, default=82, help="MJPEG JPEG quality, 1-100")

    depth = parser.add_argument_group("Depth Boxes")
    depth.add_argument(
        "--default-box-size",
        "--roi-size",
        dest="default_box_size",
        type=int,
        default=max(DEFAULT_DEPTH_BOX_SIZE, DEPTH_PIXEL_WINDOW),
        help="default square box size used for initial values and old pixel configs",
    )
    depth.add_argument("--min-valid-pixels", type=int, default=MIN_VALID_DEPTH_PIXELS)
    depth.add_argument("--max-valid-depth", type=float, default=0.0, help="ignore depth values above this many mm; 0 disables")

    servo = parser.add_argument_group("Servo Control")
    servo.add_argument("--servo-port", default="/dev/ttyACM0", help="Arduino serial port")
    servo.add_argument("--baud", type=int, default=115200, choices=sorted(BAUD_RATES))
    servo.add_argument("--start-angle", type=float, default=90.0)
    servo.add_argument("--min-angle", type=float, default=10.0)
    servo.add_argument("--max-angle", type=float, default=170.0)
    servo.add_argument("--step-deg", type=float, default=2.0)
    servo.add_argument("--min-step-deg", type=float, default=0.25)
    servo.add_argument("--reverse", action="store_true")
    servo.add_argument("--move-delay", type=float, default=0.03)
    servo.add_argument("--ready-timeout", type=float, default=4.0)
    servo.add_argument("--response-wait", type=float, default=0.03)
    servo.add_argument("--response-idle", type=float, default=0.004)
    servo.add_argument("--dry-run-servo", action="store_true")
    servo.add_argument("--tolerance-mm", type=float, default=1.0)
    servo.add_argument("--max-invalid", type=int, default=10)
    servo.add_argument("--no-auto-reverse", dest="auto_reverse", action="store_false")
    servo.add_argument("--worse-margin-mm", type=float, default=25.0)
    servo.add_argument("--step-shrink", type=float, default=0.5)
    parser.add_argument("--verbose", action="store_true")
    parser.set_defaults(auto_reverse=True)

    args = parser.parse_args()
    if args.aligned_depth and args.color_resolution == "off":
        parser.error("--aligned-depth requires --color-resolution to be enabled")
    if not 1 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality must be 1-100")
    if args.max_depth <= 0:
        parser.error("--max-depth must be greater than 0")
    if args.default_box_size <= 0:
        parser.error("--default-box-size must be greater than 0")
    if args.min_valid_pixels <= 0:
        parser.error("--min-valid-pixels must be greater than 0")
    if not 0 <= args.min_angle < args.max_angle <= 180:
        parser.error("--min-angle and --max-angle must satisfy 0 <= min < max <= 180")
    if not args.min_angle <= args.start_angle <= args.max_angle:
        parser.error("--start-angle must be inside --min-angle and --max-angle")
    if args.step_deg <= 0:
        parser.error("--step-deg must be greater than 0")
    if args.min_step_deg <= 0 or args.min_step_deg > args.step_deg:
        parser.error("--min-step-deg must be greater than 0 and no larger than --step-deg")
    if args.move_delay < 0 or args.response_wait < 0 or args.response_idle < 0:
        parser.error("timing values cannot be negative")
    if args.tolerance_mm <= 0:
        parser.error("--tolerance-mm must be greater than 0")
    if args.max_invalid <= 0:
        parser.error("--max-invalid must be greater than 0")
    if not 0 < args.step_shrink <= 1:
        parser.error("--step-shrink must be in the range (0, 1]")
    return args


def main():
    args = parse_args()
    state = WebState(args.config, args.default_box_size)
    camera = KinectFrameHub(args)
    control = ServoControlRunner(state, camera, args)

    KinectWebHandler.camera = camera
    KinectWebHandler.state = state
    KinectWebHandler.control = control
    KinectWebHandler.args = args
    KinectWebHandler.static_dir = WEB_DIR

    camera.start()
    server = KinectThreadingHTTPServer((args.host, args.http_port), KinectWebHandler)
    print(f"Kinect web control running at http://{args.host}:{args.http_port}/")
    print("Use Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web control...")
    finally:
        control.stop()
        camera.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
