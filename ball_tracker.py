#!/usr/bin/env python3
"""
Real-time ball detection and 3D tracking using the Azure Kinect IR camera.

The IR image is naturally co-registered with the depth map (both live on the
depth sensor), so no aligned-depth transform is needed.  The ball is a highly
retro-reflective sphere that appears as the brightest blob in the IR image.

Two classes are provided:

    BallDetector  — runs the per-frame image pipeline; stateless between calls.
    BallTracker   — maintains an EKF over BallDetection results; runs a 60 Hz
                    predict thread independently of the camera frame rate.

Typical usage:
    detector = BallDetector.from_k4a_calibration(k4a.calibration)
    tracker  = BallTracker.from_k4a_calibration(k4a.calibration)

    # per frame:
    detection = detector.detect(ir_uint16, depth_mm)
    position, smoothed_detection = tracker.update(detection)
    # position: (X, Y, Z) mm from camera origin, or None
"""

import threading
import time

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional

import camera_geometry

# ---------------------------------------------------------------------------
# Detection parameters
# ---------------------------------------------------------------------------

# Default raw uint16 IR count threshold — overridden at runtime via config/slider.
_BALL_IR_THRESHOLD_DEFAULT = 3000

# Inner fraction of the detected radius used when sampling depth.
# Avoids edge pixels where depth reads the background.
_DEPTH_SAMPLE_FRACTION = 0.40

# Reject candidates if fewer than this fraction of the depth sample patch
# has valid readings.
_MIN_VALID_DEPTH_FRACTION = 0.10

# Contour shape filters
_MIN_CIRCULARITY  = 0.50   # 4π·Area/Perimeter²; perfect circle = 1.0
_MIN_CONTOUR_AREA = 15     # pixels²
_MAX_CONTOUR_AREA = 4000   # pixels² — rejects merged ball+edge blobs
_MIN_FILL_FRACTION = 0.60  # contour area / min-enclosing-circle area


# ---------------------------------------------------------------------------
# Tracking parameters
# ---------------------------------------------------------------------------

_GATE_DISTANCE_MM  = 150.0  # max XY shift between prediction and detection
_PREDICT_HZ        = 60.0   # EKF predict thread rate
_MAX_MISS_FRAMES   = 15     # consecutive misses before tracking resets
_REACQUIRE_FRAMES  = 30     # frames after loss during which the re-acquisition
                             # gate is active (prevents false-positive hijack)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class BallDetection:
    """Raw per-frame output from BallDetector, before EKF smoothing."""
    cx: float          # 2-D pixel centre x (IR image)
    cy: float          # 2-D pixel centre y (IR image)
    radius_px: float   # 2-D pixel radius
    x_mm: float        # 3-D X (right) — directly unprojected, not filtered
    y_mm: float        # 3-D Y (down)
    z_mm: float        # 3-D Z (into scene)
    radius_mm: float   # estimated physical radius


# ---------------------------------------------------------------------------
# Extended Kalman Filter (private)
# ---------------------------------------------------------------------------

class _EKF:
    """
    EKF for 3-D ball tracking.

    State  x  = [X, Y, Z, Vx, Vy, Vz]^T  (mm, mm/s)
    Process   : constant-velocity, linear  →  x_k = F * x_{k-1} + noise
    Measurement: h(x) = [fx·X/Z + ppx,  fy·Y/Z + ppy,  Z]
                nonlinear in Z; Jacobian H linearised at the current estimate.
    """

    def __init__(
        self,
        fx: float, fy: float, ppx: float, ppy: float,
        dt: float,
        Q: np.ndarray,
        R: np.ndarray,
    ):
        self.fx = fx; self.fy = fy
        self.ppx = ppx; self.ppy = ppy

        self.F = np.eye(6, dtype=np.float64)
        self.F[0, 3] = self.F[1, 4] = self.F[2, 5] = dt

        self.Q = Q.astype(np.float64)
        self.R = R.astype(np.float64)
        self.x = np.zeros((6, 1), dtype=np.float64)
        self.P = np.eye(6, dtype=np.float64) * 500.0
        self._lock = threading.Lock()

    def init(self, X: float, Y: float, Z: float) -> None:
        with self._lock:
            self.x[:] = 0.0
            self.x[0, 0] = X
            self.x[1, 0] = Y
            self.x[2, 0] = Z
            self.P = np.eye(6, dtype=np.float64) * 500.0

    def predict(self) -> None:
        with self._lock:
            self.x = self.F @ self.x
            self.P = self.F @ self.P @ self.F.T + self.Q

    def correct(self, u_px: float, v_px: float, z_mm: float) -> None:
        with self._lock:
            X, Y, Z = float(self.x[0]), float(self.x[1]), float(self.x[2])
            if abs(Z) < 1.0:
                return

            h = np.array([
                [self.fx * X / Z + self.ppx],
                [self.fy * Y / Z + self.ppy],
                [Z],
            ], dtype=np.float64)

            H = np.zeros((3, 6), dtype=np.float64)
            H[0, 0] =  self.fx / Z
            H[0, 2] = -self.fx * X / (Z * Z)
            H[1, 1] =  self.fy / Z
            H[1, 2] = -self.fy * Y / (Z * Z)
            H[2, 2] =  1.0

            z = np.array([[u_px], [v_px], [z_mm]], dtype=np.float64)
            y = z - h
            S = H @ self.P @ H.T + self.R
            K = self.P @ H.T @ np.linalg.inv(S)
            I_KH = np.eye(6) - K @ H
            self.x = self.x + K @ y
            self.P = I_KH @ self.P @ I_KH.T + K @ self.R @ K.T

    @property
    def position(self) -> tuple[float, float, float]:
        with self._lock:
            return (float(self.x[0]), float(self.x[1]), float(self.x[2]))


