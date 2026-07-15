#!/usr/bin/env python3
import argparse
import json
import math
import mimetypes
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
    CalibrationType,
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
from table_pose import (
    PoseFitAttempt,
    TablePoseTracker,
    configure_table_geometry,
    world_to_cell,
)
from live_capture_viewer import (
    COLOR_RESOLUTIONS,
    DEFAULT_MAX_BRIGHTNESS,
    DEPTH_ENGINE_DISPLAY,
    DEPTH_MODES,
    FPS_VALUES,
    brightness_to_display,
    color_to_bgr,
    depth_to_display,
    get_depth,
    set_display,
)
from servo_write import BAUD_RATES


ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
APP_CONFIG_PATH = ROOT_DIR / "config.json"
DEFAULT_SERVO_CONFIG_PATH = ROOT_DIR / "web_control_config.json"
WEB_COLOR_RESOLUTIONS = {**COLOR_RESOLUTIONS, "off": ColorResolution.OFF}
SERVO_COLORS = {0: "#ffcc4d", 1: "#ff6b6b", 2: "#44d7b6", 3: "#78a6ff"}
DEFAULT_DEPTH_BOX_SIZE = 25


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

def _load_config_file(path: Path) -> dict:
    """
    Load a nested JSON config file, flattening sections into a single dict
    whose keys match argparse dest names.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    flat: dict = {}
    for value in data.values():
        if isinstance(value, dict):
            flat.update(value)
    return flat


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DepthReading:
    depth_mm: float | None
    valid_pixels: int
    total_pixels: int
    bounds: tuple[int, int, int, int] | None


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def clamp(value, low, high):
    return max(low, min(high, value))


def normalize_box(box):
    x, y, w, h = (int(round(float(v))) for v in box)
    if w <= 0 or h <= 0:
        raise ValueError("box width and height must be greater than 0")
    if x < 0 or y < 0:
        raise ValueError("box x and y cannot be negative")
    return x, y, w, h


def box_from_center_pixel(pixel, size):
    x, y = pixel
    size = max(1, int(size))
    half = size // 2
    return normalize_box((max(0, int(x) - half), max(0, int(y) - half), size, size))


def clip_box_to_depth(depth_shape, box):
    x, y, w, h = normalize_box(box)
    ih, iw = depth_shape[:2]
    x0, y0 = clamp(x, 0, iw), clamp(y, 0, ih)
    x1, y1 = clamp(x + w, 0, iw), clamp(y + h, 0, ih)
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


# ---------------------------------------------------------------------------
# WebState — servo box/target persistence and status
# ---------------------------------------------------------------------------

class WebState:
    def __init__(self, servo_config_path, default_box_size=DEFAULT_DEPTH_BOX_SIZE):
        self.config_path = Path(servo_config_path)
        self.default_box_size = int(default_box_size)
        self.lock = threading.RLock()
        self.boxes = {
            ch: box_from_center_pixel(SERVO_DEPTH_PIXELS[ch], self.default_box_size)
            for ch in SERVO_CHANNELS
        }
        self.targets = {ch: 1000.0 for ch in SERVO_CHANNELS}
        self.angles = {ch: None for ch in SERVO_CHANNELS}
        self.reached = {ch: False for ch in SERVO_CHANNELS}
        self.last_control_error = {ch: None for ch in SERVO_CHANNELS}
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
            for ch in SERVO_CHANNELS:
                item = data.get("servos", {}).get(str(ch), {})
                box = item.get("box")
                if isinstance(box, list) and len(box) == 4:
                    try:
                        self.boxes[ch] = normalize_box(box)
                    except (TypeError, ValueError):
                        pass
                else:
                    pixel = item.get("pixel")
                    if isinstance(pixel, list) and len(pixel) == 2:
                        try:
                            self.boxes[ch] = box_from_center_pixel(pixel, self.default_box_size)
                        except (TypeError, ValueError):
                            pass
                target = item.get("target_depth_mm")
                if isinstance(target, (int, float)) and target > 0:
                    self.targets[ch] = float(target)

    def save(self):
        with self.lock:
            data = {
                "servos": {
                    str(ch): {"box": list(self.boxes[ch]), "target_depth_mm": self.targets[ch]}
                    for ch in SERVO_CHANNELS
                }
            }
        tmp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.config_path)

    def set_box(self, channel, box):
        with self.lock:
            self.boxes[channel] = normalize_box(box)
        self.save()

    def set_target(self, channel, target_depth_mm):
        with self.lock:
            self.targets[channel] = float(target_depth_mm)
        self.save()

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

    def snapshot_config(self):
        with self.lock:
            return {
                ch: {"box": self.boxes[ch], "target_depth_mm": self.targets[ch]}
                for ch in SERVO_CHANNELS
            }

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
        for ch in SERVO_CHANNELS:
            box = config[ch]["box"]
            target = config[ch]["target_depth_mm"]
            reading = measure_depth_in_box(depth_mm, box, args.min_valid_pixels, args.max_valid_depth)
            servos.append({
                "channel": ch,
                "color": SERVO_COLORS[ch],
                "box": {"x": box[0], "y": box[1], "width": box[2], "height": box[3]},
                "target_depth_mm": target,
                "current_depth_mm": reading.depth_mm,
                "current_error_mm": None if reading.depth_mm is None else reading.depth_mm - target,
                "valid_pixels": reading.valid_pixels,
                "total_pixels": reading.total_pixels,
                "sample_bounds": reading.bounds,
                "angle_deg": angles[ch],
                "reached": reached[ch],
                "control_error_mm": control_errors[ch],
            })

        w = depth_shape[1] if depth_shape else None
        h = depth_shape[0] if depth_shape else None
        return {
            "servos": servos,
            "servo_channels": list(SERVO_CHANNELS),
            "depth_image": {"width": w, "height": h},
            "ball": camera.get_ball_state(),
            "camera": camera.status_snapshot(),
            "table_pose": camera.pose_state_json(),
            "control": {"running": control_running, "message": control_message, "error": control_error},
            "settings": {
                "default_box_size": args.default_box_size,
                "min_valid_pixels": args.min_valid_pixels,
                "max_valid_depth": args.max_valid_depth,
                "tolerance_mm": args.tolerance_mm,
            },
        }


# ---------------------------------------------------------------------------
# KinectFrameHub — camera capture loop, frame distribution, ball tracking
# ---------------------------------------------------------------------------

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

        self.detector = None
        self.tracker = None
        self.ball_position = None
        self.ball_detection = None
        self.ir_jpeg = None
        self.placeholder_ir = make_placeholder_jpeg(640, 576, "Waiting for Kinect IR")
        self.tracker_jpeg = None
        self.placeholder_tracker = make_placeholder_jpeg(640, 576, "Ball tracking not enabled")
        self.max_ir_brightness = args.max_ir_brightness

        self._last_ir_frame = None
        self._last_depth_for_tracker = None
        self._intrinsics = None   # (fx, fy, ppx, ppy) — set once camera starts
        self._last_ball_r_px = None  # last known ball radius in pixels for miss-frame overlay

        self.table_pose = TablePoseTracker()
        self._last_pose_attempt = None
        self._pose_debug_jpeg = None
        self._pose_frame_counter = 0
        self.placeholder_pose = make_placeholder_jpeg(640, 576, "No pose attempt yet")
        self.marker_ir_min_counts = args.marker_ir_min_counts

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
        if self.tracker is not None:
            self.tracker.close()

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

            mat = self.k4a.calibration.get_camera_matrix(CalibrationType.DEPTH)
            with self.lock:
                self._intrinsics = (
                    float(mat[0, 0]), float(mat[1, 1]),
                    float(mat[0, 2]), float(mat[1, 2]),
                )

            if args.ball_tracking:
                try:
                    from ball_tracker import BallDetector, BallTracker
                    det = BallDetector.from_k4a_calibration(
                        self.k4a.calibration,
                        ball_radius_min_mm=args.ball_radius_min,
                        ball_radius_max_mm=args.ball_radius_max,
                    )
                    trk = BallTracker.from_k4a_calibration(self.k4a.calibration)
                    with self.lock:
                        self.detector = det
                        self.tracker = trk
                    print("Ball tracker ready.")
                except Exception as exc:
                    print(f"Ball tracker disabled: {exc}", file=sys.stderr)

            while not self.stop_event.is_set():
                try:
                    capture = self.k4a.get_capture(timeout=args.timeout_ms)
                except K4ATimeoutException:
                    self._set_status("timeout", "Timed out waiting for a Kinect frame.")
                    continue

                depth_mm = get_depth(capture, args.aligned_depth)
                if depth_mm is None:
                    continue

                ir_frame = capture.ir
                depth_for_tracker = capture.depth

                ir_bgr = None
                if ir_frame is not None:
                    ir_bgr = brightness_to_display(ir_frame, self.max_ir_brightness)

                color_jpeg = self.placeholder_color
                if capture.color is not None:
                    bgr = color_to_bgr(capture.color)
                    if bgr is not None:
                        color_jpeg = encode_jpeg(bgr, args.jpeg_quality)

                depth_jpeg = encode_jpeg(depth_to_display(depth_mm, args.max_depth), args.jpeg_quality)

                now = time.monotonic()
                self._frame_count += 1
                elapsed = now - self._fps_started_at
                if elapsed >= 1.0:
                    self.fps = self._frame_count / elapsed
                    self._frame_count = 0
                    self._fps_started_at = now

                with self.lock:
                    detector, tracker = self.detector, self.tracker

                tracker_jpeg = None
                pos, smoothed = None, None
                if detector is not None and ir_frame is not None:
                    detection = detector.detect(ir_frame, depth_for_tracker)
                    dbg = detector.debug_frame
                    if dbg is not None:
                        tracker_jpeg = encode_jpeg(dbg, args.jpeg_quality)
                    pos, smoothed = tracker.update(detection) if tracker is not None else (None, None)

                # Draw ball circle directly onto the IR image so it updates at
                # camera frame rate via the MJPEG stream (not the 350 ms state poll).
                if ir_bgr is not None and pos is not None:
                    self._draw_ball_overlay(ir_bgr, smoothed, pos)

                ir_jpeg = encode_jpeg(ir_bgr, args.jpeg_quality) if ir_bgr is not None else self.placeholder_ir

                # Refresh the table's pose every few frames rather than every
                # single one — the fit is cheap but there's no need to redo it
                # at full camera frame rate.
                pose_jpeg = None
                self._pose_frame_counter += 1
                if (
                    ir_frame is not None
                    and self._intrinsics is not None
                    and self._pose_frame_counter >= args.pose_update_every_n_frames
                ):
                    self._pose_frame_counter = 0
                    fx, fy, ppx, ppy = self._intrinsics
                    pose_attempt = self.table_pose.update(
                        ir_frame, depth_for_tracker, fx, fy, ppx, ppy,
                        marker_ir_min_counts=self.marker_ir_min_counts,
                    )
                    if pose_attempt.debug_frame is not None:
                        pose_jpeg = encode_jpeg(pose_attempt.debug_frame, args.jpeg_quality)
                    with self.lock:
                        self._last_pose_attempt = pose_attempt

                with self.lock:
                    self.ball_position = pos
                    if smoothed is not None:
                        self.ball_detection = smoothed
                        self._last_ball_r_px = smoothed.radius_px
                    elif pos is None:
                        self.ball_detection = None

                with self.lock:
                    self.seq += 1
                    self.color_jpeg = color_jpeg
                    self.depth_jpeg = depth_jpeg
                    self.ir_jpeg = ir_jpeg
                    if tracker_jpeg is not None:
                        self.tracker_jpeg = tracker_jpeg
                    if pose_jpeg is not None:
                        self._pose_debug_jpeg = pose_jpeg
                    self.depth_mm = depth_mm.copy()
                    self.depth_shape = depth_mm.shape[:2]
                    self._last_ir_frame = ir_frame
                    self._last_depth_for_tracker = depth_for_tracker
                    self.status = "running"
                    self.error = ""
                    self.lock.notify_all()

        except (K4AException, RuntimeError, ValueError, cv2.error) as exc:
            self._set_status("error", str(exc))
        finally:
            if self.k4a is not None and self.k4a.is_running:
                self.k4a.stop()

    def _draw_ball_overlay(self, ir_bgr, smoothed, pos):
        """Draw ball circle onto IR image in-place."""
        if smoothed is not None:
            cx = int(round(smoothed.cx))
            cy = int(round(smoothed.cy))
            r  = max(1, int(round(smoothed.radius_px)))
        else:
            # Project EKF position when the detector missed this frame.
            with self.lock:
                intrinsics = self._intrinsics
                last_r = self._last_ball_r_px
            if intrinsics is None or last_r is None:
                return
            fx, fy, ppx, ppy = intrinsics
            X, Y, Z = pos
            if Z <= 1.0:
                return
            cx = int(round(fx * X / Z + ppx))
            cy = int(round(fy * Y / Z + ppy))
            r  = max(1, int(round(last_r)))
        cv2.circle(ir_bgr, (cx, cy), r,  (55, 55, 255), 2)
        cv2.circle(ir_bgr, (cx, cy), 4,  (55, 55, 255), -1)

    # ------------------------------------------------------------------
    # Frame access
    # ------------------------------------------------------------------

    def wait_for_jpeg(self, kind, last_seq, timeout=1.0):
        with self.lock:
            if self.seq == last_seq:
                self.lock.wait(timeout=timeout)
            seq = self.seq
            jpeg = {
                "color": self.color_jpeg or self.placeholder_color,
                "ir": self.ir_jpeg or self.placeholder_ir,
                "tracker": self.tracker_jpeg or self.placeholder_tracker,
                "pose": self._pose_debug_jpeg or self.placeholder_pose,
            }.get(kind, self.depth_jpeg or self.placeholder_depth)
        return seq, jpeg

    def get_depth_snapshot(self):
        with self.lock:
            return None if self.depth_mm is None else self.depth_mm.copy()

    def get_depth_shape(self):
        with self.lock:
            return self.depth_shape

    def status_snapshot(self):
        with self.lock:
            return {"status": self.status, "error": self.error, "fps": self.fps, "frame_seq": self.seq}

    def set_ir_brightness(self, value: int) -> None:
        with self.lock:
            self.max_ir_brightness = max(1, int(value))

    def set_marker_ir_min_counts(self, value: float) -> None:
        with self.lock:
            self.marker_ir_min_counts = max(0.0, float(value))

    def get_ball_state(self):
        with self.lock:
            if self.detector is None:
                return {"enabled": False, "ir_brightness": self.max_ir_brightness}
            pos = self.ball_position
            det = self.ball_detection
            ir_brightness = self.max_ir_brightness
            reject_counts = dict(self.detector.last_reject_counts)

        if pos is None:
            return {
                "enabled": True, "detected": False, "position": None, "position_world": None,
                "table_tracking": self.table_pose.is_tracking, "pose_stale": None, "pose_age_s": None,
                "cell": None,
                "pixel": None, "radius_mm": None,
                "ir_brightness": ir_brightness, "reject_counts": reject_counts,
            }

        world, stale, age_s = self.table_pose.apply(pos)
        position_world = None
        cell = None
        if world is not None:
            wx, wy, wz = world
            position_world = {"x": round(wx, 1), "y": round(wy, 1), "z": round(wz, 1)}
            row, col = world_to_cell(wx, wy)
            cell = {"row": row, "col": col}

        return {
            "enabled": True,
            "detected": True,
            "position": {"x": round(pos[0], 1), "y": round(pos[1], 1), "z": round(pos[2], 1)},
            "position_world": position_world,
            "table_tracking": self.table_pose.is_tracking,
            "pose_stale": stale,
            "pose_age_s": round(age_s, 1) if age_s is not None else None,
            "cell": cell,
            "pixel": {"cx": round(det.cx), "cy": round(det.cy), "radius": round(det.radius_px)} if det else None,
            "radius_mm": round(det.radius_mm, 1) if det else None,
            "ir_brightness": ir_brightness,
            "reject_counts": reject_counts,
        }

    # ------------------------------------------------------------------
    # Table pose tracking
    # ------------------------------------------------------------------

    def pose_state_json(self):
        with self.lock:
            tracker = self.table_pose
            marker_ir_min_counts = self.marker_ir_min_counts
            last_attempt = self._last_pose_attempt
        _, stale, age_s = tracker.apply((0.0, 0.0, 0.0))
        result = {
            "tracking": tracker.is_tracking,
            "stale": stale if tracker.is_tracking else None,
            "age_s": round(age_s, 1) if age_s is not None else None,
            "rms_residual_mm": tracker.last_fit.rms_residual_mm if tracker.last_fit else None,
            "max_residual_mm": tracker.last_fit.max_residual_mm if tracker.last_fit else None,
            "last_error": tracker.last_error,
            "marker_ir_min_counts": marker_ir_min_counts,
        }
        if last_attempt is not None and last_attempt.diagnostics is not None:
            result["diagnostics"] = {
                "ir_max": last_attempt.diagnostics.ir_max,
                "threshold_counts": [
                    {"threshold": t, "count": c}
                    for t, c in sorted(last_attempt.diagnostics.threshold_counts.items())
                ],
            }
        if last_attempt is not None and last_attempt.ok and last_attempt.matched_points is not None:
            result["matched_points"] = {
                name: {"residual_mm": info["residual_mm"]}
                for name, info in last_attempt.matched_points.items()
            }
        return result


# ---------------------------------------------------------------------------
# ServoControlRunner
# ---------------------------------------------------------------------------

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
        angles = {ch: args.start_angle for ch in SERVO_CHANNELS}
        steps = {ch: args.step_deg for ch in SERVO_CHANNELS}
        directions = {ch: -1.0 if args.reverse else 1.0 for ch in SERVO_CHANNELS}
        last_abs_errors = {ch: None for ch in SERVO_CHANNELS}
        invalid_streaks = {ch: 0 for ch in SERVO_CHANNELS}

        try:
            self.state.set_control(True, "opening serial")
            controller.open()
            for ch in SERVO_CHANNELS:
                controller.write_angle(ch, angles[ch])
                self.state.set_angle(ch, angles[ch])
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

                for ch in SERVO_CHANNELS:
                    box = configs[ch]["box"]
                    target = configs[ch]["target_depth_mm"]
                    reading = measure_depth_in_box(depth_mm, box, args.min_valid_pixels, args.max_valid_depth)

                    if reading.depth_mm is None:
                        invalid_streaks[ch] += 1
                        self.state.set_channel_status(ch, reached=False)
                        if invalid_streaks[ch] >= args.max_invalid:
                            self.state.set_control(True, f"channel {ch} has invalid depth; waiting")
                        continue

                    invalid_streaks[ch] = 0
                    error = reading.depth_mm - target
                    abs_error = abs(error)
                    self.state.set_channel_status(ch, reached=abs_error <= args.tolerance_mm, error=error)

                    if abs_error <= args.tolerance_mm:
                        reached_count += 1
                        continue

                    if (
                        args.auto_reverse
                        and last_abs_errors[ch] is not None
                        and abs_error > last_abs_errors[ch] + args.worse_margin_mm
                    ):
                        directions[ch] *= -1.0
                        steps[ch] = max(args.min_step_deg, steps[ch] * args.step_shrink)

                    delta = directions[ch] * math.copysign(steps[ch], error)
                    next_angle = clamp(angles[ch] + delta, args.min_angle, args.max_angle)
                    if not math.isclose(next_angle, angles[ch], abs_tol=1e-9):
                        moves.append((ch, next_angle, abs_error))

                for ch, next_angle, abs_error in moves:
                    controller.write_angle(ch, next_angle)
                    angles[ch] = next_angle
                    last_abs_errors[ch] = abs_error
                    self.state.set_angle(ch, next_angle)

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


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class KinectWebHandler(BaseHTTPRequestHandler):
    camera: "KinectFrameHub" = None
    state: "WebState" = None
    control: "ServoControlRunner" = None
    args = None
    static_dir: Path = WEB_DIR

    def log_message(self, fmt, *args):
        if self.args and self.args.verbose:
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path

        static = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
        if path in static:
            return self._serve_static(static[path])

        streams = {
            "/stream/color.mjpg": "color",
            "/stream/depth.mjpg": "depth",
            "/stream/ir.mjpg": "ir",
            "/stream/tracker.mjpg": "tracker",
            "/stream/pose.mjpg": "pose",
        }
        if path in streams:
            return self._serve_mjpeg(streams[path])

        api = {
            "/api/state": lambda: self._send_json(self.state.to_json(self.camera, self.args)),
            "/api/pose/state": lambda: self._send_json(self.camera.pose_state_json()),
        }
        if path in api:
            return api[path]()

        self.send_error(404, "not found")

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            parts = path.strip("/").split("/")

            # /api/servos/<channel>/<action>
            if len(parts) == 4 and parts[:2] == ["api", "servos"]:
                channel = self._parse_channel(parts[2])
                handler = {
                    "box":    self._post_servo_box,
                    "pixel":  self._post_servo_pixel,
                    "target": self._post_servo_target,
                }.get(parts[3])
                if handler:
                    return handler(channel, data)

            handler = {
                "/api/ir/brightness":         self._post_ir_brightness,
                "/api/control/start":         self._post_control_start,
                "/api/control/stop":          self._post_control_stop,
                "/api/pose/threshold":        self._post_pose_threshold,
            }.get(path)
            if handler:
                return handler(data)

        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return

        self.send_error(404, "not found")

    # ------------------------------------------------------------------
    # POST handlers
    # ------------------------------------------------------------------

    def _post_servo_box(self, channel, data):
        box = normalize_box((int(data["x"]), int(data["y"]), int(data["width"]), int(data["height"])))
        self._validate_box(box)
        self.state.set_box(channel, box)
        self._send_json({"ok": True})

    def _post_servo_pixel(self, channel, data):
        box = box_from_center_pixel((int(data["x"]), int(data["y"])), self.args.default_box_size)
        self._validate_box(box)
        self.state.set_box(channel, box)
        self._send_json({"ok": True})

    def _post_servo_target(self, channel, data):
        target = float(data["target_depth_mm"])
        if target <= 0:
            raise ValueError("target_depth_mm must be greater than 0")
        self.state.set_target(channel, target)
        self._send_json({"ok": True})

    def _post_ir_brightness(self, data):
        self.camera.set_ir_brightness(int(data["value"]))
        self._send_json({"ok": True, "ir_brightness": self.camera.max_ir_brightness})

    def _post_control_start(self, data):
        self._send_json({"ok": True, "started": self.control.start()})

    def _post_control_stop(self, data):
        self.control.stop()
        self._send_json({"ok": True})

    def _post_pose_threshold(self, data):
        self.camera.set_marker_ir_min_counts(float(data["value"]))
        self._send_json({"ok": True, "marker_ir_min_counts": self.camera.marker_ir_min_counts})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _serve_static(self, name):
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

    def _serve_mjpeg(self, kind):
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

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _parse_channel(self, text):
        channel = int(text)
        if channel not in SERVO_CHANNELS:
            raise ValueError(f"channel must be one of {sorted(SERVO_CHANNELS)}")
        return channel

    def _validate_box(self, box):
        x, y, w, h = box  # already normalized
        if w * h < self.args.min_valid_pixels:
            raise ValueError(f"box area must be at least {self.args.min_valid_pixels} pixels")
        shape = self.camera.get_depth_shape()
        if shape is None:
            return
        ih, iw = shape
        if x + w > iw or y + h > ih:
            raise ValueError(f"box ({x}, {y}, {w}, {h}) is outside depth image {iw}x{ih}")


class KinectThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------------------
# Argument parsing — loads config.json as defaults, CLI overrides on top
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    # Stage 1: find --config before full parse so we can use the file as defaults.
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--config", default=str(APP_CONFIG_PATH))
    _pre_ns, _ = _pre.parse_known_args(argv)

    file_cfg: dict = {}
    cfg_path = Path(_pre_ns.config)
    if cfg_path.exists():
        try:
            file_cfg = _load_config_file(cfg_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"Warning: could not load config {cfg_path}: {exc}", file=sys.stderr)

    parser = argparse.ArgumentParser(
        description="Web UI for Azure Kinect depth boxes and four-servo depth control.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", default=str(APP_CONFIG_PATH),
                        help="JSON settings file (default: config.json in project root)")
    parser.add_argument("--verbose", action="store_true", default=False)

    srv = parser.add_argument_group("Server")
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--http-port", type=int, default=8080)

    kinect = parser.add_argument_group("Azure Kinect")
    kinect.add_argument("--device-id", type=int, default=0)
    kinect.add_argument("--color-resolution", choices=sorted(WEB_COLOR_RESOLUTIONS), default="720p")
    kinect.add_argument("--depth-mode", choices=sorted(DEPTH_MODES), default="nfov_unbinned")
    kinect.add_argument("--fps", choices=sorted(FPS_VALUES, key=int), default="30")
    kinect.add_argument("--timeout-ms", type=int, default=1000)
    kinect.add_argument("--max-depth", type=int, default=4000)
    kinect.add_argument("--aligned-depth", action="store_true", default=False)
    kinect.add_argument("--depth-engine-display", default=DEPTH_ENGINE_DISPLAY)
    kinect.add_argument("--jpeg-quality", type=int, default=82)

    depth = parser.add_argument_group("Depth Boxes")
    depth.add_argument("--servo-config", default=str(DEFAULT_SERVO_CONFIG_PATH),
                       help="JSON file for saved servo box positions and depth targets")
    depth.add_argument("--default-box-size", type=int,
                       default=max(DEFAULT_DEPTH_BOX_SIZE, DEPTH_PIXEL_WINDOW))
    depth.add_argument("--min-valid-pixels", type=int, default=MIN_VALID_DEPTH_PIXELS)
    depth.add_argument("--max-valid-depth", type=float, default=0.0,
                       help="ignore depth above this many mm; 0 disables")

    servo = parser.add_argument_group("Servo Control")
    servo.add_argument("--servo-port", default="/dev/ttyACM0")
    servo.add_argument("--baud", type=int, default=115200, choices=sorted(BAUD_RATES))
    servo.add_argument("--start-angle", type=float, default=90.0)
    servo.add_argument("--min-angle", type=float, default=10.0)
    servo.add_argument("--max-angle", type=float, default=170.0)
    servo.add_argument("--step-deg", type=float, default=2.0)
    servo.add_argument("--min-step-deg", type=float, default=0.25)
    servo.add_argument("--reverse", action="store_true", default=False)
    servo.add_argument("--move-delay", type=float, default=0.03)
    servo.add_argument("--ready-timeout", type=float, default=4.0)
    servo.add_argument("--response-wait", type=float, default=0.03)
    servo.add_argument("--response-idle", type=float, default=0.004)
    servo.add_argument("--dry-run-servo", action="store_true", default=False)
    servo.add_argument("--tolerance-mm", type=float, default=1.0)
    servo.add_argument("--max-invalid", type=int, default=10)
    servo.add_argument("--no-auto-reverse", dest="auto_reverse", action="store_false")
    servo.add_argument("--worse-margin-mm", type=float, default=25.0)
    servo.add_argument("--step-shrink", type=float, default=0.5)

    ball = parser.add_argument_group("Ball Tracking")
    ball.add_argument("--ball-tracking", action="store_true", default=False)
    ball.add_argument("--ball-radius-min", type=float, default=20.0, metavar="MM")
    ball.add_argument("--ball-radius-max", type=float, default=40.0, metavar="MM")
    ball.add_argument("--max-ir-brightness", type=int, default=DEFAULT_MAX_BRIGHTNESS, metavar="DN")

    calib = parser.add_argument_group("Table Pose Tracking")
    calib.add_argument("--pose-update-every-n-frames", type=int, default=3, metavar="N",
                       help="recompute the camera-to-table pose every N camera frames")
    calib.add_argument("--marker-ir-min-counts", type=float, default=3800.0, metavar="COUNTS")
    calib.add_argument("--marker-height-mm", type=float, default=50.8, metavar="MM",
                       help="height of the wall-mounted markers above the table surface")
    calib.add_argument("--marker-mount-radius-mm", type=float, default=12.7, metavar="MM",
                       help="physical marker disc radius; offsets each marker's mounted "
                            "position off the nominal wall line by this much")
    calib.add_argument("--wall-thickness-mm", type=float, default=4.7625, metavar="MM",
                       help="thickness of the foam wall the markers are mounted on; "
                            "adds to --marker-mount-radius-mm for the total mounting offset")
    calib.add_argument("--max-marker-radius-mm", type=float, default=15.0, metavar="MM",
                       help="reject IR blobs larger than this physical radius (excludes the ball)")

    # Config file values override argparse defaults; CLI args override everything.
    parser.set_defaults(auto_reverse=True)
    parser.set_defaults(**file_cfg)

    args = parser.parse_args(argv)

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
    if args.pose_update_every_n_frames <= 0:
        parser.error("--pose-update-every-n-frames must be greater than 0")
    if args.marker_ir_min_counts < 0:
        parser.error("--marker-ir-min-counts cannot be negative")
    if args.marker_height_mm < 0:
        parser.error("--marker-height-mm cannot be negative")
    if args.marker_mount_radius_mm < 0:
        parser.error("--marker-mount-radius-mm cannot be negative")
    if args.wall_thickness_mm < 0:
        parser.error("--wall-thickness-mm cannot be negative")
    if args.max_marker_radius_mm <= 0:
        parser.error("--max-marker-radius-mm must be greater than 0")

    return args


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    configure_table_geometry(
        marker_height_mm=args.marker_height_mm,
        marker_mount_radius_mm=args.marker_mount_radius_mm,
        wall_thickness_mm=args.wall_thickness_mm,
        max_marker_radius_mm=args.max_marker_radius_mm,
    )
    state = WebState(args.servo_config, args.default_box_size)
    camera = KinectFrameHub(args)
    control = ServoControlRunner(state, camera, args)

    KinectWebHandler.camera = camera
    KinectWebHandler.state = state
    KinectWebHandler.control = control
    KinectWebHandler.args = args

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
