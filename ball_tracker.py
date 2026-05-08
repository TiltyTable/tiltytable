#!/usr/bin/env python3
"""
Real-time blue ball detection and 3D tracking.

Requires --aligned-depth so the depth map is in the color camera's pixel space,
giving a direct (cx, cy) → depth_mm lookup with no coordinate remapping.

Usage:
    tracker = BallTracker.from_k4a_calibration(k4a.calibration)
    position, detection = tracker.update(color_bgr, depth_mm)
    # position: (X, Y, Z) mm from camera origin, or None
    # detection: BallDetection with raw pixel info, or None
"""

import json
from pathlib import Path

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# HSV range for deep blue.  OpenCV hue is 0–179 (half of 0–360°).
# Blue sits around 210–250° → 105–125 in OpenCV.  Widen a bit for finish
# variation and shadow.  Saturation floor at 60 lets in darker blues while
# rejecting near-grey; keep value floor low for shadowed regions.
# ---------------------------------------------------------------------------
HSV_BLUE_LOW  = np.array([100,  60,  30], dtype=np.uint8)
HSV_BLUE_HIGH = np.array([130, 255, 255], dtype=np.uint8)

# Fraction of the detected pixel radius used when sampling depth.
# The inner 40% avoids edge pixels where depth often reads the background.
_DEPTH_SAMPLE_FRACTION = 0.40

# A candidate is rejected if fewer than this fraction of the sample patch
# has valid depth values (handles specular glare at the ball's highlight).
_MIN_VALID_DEPTH_FRACTION = 0.25

# Contour shape filters
_MIN_CIRCULARITY = 0.72
_MIN_CONTOUR_AREA_PX = 150    # ignore tiny speckles
_MAX_CONTOUR_AREA_PX = 80_000 # ignore room-sized blobs

# Kalman filter assumes 30 fps; updated via reset_dt() if the pipeline knows better.
_DEFAULT_DT = 1.0 / 30.0

# After this many consecutive frames without a detection the filter is reset.
_MAX_MISS_FRAMES = 15


@dataclass
class BallDetection:
    """Raw per-frame output before Kalman smoothing."""
    cx: float          # 2-D pixel centre x (color image)
    cy: float          # 2-D pixel centre y (color image)
    radius_px: float   # 2-D pixel radius
    x_mm: float        # 3-D X (right)
    y_mm: float        # 3-D Y (down)
    z_mm: float        # 3-D Z (into scene)
    radius_mm: float   # estimated physical radius
    circularity: float # 0–1, how circular the blob is