# ---------------------------------------------------------------------------
# Ball detector
# ---------------------------------------------------------------------------

class BallDetector:
    """
    Detects a retro-reflective ball in Azure Kinect IR frames.

    Stateless between calls: each detect() call runs the full image pipeline
    independently.  The debug_frame property returns a BGR annotation of the
    most recent call, useful for streaming to a tracker view.

    Parameters
    ----------
    fx, fy, ppx, ppy
        Depth/IR camera intrinsics (pixels).
    ball_radius_min_mm, ball_radius_max_mm
        Plausible physical radius range in mm.  Defaults suit a 55 mm
        diameter ball.
    debug
        Build the annotated BGR frame used by the calibration web UI.
    """

    def __init__(
        self,
        fx: float,
        fy: float,
        ppx: float,
        ppy: float,
        ball_radius_min_mm: float = 20.0,
        ball_radius_max_mm: float = 40.0,
        ball_ir_threshold: int = _BALL_IR_THRESHOLD_DEFAULT,
        debug: bool = True,
    ):
        self.fx = fx
        self.fy = fy
        self.ppx = ppx
        self.ppy = ppy
        self.ball_radius_min_mm = ball_radius_min_mm
        self.ball_radius_max_mm = ball_radius_max_mm
        self.ball_ir_threshold = ball_ir_threshold
        self.debug = debug
        self._debug_frame: Optional[np.ndarray] = None
        self.last_reject_counts: dict[str, int] = {}

    @classmethod
    def from_k4a_calibration(
        cls,
        calibration,
        camera_type=None,
        **kwargs,
    ) -> "BallDetector":
        """Build a detector from a pyk4a Calibration object."""
        from pyk4a import CalibrationType
        if camera_type is None:
            camera_type = CalibrationType.DEPTH
        mat = calibration.get_camera_matrix(camera_type)
        return cls(
            fx=float(mat[0, 0]),
            fy=float(mat[1, 1]),
            ppx=float(mat[0, 2]),
            ppy=float(mat[1, 2]),
            **kwargs,
        )

    @property
    def debug_frame(self) -> Optional[np.ndarray]:
        """BGR annotation of the last detect() call, or None before the first."""
        return self._debug_frame

    def detect(
        self,
        ir_uint16: np.ndarray,
        depth_mm: np.ndarray,
    ) -> Optional[BallDetection]:
        """
        Run the detection pipeline on one camera frame.

        Returns a BallDetection for the best candidate, or None if no ball
        was found. Always updates last_reject_counts and updates debug_frame
        when debug rendering is enabled.
        """
        counts = {"shape": 0, "fill": 0, "depth": 0, "size": 0, "accepted": 0}
        self.last_reject_counts = counts

        valid = ir_uint16 > 0
        if not valid.any():
            self._debug_frame = None
            return None

        ir_blurred = cv2.GaussianBlur(ir_uint16.astype(np.float32), (3, 3), 0).astype(np.uint16)
        mask = np.where((ir_blurred >= self.ball_ir_threshold) & valid, np.uint8(255), np.uint8(0))

        dbg = None
        if self.debug:
            # 8-bit display: same linear scale as the active brightness view.
            ir8 = (ir_uint16 >> 8).astype(np.uint8)
            dbg = cv2.cvtColor(ir8, cv2.COLOR_GRAY2BGR)
            tint = np.zeros_like(dbg)
            tint[mask > 0] = (0, 60, 20)
            dbg = cv2.addWeighted(dbg, 1.0, tint, 0.5, 0)

        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        radius_mid = (self.ball_radius_min_mm + self.ball_radius_max_mm) / 2.0
        best: Optional[BallDetection] = None
        best_score = float("inf")

        _CLR_SHAPE = (0,  80, 200)
        _CLR_FILL  = (200, 0, 200)
        _CLR_DEPTH = (0, 165, 255)
        _CLR_SIZE  = (0, 200, 200)
        _CLR_CAND  = (0, 200,  80)
        _CLR_BEST  = (0, 255, 120)

        for c in contours:
            area = float(cv2.contourArea(c))
            if area < _MIN_CONTOUR_AREA or area > _MAX_CONTOUR_AREA:
                continue

            perimeter = float(cv2.arcLength(c, closed=True))
            if perimeter < 1.0:
                continue

            circularity = 4.0 * np.pi * area / (perimeter * perimeter)
            if circularity < _MIN_CIRCULARITY:
                counts["shape"] += 1
                if dbg is not None:
                    cv2.drawContours(dbg, [c], -1, _CLR_SHAPE, 1)
                continue

            (_, _), radius_px = cv2.minEnclosingCircle(c)
            radius_px = float(radius_px)
            fill = area / (np.pi * radius_px * radius_px) if radius_px > 0 else 0.0
            if fill < _MIN_FILL_FRACTION:
                counts["fill"] += 1
                if dbg is not None:
                    cv2.drawContours(dbg, [c], -1, _CLR_FILL, 1)
                continue

            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]
            icx, icy, ir_ = int(round(cx)), int(round(cy)), int(round(radius_px))

            z_mm = self._sample_depth(depth_mm, cx, cy, radius_px)
            if z_mm is None:
                z_mm = self._sample_depth_ring(depth_mm, cx, cy, radius_px, 1.0, 1.6)
            if z_mm is None:
                counts["depth"] += 1
                if dbg is not None:
                    cv2.circle(dbg, (icx, icy), ir_, _CLR_DEPTH, 1)
                continue

            x_mm, y_mm, _ = camera_geometry.unproject_pixel(
                cx, cy, z_mm, self.fx, self.fy, self.ppx, self.ppy,
            )

            radius_mm = radius_px * z_mm / self.fx
            if not (self.ball_radius_min_mm <= radius_mm <= self.ball_radius_max_mm):
                counts["size"] += 1
                if dbg is not None:
                    cv2.circle(dbg, (icx, icy), ir_, _CLR_SIZE, 1)
                continue

            counts["accepted"] += 1
            if dbg is not None:
                cv2.circle(dbg, (icx, icy), ir_, _CLR_CAND, 1)

            score = abs(radius_mm - radius_mid)
            if score < best_score:
                best_score = score
                best = BallDetection(
                    cx=cx, cy=cy, radius_px=radius_px,
                    x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, radius_mm=radius_mm,
                )

        if best is not None and dbg is not None:
            icx, icy, ir_ = int(round(best.cx)), int(round(best.cy)), int(round(best.radius_px))
            cv2.circle(dbg, (icx, icy), ir_, _CLR_BEST, 2)
            cv2.drawMarker(dbg, (icx, icy), _CLR_BEST, cv2.MARKER_CROSS, ir_ // 2, 1, cv2.LINE_AA)

        if dbg is not None:
            summary = "  ".join(f"{k}:{v}" for k, v in counts.items() if v) or "no candidates"
            cv2.putText(dbg, summary, (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 255, 255), 1, cv2.LINE_AA)

        self._debug_frame = dbg
        return best

    def _sample_depth(
        self, depth_mm: np.ndarray, cx: float, cy: float, radius_px: float,
    ) -> Optional[float]:
        return camera_geometry.sample_depth_patch(
            depth_mm, cx, cy, radius_px,
            _DEPTH_SAMPLE_FRACTION, _MIN_VALID_DEPTH_FRACTION,
        )

    def _sample_depth_ring(
        self, depth_mm: np.ndarray, cx: float, cy: float,
        radius_px: float, r_low: float, r_high: float,
    ) -> Optional[float]:
        return camera_geometry.sample_depth_ring(
            depth_mm, cx, cy, radius_px, r_low, r_high, _MIN_VALID_DEPTH_FRACTION,
        )


# ---------------------------------------------------------------------------
# Ball tracker
# ---------------------------------------------------------------------------

class BallTracker:
    """
    Tracks a ball across frames using an EKF.

    Takes BallDetection results from BallDetector and maintains a
    constant-velocity EKF state [X, Y, Z, Vx, Vy, Vz] (mm, mm/s).

    The predict step runs at _PREDICT_HZ in a daemon background thread;
    update() only performs EKF corrections when a detection is provided.

    Parameters
    ----------
    fx, fy, ppx, ppy
        Depth/IR camera intrinsics (pixels).  Must match those used by the
        BallDetector so the EKF measurement model is consistent.
    """

    def __init__(self, fx: float, fy: float, ppx: float, ppy: float):
        self.fx = fx
        self.fy = fy
        self.ppx = ppx
        self.ppy = ppy

        self._tracking = False
        self._miss_count = 0
        self._lost_pos: Optional[tuple[float, float]] = None
        self._lost_frames = 0
        self._ekf = self._make_ekf(1.0 / _PREDICT_HZ)
        self._stop_predict = threading.Event()
        _pt = threading.Thread(target=self._predict_loop, daemon=True, name="ekf-predict")
        _pt.start()

    @classmethod
    def from_k4a_calibration(cls, calibration, camera_type=None) -> "BallTracker":
        """Build a tracker from a pyk4a Calibration object."""
        from pyk4a import CalibrationType
        if camera_type is None:
            camera_type = CalibrationType.DEPTH
        mat = calibration.get_camera_matrix(camera_type)
        return cls(
            fx=float(mat[0, 0]),
            fy=float(mat[1, 1]),
            ppx=float(mat[0, 2]),
            ppy=float(mat[1, 2]),
        )

    def close(self) -> None:
        """Stop the background prediction thread."""
        self._stop_predict.set()

    def update(
        self,
        detection: Optional[BallDetection],
    ) -> tuple[Optional[tuple[float, float, float]], Optional[BallDetection]]:
        """
        Advance the tracker with the latest detection result.

        Parameters
        ----------
        detection
            Output of BallDetector.detect(), or None if no ball was found.

        Returns
        -------
        position
            EKF-smoothed (X, Y, Z) mm from camera origin, or None if not tracking.
        smoothed_detection
            The input detection with radius_px re-derived from the EKF Z so the
            display circle is smooth, or None.
        """
        if not self._tracking:
            self._lost_frames += 1
            if detection is not None:
                if self._lost_pos is not None and self._lost_frames <= _REACQUIRE_FRAMES:
                    lx, ly = self._lost_pos
                    dist = ((detection.x_mm - lx) ** 2 + (detection.y_mm - ly) ** 2) ** 0.5
                    if dist > _GATE_DISTANCE_MM:
                        detection = None
                if detection is not None:
                    self._ekf.init(detection.x_mm, detection.y_mm, detection.z_mm)
                    self._tracking = True
                    self._miss_count = 0
                    self._lost_pos = None
                    self._lost_frames = 0
        else:
            if detection is not None:
                px, py, _ = self._ekf.position
                dist = ((detection.x_mm - px) ** 2 + (detection.y_mm - py) ** 2) ** 0.5
                if dist <= _GATE_DISTANCE_MM:
                    self._ekf.correct(detection.cx, detection.cy, detection.z_mm)
                    self._miss_count = 0
                else:
                    detection = None
                    self._miss_count += 1
            else:
                self._miss_count += 1

            if self._miss_count >= _MAX_MISS_FRAMES:
                self._lost_pos = (self._ekf.position[0], self._ekf.position[1])
                self._lost_frames = 0
                self._tracking = False
                self._miss_count = 0

        if not self._tracking:
            return None, None

        pos = self._ekf.position
        if detection is not None:
            ekf_z = pos[2]
            if ekf_z > 1.0:
                smooth_r = detection.radius_mm * self.fx / ekf_z
                detection = BallDetection(
                    cx=detection.cx, cy=detection.cy, radius_px=smooth_r,
                    x_mm=detection.x_mm, y_mm=detection.y_mm,
                    z_mm=detection.z_mm, radius_mm=detection.radius_mm,
                )
        return pos, detection

    def _predict_loop(self) -> None:
        dt = 1.0 / _PREDICT_HZ
        while not self._stop_predict.is_set():
            t0 = time.monotonic()
            if self._tracking:
                self._ekf.predict()
            elapsed = time.monotonic() - t0
            rem = dt - elapsed
            if rem > 0:
                time.sleep(rem)

    def _make_ekf(self, dt: float) -> _EKF:
        s = dt * 30.0  # scale Q so noise per second is constant regardless of predict rate
        Q = np.diag([
            1.0  * s, 1.0  * s, 1.0  * s,
            50.0 * s, 50.0 * s, 50.0 * s,
        ])
        R = np.diag([2.0, 2.0, 25.0])  # [u_px, v_px, z_mm]
        return _EKF(
            fx=self.fx, fy=self.fy, ppx=self.ppx, ppy=self.ppy,
            dt=dt, Q=Q, R=R,
        )


# ---------------------------------------------------------------------------
# Terminal test runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse as _ap
    import sys as _sys

    from pyk4a import (
        Config as _Config,
        K4AException as _K4AException,
        K4ATimeoutException as _K4ATimeoutException,
        PyK4A as _PyK4A,
        connected_device_count as _connected_device_count,
    )
    from live_capture_viewer import (
        DEPTH_ENGINE_DISPLAY as _DEPTH_ENGINE_DISPLAY,
        DEPTH_MODES as _DEPTH_MODES,
        FPS_VALUES as _FPS_VALUES,
        get_depth as _get_depth,
        set_display as _set_display,
    )

    def _parse():
        p = _ap.ArgumentParser(description="Ball tracker terminal test — prints 3-D position live.")
        p.add_argument("--device-id",   type=int, default=0)
        p.add_argument("--depth-mode",  choices=sorted(_DEPTH_MODES), default="nfov_unbinned")
        p.add_argument("--fps",         choices=sorted(_FPS_VALUES, key=int), default="30")
        p.add_argument("--depth-engine-display", default=_DEPTH_ENGINE_DISPLAY)
        p.add_argument("--ball-radius-min",  type=float, default=25.0)
        p.add_argument("--ball-radius-max",  type=float, default=30.0)
        return p.parse_args()

    _args = _parse()

    if _args.depth_engine_display:
        _set_display(_args.depth_engine_display, "depth engine")

    _n = _connected_device_count()
    if _n <= _args.device_id:
        print(f"No Kinect at index {_args.device_id} ({_n} found).", file=_sys.stderr)
        raise SystemExit(1)

    _config = _Config(
        depth_mode=_DEPTH_MODES[_args.depth_mode],
        camera_fps=_FPS_VALUES[_args.fps],
        synchronized_images_only=False,
    )
    _k4a = _PyK4A(config=_config, device_id=_args.device_id)

    try:
        _k4a.start()
        _detector = BallDetector.from_k4a_calibration(
            _k4a.calibration,
            ball_radius_min_mm=_args.ball_radius_min,
            ball_radius_max_mm=_args.ball_radius_max,
        )
        _tracker = BallTracker.from_k4a_calibration(_k4a.calibration)
        print(
            f"Detector ready.  ball_ir_threshold={_detector.ball_ir_threshold}  "
            f"radius=[{_args.ball_radius_min}, {_args.ball_radius_max}] mm"
        )
        print("Ctrl-C to stop.\n")

        while True:
            try:
                _cap = _k4a.get_capture(timeout=1000)
            except _K4ATimeoutException:
                print("\rTimeout waiting for frame…" + " " * 40, end="", flush=True)
                continue

            _ir    = _cap.ir
            _depth = _get_depth(_cap, aligned_depth=False)
            if _ir is None or _depth is None:
                continue

            _det = _detector.detect(_ir, _depth)
            _pos, _det = _tracker.update(_det)

            if _pos is not None:
                _x, _y, _z = _pos
                _r = _det.radius_mm if _det else float("nan")
                _line = (
                    f"\r DETECTED  "
                    f"X={_x:8.1f}  Y={_y:8.1f}  Z={_z:8.1f} mm  "
                    f"r={_r:5.1f} mm   "
                )
            else:
                _line = f"\r no ball   (miss {_tracker._miss_count:2d}/{_MAX_MISS_FRAMES}){'':30}"

            print(_line, end="", flush=True)

    except KeyboardInterrupt:
        print("\nStopped.")
    except (_K4AException, RuntimeError) as _exc:
        print(f"\nError: {_exc}", file=_sys.stderr)
        raise SystemExit(1)
    finally:
        _tracker.close()
        if _k4a.is_running:
            _set_display(_args.depth_engine_display, "depth engine", quiet=True)
            _k4a.stop()
