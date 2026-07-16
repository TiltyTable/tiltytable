#!/usr/bin/env python3
import argparse
import json
import mimetypes
import sys
import threading
import time
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

from table_pose import (
    GravityEstimator,
    PoseFitAttempt,
    TableGeometry,
    TablePoseTracker,
    configure_table_geometry,
    world_to_cell,
)
from live_capture_viewer import (
    COLOR_RESOLUTIONS,
    DEPTH_ENGINE_DISPLAY,
    DEPTH_MODES,
    FPS_VALUES,
    brightness_to_display,
    color_to_bgr,
    depth_to_display,
    get_depth,
    set_display,
)
ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
APP_CONFIG_PATH = ROOT_DIR / "config.json"
WEB_COLOR_RESOLUTIONS = {**COLOR_RESOLUTIONS, "off": ColorResolution.OFF}


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
# KinectFrameHub — camera capture loop, frame distribution, ball tracking
# ---------------------------------------------------------------------------

class KinectFrameHub:
    def __init__(self, args, *, headless=False):
        self.args = args
        self.headless = headless
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
        self.placeholder_color = (
            None if headless else make_placeholder_jpeg(960, 540, "Waiting for Kinect color")
        )
        self.placeholder_depth = (
            None if headless else make_placeholder_jpeg(640, 576, "Waiting for Kinect depth")
        )

        self.detector = None
        self.tracker = None
        self.ball_position = None
        self.ball_detection = None
        self.ir_jpeg = None
        self.placeholder_ir = (
            None if headless else make_placeholder_jpeg(640, 576, "Waiting for Kinect IR")
        )
        self.tracker_jpeg = None
        self.placeholder_tracker = (
            None if headless else make_placeholder_jpeg(640, 576, "Ball tracking not enabled")
        )
        self._last_ir_frame = None
        self._last_depth_for_tracker = None
        self._intrinsics = None   # (fx, fy, ppx, ppy) — set once camera starts
        self._last_ball_r_px = None  # last known ball radius in pixels for miss-frame overlay

        self.table_pose = TablePoseTracker()
        self._last_pose_attempt = None
        self._pose_debug_jpeg = None
        self._pose_frame_counter = 0
        self.placeholder_pose = (
            None if headless else make_placeholder_jpeg(640, 576, "No pose attempt yet")
        )
        self.marker_ir_threshold = args.marker_ir_threshold
        self.ball_ir_threshold = args.ball_ir_threshold

        self.gravity = GravityEstimator(sign=args.gravity_sign)
        self._imu_thread = None
        self._accel_to_depth_R = None  # set once calibration is available in _run()

    def start(self):
        self.thread = threading.Thread(target=self._run, name="kinect-capture", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.lock.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self._imu_thread is not None:
            self._imu_thread.join(timeout=2.0)
        if self.k4a is not None and self.k4a.is_running:
            self.k4a.stop()
        if self.tracker is not None:
            self.tracker.close()

    # Azure Kinect's IMU streams at ~1.6kHz, far faster than the heavily
    # smoothed gravity estimate (EMA factor 0.02, tracking something that
    # shouldn't move at all -- a stationary tripod's "up" direction) needs.
    # PyK4A defaults to thread_safe=True, which wraps *every* native device
    # call -- get_capture() and get_imu_sample() alike -- in a shared lock,
    # so draining every IMU sample as fast as they arrive would hammer that
    # lock and starve the main capture loop's get_capture() calls, slowing
    # ball tracking / table tracking / streaming (all downstream of that
    # same loop). Throttling to a much lower poll rate avoids that.
    _IMU_POLL_INTERVAL_S = 1.0 / 30.0

    def _run_imu(self):
        """Periodically samples the IMU into self.gravity so the camera's
        accelerometer-derived "up" direction stays fresh — decoupled from
        the depth-camera capture loop's cadence."""
        while not self.stop_event.is_set():
            try:
                sample = self.k4a.get_imu_sample(timeout=100)
            except K4ATimeoutException:
                continue
            except K4AException:
                break
            if sample is not None and self._accel_to_depth_R is not None:
                acc_depth_frame = self._accel_to_depth_R @ np.array(sample["acc_sample"])
                self.gravity.add_sample(acc_depth_frame)
            time.sleep(self._IMU_POLL_INTERVAL_S)

    def _set_status(self, status, error=""):
        with self.lock:
            self.status = status
            self.error = error
            self.lock.notify_all()

    def _run(self):
        args = self.args
        if args.depth_engine_display and not self.headless:
            set_display(args.depth_engine_display, "depth engine", quiet=not args.verbose)

        try:
            device_count = connected_device_count()
            if device_count <= args.device_id:
                self._set_status(
                    "error",
                    f"No Azure Kinect device at index {args.device_id}; found {device_count} device(s).",
                )
                return

            color_resolution = "off" if self.headless else args.color_resolution
            config = Config(
                color_resolution=WEB_COLOR_RESOLUTIONS[color_resolution],
                color_format=ImageFormat.COLOR_BGRA32,
                depth_mode=DEPTH_MODES[args.depth_mode],
                camera_fps=FPS_VALUES[args.fps],
                synchronized_images_only=(
                    not self.headless
                    and (color_resolution != "off" or args.aligned_depth)
                ),
            )
            self.k4a = PyK4A(config=config, device_id=args.device_id)
            self.k4a.start()
            self._set_status("running")

            if not self.headless:
                # The calibration UI displays table tilt from the IMU. The
                # arcade only needs camera-to-table pose and skips this stream.
                self._accel_to_depth_R, _ = self.k4a.calibration.get_extrinsic_parameters(
                    CalibrationType.ACCEL, CalibrationType.DEPTH,
                )
                self._imu_thread = threading.Thread(
                    target=self._run_imu,
                    name="kinect-imu",
                    daemon=True,
                )
                self._imu_thread.start()

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
                        ball_ir_threshold=self.ball_ir_threshold,
                        debug=not self.headless,
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

                depth_mm = (
                    capture.depth
                    if self.headless
                    else get_depth(capture, args.aligned_depth)
                )
                if depth_mm is None:
                    continue

                ir_frame = capture.ir
                depth_for_tracker = capture.depth

                ir_bgr = None
                if ir_frame is not None and not self.headless:
                    ir_bgr = brightness_to_display(ir_frame)

                color_jpeg = None if self.headless else self.placeholder_color
                if not self.headless and capture.color is not None:
                    bgr = color_to_bgr(capture.color)
                    if bgr is not None:
                        color_jpeg = encode_jpeg(bgr, args.jpeg_quality)

                depth_jpeg = None
                if not self.headless:
                    depth_jpeg = encode_jpeg(
                        depth_to_display(depth_mm, args.max_depth),
                        args.jpeg_quality,
                    )

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
                if (
                    detector is not None
                    and ir_frame is not None
                    and depth_for_tracker is not None
                ):
                    detection = detector.detect(ir_frame, depth_for_tracker)
                    dbg = detector.debug_frame
                    if dbg is not None and not self.headless:
                        tracker_jpeg = encode_jpeg(dbg, args.jpeg_quality)
                    pos, smoothed = tracker.update(detection) if tracker is not None else (None, None)

                # Draw ball circle directly onto the IR image so it updates at
                # camera frame rate via the MJPEG stream (not the 350 ms state poll).
                if not self.headless and ir_bgr is not None and pos is not None:
                    self._draw_ball_overlay(ir_bgr, smoothed, pos)

                ir_jpeg = None
                if not self.headless:
                    ir_jpeg = (
                        encode_jpeg(ir_bgr, args.jpeg_quality)
                        if ir_bgr is not None
                        else self.placeholder_ir
                    )

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
                        marker_ir_threshold=self.marker_ir_threshold,
                    )
                    if pose_attempt.debug_frame is not None and not self.headless:
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
                    if not self.headless:
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


    def set_marker_ir_threshold(self, value: float) -> None:
        with self.lock:
            self.marker_ir_threshold = max(0, int(value))

    def set_ball_ir_threshold(self, value: int) -> None:
        with self.lock:
            self.ball_ir_threshold = max(0, int(value))
            if self.detector is not None:
                self.detector.ball_ir_threshold = self.ball_ir_threshold

    def get_ball_state(self):
        with self.lock:
            if self.detector is None:
                return {"enabled": False}
            pos = self.ball_position
            det = self.ball_detection
            ball_ir_threshold = self.ball_ir_threshold
            reject_counts = dict(self.detector.last_reject_counts)

        if pos is None:
            return {
                "enabled": True, "detected": False, "position": None, "position_world": None,
                "table_tracking": self.table_pose.is_tracking, "pose_stale": None, "pose_age_s": None,
                "cell": None,
                "pixel": None, "radius_mm": None,
                "ball_ir_threshold": ball_ir_threshold,
                "reject_counts": reject_counts,
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
            "ball_ir_threshold": ball_ir_threshold,
            "reject_counts": reject_counts,
        }

    # ------------------------------------------------------------------
    # Table pose tracking
    # ------------------------------------------------------------------

    def pose_state_json(self):
        with self.lock:
            tracker = self.table_pose
            marker_ir_threshold = self.marker_ir_threshold
            last_attempt = self._last_pose_attempt
        _, stale, age_s = tracker.apply((0.0, 0.0, 0.0))
        tilt_deg = tracker.tilt_deg(self.gravity.up_vector)
        roll_pitch = tracker.roll_pitch_deg(self.gravity.up_vector)
        result = {
            "tracking": tracker.is_tracking,
            "stale": stale if tracker.is_tracking else None,
            "age_s": round(age_s, 1) if age_s is not None else None,
            "rms_residual_mm": tracker.last_fit.rms_residual_mm if tracker.last_fit else None,
            "max_residual_mm": tracker.last_fit.max_residual_mm if tracker.last_fit else None,
            "last_error": tracker.last_error,
            "marker_ir_threshold": marker_ir_threshold,
            "tilt_deg": round(tilt_deg, 1) if tilt_deg is not None else None,
            "roll_deg": round(roll_pitch[0], 1) if roll_pitch is not None else None,
            "pitch_deg": round(roll_pitch[1], 1) if roll_pitch is not None else None,
        }
        if last_attempt is not None and last_attempt.ok and last_attempt.matched_points is not None:
            result["matched_points"] = {
                name: {"residual_mm": info["residual_mm"]}
                for name, info in last_attempt.matched_points.items()
            }
        return result


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class KinectWebHandler(BaseHTTPRequestHandler):
    camera: "KinectFrameHub" = None
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
            "/api/state": lambda: self._send_json({
                "ball": self.camera.get_ball_state(),
                "camera": self.camera.status_snapshot(),
                "table_pose": self.camera.pose_state_json(),
            }),
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

            handler = {
                "/api/pose/threshold":        self._post_pose_threshold,
                "/api/ball/threshold":        self._post_ball_threshold,
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

    def _post_pose_threshold(self, data):
        self.camera.set_marker_ir_threshold(int(data["value"]))
        self._send_json({"ok": True, "marker_ir_threshold": self.camera.marker_ir_threshold})

    def _post_ball_threshold(self, data):
        self.camera.set_ball_ir_threshold(int(data["value"]))
        self._send_json({"ok": True, "ball_ir_threshold": self.camera.ball_ir_threshold})

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
        description="Web UI for Azure Kinect ball and table tracking.",
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

    ball = parser.add_argument_group("Ball Tracking")
    ball.add_argument("--ball-tracking", action="store_true", default=False)
    ball.add_argument("--ball-radius-min", type=float, default=20.0, metavar="MM")
    ball.add_argument("--ball-radius-max", type=float, default=40.0, metavar="MM")
    ball.add_argument("--ball-ir-threshold", type=int, default=3000, metavar="DN")


    calib = parser.add_argument_group("Table Pose Tracking")
    calib.add_argument("--pose-update-every-n-frames", type=int, default=3, metavar="N",
                       help="recompute the camera-to-table pose every N camera frames")
    calib.add_argument("--marker-ir-threshold", type=float, default=3800.0, metavar="COUNTS")
    calib.add_argument("--gravity-sign", type=float, default=1.0, choices=(1.0, -1.0),
                       help="flip if a level table doesn't read ~0 tilt_deg (IMU accelerometer "
                            "sign convention can't be verified without real hardware)")
    calib.add_argument("--marker-height-mm", type=float, default=50.8, metavar="MM",
                       help="height of the fiducial centers above the table surface")
    calib.add_argument("--marker-world-points", type=json.loads, default=None, metavar="JSON",
                       help="five named marker-center [x_mm, y_mm, z_mm] coordinates; normally set in config.json")
    calib.add_argument("--max-marker-radius-mm", type=float, default=15.0, metavar="MM",
                       help="reject IR blobs larger than this physical radius (excludes the ball)")

    # Config file values override argparse defaults; CLI args override everything.
    parser.set_defaults(**file_cfg)

    args = parser.parse_args(argv)

    if args.aligned_depth and args.color_resolution == "off":
        parser.error("--aligned-depth requires --color-resolution to be enabled")
    if not 1 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality must be 1-100")
    if args.max_depth <= 0:
        parser.error("--max-depth must be greater than 0")
    if args.pose_update_every_n_frames <= 0:
        parser.error("--pose-update-every-n-frames must be greater than 0")
    if args.marker_ir_threshold < 0:
        parser.error("--marker-ir-threshold cannot be negative")
    if args.marker_height_mm < 0:
        parser.error("--marker-height-mm cannot be negative")
    if args.max_marker_radius_mm <= 0:
        parser.error("--max-marker-radius-mm must be greater than 0")
    if args.marker_world_points is not None:
        try:
            TableGeometry._validated_world_points(args.marker_world_points)
        except ValueError as exc:
            parser.error(str(exc))
    return args


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    configure_table_geometry(
        marker_height_mm=args.marker_height_mm,
        marker_world_points=args.marker_world_points,
        max_marker_radius_mm=args.max_marker_radius_mm,
    )
    camera = KinectFrameHub(args)

    KinectWebHandler.camera = camera
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
        camera.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