class BallTracker:
    """
    Detects a deep-blue ball in each frame and tracks its 3-D position with a
    constant-velocity Kalman filter.

    Parameters
    ----------
    fx, fy, ppx, ppy
        Color camera intrinsics (pixels).  Read from k4a.calibration or pass
        approximate values for your resolution.
    ball_radius_min_mm, ball_radius_max_mm
        Plausible physical radius range in mm.  Defaults match a ~50 mm
        diameter ball (25 mm radius ± 2.5 mm).  Used to reject false positives
        by cross-checking the pixel radius against the measured depth.
    hsv_low, hsv_high
        HSV colour bounds.  Defaults target deep blue with shade tolerance.
    dt
        Assumed time between frames in seconds for the Kalman motion model.
    """

    def __init__(
        self,
        fx: float,
        fy: float,
        ppx: float,
        ppy: float,
        ball_radius_min_mm: float = 22.5,
        ball_radius_max_mm: float = 27.5,
        hsv_low: np.ndarray = HSV_BLUE_LOW,
        hsv_high: np.ndarray = HSV_BLUE_HIGH,
        dt: float = _DEFAULT_DT,
    ):
        self.fx = fx
        self.fy = fy
        self.ppx = ppx
        self.ppy = ppy
        self.ball_radius_min_mm = ball_radius_min_mm
        self.ball_radius_max_mm = ball_radius_max_mm
        self.hsv_low = hsv_low
        self.hsv_high = hsv_high
        self.dt = dt

        self._tracking = False
        self._miss_count = 0
        self._kf = self._make_kalman(dt)

        # Structuring element reused every frame
        self._morph_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def from_calibration_file(
        cls,
        path,
        k4a_calibration=None,
        camera_type=None,
        **kwargs,
    ) -> "BallTracker":
        """
        Load HSV bounds from a JSON file produced by ball_calibrate.py,
        optionally combined with k4a camera intrinsics.

        path             – path to ball_hsv_calibration.json
        k4a_calibration  – if provided, intrinsics are read from it (same as
                           from_k4a_calibration).  Otherwise fx/fy/ppx/ppy must
                           be supplied via **kwargs.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        hsv_low  = np.array(data["hsv_low"],  dtype=np.uint8)
        hsv_high = np.array(data["hsv_high"], dtype=np.uint8)
        kwargs.setdefault("hsv_low",  hsv_low)
        kwargs.setdefault("hsv_high", hsv_high)
        if k4a_calibration is not None:
            return cls.from_k4a_calibration(k4a_calibration, camera_type, **kwargs)
        return cls(**kwargs)

    @classmethod
    def from_k4a_calibration(
        cls,
        calibration,
        camera_type=None,
        **kwargs,
    ) -> "BallTracker":
        """
        Build a tracker directly from a pyk4a Calibration object.

        calibration  – k4a.calibration
        camera_type  – pyk4a.CalibrationType.COLOR (default) or DEPTH.
                       Pass DEPTH if you are NOT using --aligned-depth.
        """
        from pyk4a import CalibrationType
        if camera_type is None:
            camera_type = CalibrationType.COLOR
        mat = calibration.get_camera_matrix(camera_type)
        return cls(
            fx=float(mat[0, 0]),
            fy=float(mat[1, 1]),
            ppx=float(mat[0, 2]),
            ppy=float(mat[1, 2]),
            **kwargs,
        )

    def reset_dt(self, dt: float) -> None:
        """Call if the measured frame interval differs significantly from 1/30 s."""
        self.dt = dt
        self._kf = self._make_kalman(dt)
        self._tracking = False
        self._miss_count = 0

    def update(
        self,
        color_bgr: np.ndarray,
        depth_mm: np.ndarray,
    ) -> tuple[Optional[tuple[float, float, float]], Optional[BallDetection]]:
        """
        Process one frame.  color_bgr and depth_mm must be the same spatial
        resolution — use --aligned-depth when capturing.

        Returns
        -------
        position : (X, Y, Z) mm from camera origin, or None if not tracking.
        detection : BallDetection with raw pixel data, or None if no blob found.
        """
        detection = self._detect(color_bgr, depth_mm)

        if detection is not None:
            meas = np.array(
                [[detection.x_mm], [detection.y_mm], [detection.z_mm]],
                dtype=np.float32,
            )
            if not self._tracking:
                self._init_filter(detection)
            else:
                self._kf.predict()
                self._kf.correct(meas)
            self._tracking = True
            self._miss_count = 0
        else:
            if self._tracking:
                self._miss_count += 1
                if self._miss_count >= _MAX_MISS_FRAMES:
                    self._tracking = False
                    self._miss_count = 0
                else:
                    # Pure prediction — keep the filter warm
                    self._kf.predict()

        if not self._tracking:
            return None, None

        state = self._kf.statePost
        position = (float(state[0]), float(state[1]), float(state[2]))
        return position, detection

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _detect(
        self, color_bgr: np.ndarray, depth_mm: np.ndarray
    ) -> Optional[BallDetection]:
        # --- colour mask ---
        hsv = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.hsv_low, self.hsv_high)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  self.morph_kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.morph_kernel)

        # --- contour candidates ---
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        best: Optional[BallDetection] = None
        best_score = -1.0

        for contour in contours:
            area = cv2.contourArea(contour)
            if not (_MIN_CONTOUR_AREA_PX <= area <= _MAX_CONTOUR_AREA_PX):
                continue

            perimeter = cv2.arcLength(contour, True)
            if perimeter == 0:
                continue
            circularity = 4.0 * np.pi * area / (perimeter ** 2)
            if circularity < _MIN_CIRCULARITY:
                continue

            (cx, cy), radius_px = cv2.minEnclosingCircle(contour)

            # --- depth sample ---
            z_mm = self._sample_depth(depth_mm, cx, cy, radius_px)
            if z_mm is None:
                continue

            # --- 3-D unproject ---
            x_mm = (cx - self.ppx) * z_mm / self.fx
            y_mm = (cy - self.ppy) * z_mm / self.fy

            # --- radius sanity check (use fx as proxy; ball is roughly round) ---
            radius_mm = radius_px * z_mm / self.fx
            if not (self.ball_radius_min_mm <= radius_mm <= self.ball_radius_max_mm):
                continue

            # Score: circularity × log(area) — prefers round, larger blobs
            score = circularity * np.log(area + 1.0)
            if score > best_score:
                best_score = score
                best = BallDetection(
                    cx=float(cx),
                    cy=float(cy),
                    radius_px=float(radius_px),
                    x_mm=x_mm,
                    y_mm=y_mm,
                    z_mm=z_mm,
                    radius_mm=radius_mm,
                    circularity=circularity,
                )

        return best

    def _sample_depth(
        self,
        depth_mm: np.ndarray,
        cx: float,
        cy: float,
        radius_px: float,
    ) -> Optional[float]:
        r = max(1, int(radius_px * _DEPTH_SAMPLE_FRACTION))
        h, w = depth_mm.shape[:2]
        x0, x1 = max(0, int(cx) - r), min(w, int(cx) + r + 1)
        y0, y1 = max(0, int(cy) - r), min(h, int(cy) + r + 1)
        if x1 <= x0 or y1 <= y0:
            return None

        patch = depth_mm[y0:y1, x0:x1].astype(np.float32, copy=False)
        valid_mask = np.isfinite(patch) & (patch > 0)
        valid_count = int(np.count_nonzero(valid_mask))
        if valid_count < max(1, int(patch.size * _MIN_VALID_DEPTH_FRACTION)):
            return None

        return float(np.median(patch[valid_mask]))

    # ------------------------------------------------------------------
    # Kalman filter helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_kalman(dt: float) -> cv2.KalmanFilter:
        # State: [X, Y, Z, Vx, Vy, Vz]  (mm and mm/s)
        kf = cv2.KalmanFilter(6, 3)

        # Constant-velocity transition
        kf.transitionMatrix = np.array([
            [1, 0, 0, dt,  0,  0],
            [0, 1, 0,  0, dt,  0],
            [0, 0, 1,  0,  0, dt],
            [0, 0, 0,  1,  0,  0],
            [0, 0, 0,  0,  1,  0],
            [0, 0, 0,  0,  0,  1],
        ], dtype=np.float32)

        # Observe position only
        kf.measurementMatrix = np.zeros((3, 6), dtype=np.float32)
        kf.measurementMatrix[0, 0] = 1.0
        kf.measurementMatrix[1, 1] = 1.0
        kf.measurementMatrix[2, 2] = 1.0

        # Process noise: low for position, higher for velocity (ball can accelerate)
        kf.processNoiseCov = np.diag([
            1.0, 1.0, 1.0,    # position continuity (mm²)
            50.0, 50.0, 50.0, # velocity can change quickly (mm²/s²)
        ]).astype(np.float32)

        # Measurement noise: Kinect depth is ~1–3 mm accurate in range;
        # XY reprojection adds a few more mm depending on pixel accuracy.
        kf.measurementNoiseCov = np.diag([
            4.0, 4.0, 9.0  # XY tighter than Z (depth noise)
        ]).astype(np.float32)

        kf.errorCovPost = np.eye(6, dtype=np.float32) * 500.0
        return kf

    def _init_filter(self, det: BallDetection) -> None:
        self._kf = self._make_kalman(self.dt)
        self._kf.statePost = np.array(
            [[det.x_mm], [det.y_mm], [det.z_mm], [0.0], [0.0], [0.0]],
            dtype=np.float32,
        )

    # ------------------------------------------------------------------
    # Property alias so morph_kernel can be accessed consistently
    # ------------------------------------------------------------------

    @property
    def morph_kernel(self) -> np.ndarray:
        return self._morph_kernel


# ---------------------------------------------------------------------------
# Terminal test runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse as _ap
    import sys as _sys

    from pyk4a import (
        Config as _Config,
        ImageFormat as _ImageFormat,
        K4AException as _K4AException,
        K4ATimeoutException as _K4ATimeoutException,
        PyK4A as _PyK4A,
        connected_device_count as _connected_device_count,
    )
    from live_capture_viewer import (
        COLOR_RESOLUTIONS as _COLOR_RESOLUTIONS,
        DEPTH_ENGINE_DISPLAY as _DEPTH_ENGINE_DISPLAY,
        DEPTH_MODES as _DEPTH_MODES,
        FPS_VALUES as _FPS_VALUES,
        color_to_bgr as _color_to_bgr,
        get_depth as _get_depth,
        set_display as _set_display,
    )

    def _parse():
        p = _ap.ArgumentParser(description="Ball tracker terminal test — prints 3-D position live.")
        p.add_argument("--calibration", default="ball_hsv_calibration.json",
                       help="HSV calibration JSON from ball_calibrate.py")
        p.add_argument("--device-id",   type=int, default=0)
        p.add_argument("--color-resolution", choices=sorted(_COLOR_RESOLUTIONS), default="720p")
        p.add_argument("--depth-mode",  choices=sorted(_DEPTH_MODES), default="nfov_unbinned")
        p.add_argument("--fps",         choices=sorted(_FPS_VALUES, key=int), default="30")
        p.add_argument("--depth-engine-display", default=_DEPTH_ENGINE_DISPLAY)
        p.add_argument("--ball-radius-min", type=float, default=22.5)
        p.add_argument("--ball-radius-max", type=float, default=27.5)
        return p.parse_args()

    _args = _parse()

    if _args.depth_engine_display:
        _set_display(_args.depth_engine_display, "depth engine")

    _n = _connected_device_count()
    if _n <= _args.device_id:
        print(f"No Kinect at index {_args.device_id} ({_n} found).", file=_sys.stderr)
        raise SystemExit(1)

    _config = _Config(
        color_resolution=_COLOR_RESOLUTIONS[_args.color_resolution],
        color_format=_ImageFormat.COLOR_BGRA32,
        depth_mode=_DEPTH_MODES[_args.depth_mode],
        camera_fps=_FPS_VALUES[_args.fps],
        synchronized_images_only=True,
    )
    _k4a = _PyK4A(config=_config, device_id=_args.device_id)

    try:
        _k4a.start()
        _tracker = BallTracker.from_calibration_file(
            _args.calibration,
            k4a_calibration=_k4a.calibration,
            ball_radius_min_mm=_args.ball_radius_min,
            ball_radius_max_mm=_args.ball_radius_max,
        )
        print(f"Tracker ready.  HSV low={_tracker.hsv_low.tolist()}  high={_tracker.hsv_high.tolist()}")
        print("Ctrl-C to stop.\n")

        while True:
            try:
                _cap = _k4a.get_capture(timeout=1000)
            except _K4ATimeoutException:
                print("\rTimeout waiting for frame…" + " " * 40, end="", flush=True)
                continue

            _color = _color_to_bgr(_cap.color)
            _depth = _get_depth(_cap, aligned_depth=True)
            if _color is None or _depth is None:
                continue

            _pos, _det = _tracker.update(_color, _depth)

            if _pos is not None:
                _x, _y, _z = _pos
                _r    = _det.radius_mm    if _det else float("nan")
                _circ = _det.circularity  if _det else float("nan")
                _line = (
                    f"\r DETECTED  "
                    f"X={_x:8.1f}  Y={_y:8.1f}  Z={_z:8.1f} mm  "
                    f"r={_r:5.1f} mm  circ={_circ:.2f}   "
                )
            else:
                _miss = _tracker._miss_count
                _line = f"\r no ball   (miss {_miss:2d}/{_MAX_MISS_FRAMES}){'':30}"

            print(_line, end="", flush=True)

    except KeyboardInterrupt:
        print("\nStopped.")
    except (_K4AException, RuntimeError) as _exc:
        print(f"\nError: {_exc}", file=_sys.stderr)
        raise SystemExit(1)
    finally:
        if _k4a.is_running:
            _set_display(_args.depth_engine_display, "depth engine", quiet=True)
            _k4a.stop()
