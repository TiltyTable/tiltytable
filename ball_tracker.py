#!/usr/bin/env python3
"""
Real-time ball detection and 3D tracking using the Azure Kinect IR camera.

The IR image is naturally co-registered with the depth map (both live on the
depth sensor), so no aligned-depth transform is needed.  Detection first
isolates the brightly lit tabletop (bright-region mask -> largest component ->
convex hull), then finds dark blobs inside it: grid-gap lines are erased with
a morphological opening and the ball is selected by circularity, fill fraction
and depth-validated physical radius.  Tracking uses a hand-rolled EKF whose
measurement function is the perspective projection

    h(x) = [ fx·X/Z + ppx,  fy·Y/Z + ppy,  Z ]

which is nonlinear in Z; the Jacobian H is computed analytically at each step.

Usage:
    tracker = BallTracker.from_k4a_calibration(k4a.calibration)
    position, detection = tracker.update(ir_uint16, depth_mm)
    # position: (X, Y, Z) mm from camera origin, or None
    # detection: BallDetection with raw pixel info, or None
"""

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# IR normalisation parameters
# The 16-bit IR image is contrast-stretched between the scene's low and high
# percentiles before thresholding.  Adjust _IR_THRESH_FRACTION if the ball
# is not cleanly separated from the background.
# ---------------------------------------------------------------------------
_IR_PERCENTILE_LOW  = 1     # scene floor for contrast stretch (near-minimum of valid pixels)
_IR_PERCENTILE_HIGH = 99    # scene ceiling for contrast stretch (avoids hot spots)

# A pixel is "dark" when below this fraction of the LOCAL background level
# (morphological closing of the image).  Relative thresholding keeps the ball
# detectable on both brightly lit and dim/unlit tiles.
_IR_THRESH_FRACTION = 0.5

# Kernel for the local-background closing.  Must comfortably exceed the ball
# diameter in pixels so the closing erases the ball (and smaller features)
# and reports the surrounding table level.
_BG_CLOSE_KERNEL_PX = 75

# Table-mask parameters.  The tabletop is the large bright region (IR-lit
# tiles); dark-blob search is confined to its convex hull so the floor and
# surroundings can never produce candidates.
_TABLE_BRIGHT_FRACTION   = 0.5   # bright-tile threshold for the table mask
_TABLE_CLOSE_KERNEL_PX   = 9     # merges tiles across grid gaps before CC labelling
_MIN_TABLE_AREA_FRACTION = 0.05  # of valid FOV; below this, fall back to full FOV

# Grid-gap suppression: opening the dark mask with this kernel erases the
# gap lines between tiles.  Must exceed the gap width in pixels and stay
# well below the ball diameter in pixels.
_GRID_OPEN_KERNEL_PX = 13

# Fraction of the detected pixel radius used when sampling depth.
# The inner 40% avoids edge pixels where depth often reads the background.
_DEPTH_SAMPLE_FRACTION = 0.40

# A candidate is rejected if fewer than this fraction of the sample patch
# has valid depth values (handles specular glare at the ball's highlight).
_MIN_VALID_DEPTH_FRACTION = 0.10

# Contour-based detection parameters.
# Since the ball size is consistent we filter on circularity, fill fraction and
# depth-validated physical radius rather than scanning a Hough accumulator.
_MIN_CIRCULARITY  = 0.82  # 4π·Area/Perimeter²; perfect circle = 1.0; square = 0.785
_MIN_CONTOUR_AREA = 15    # pixels² — rejects tiny noise blobs
_MIN_FILL_FRACTION = 0.75 # contour area / min-enclosing-circle area; square ≈ 0.64

# Gaussian blur sigma applied before thresholding (sensor-noise suppression
# only; grid lines are removed structurally by the opening above).
_IR_BLUR_SIGMA = 2.0


# When already tracking, detections farther than this from the predicted XY
# position are rejected as false positives.
_GATE_DISTANCE_MM = 150.0

# EKF assumes 30 fps; updated via reset_dt() if the pipeline knows better.
_DEFAULT_DT = 1.0 / 30.0

# After this many consecutive frames without a detection the filter is reset.
_MAX_MISS_FRAMES = 15


@dataclass
class BallDetection:
    """Raw per-frame output before EKF smoothing."""
    cx: float          # 2-D pixel centre x (IR image)
    cy: float          # 2-D pixel centre y (IR image)
    radius_px: float   # 2-D pixel radius
    x_mm: float        # 3-D X (right) — directly unprojected, not filtered
    y_mm: float        # 3-D Y (down)
    z_mm: float        # 3-D Z (into scene)
    radius_mm: float   # estimated physical radius


# ---------------------------------------------------------------------------
# Extended Kalman Filter
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
        Q: np.ndarray,   # 6×6 process noise covariance
        R: np.ndarray,   # 3×3 measurement noise covariance [u_px, v_px, z_mm]
    ):
        self.fx = fx; self.fy = fy
        self.ppx = ppx; self.ppy = ppy

        self.F = np.eye(6, dtype=np.float64)
        self.F[0, 3] = self.F[1, 4] = self.F[2, 5] = dt

        self.Q = Q.astype(np.float64)
        self.R = R.astype(np.float64)
        self.x = np.zeros((6, 1), dtype=np.float64)
        self.P = np.eye(6, dtype=np.float64) * 500.0

    def init(self, X: float, Y: float, Z: float) -> None:
        self.x[:] = 0.0
        self.x[0, 0] = X
        self.x[1, 0] = Y
        self.x[2, 0] = Z
        self.P = np.eye(6, dtype=np.float64) * 500.0

    def predict(self) -> np.ndarray:
        """Advance state one step; returns the predicted state vector."""
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.x

    def correct(self, u_px: float, v_px: float, z_mm: float) -> None:
        """Incorporate a pixel + depth measurement."""
        X, Y, Z = float(self.x[0]), float(self.x[1]), float(self.x[2])
        if abs(Z) < 1.0:
            return

        # Predicted measurement h(x̂⁻)
        h = np.array([
            [self.fx * X / Z + self.ppx],
            [self.fy * Y / Z + self.ppy],
            [Z],
        ], dtype=np.float64)

        # Analytical Jacobian  H = ∂h/∂x  evaluated at current estimate
        H = np.zeros((3, 6), dtype=np.float64)
        H[0, 0] =  self.fx / Z
        H[0, 2] = -self.fx * X / (Z * Z)
        H[1, 1] =  self.fy / Z
        H[1, 2] = -self.fy * Y / (Z * Z)
        H[2, 2] =  1.0

        # Innovation
        z = np.array([[u_px], [v_px], [z_mm]], dtype=np.float64)
        y = z - h

        # Kalman gain
        S = H @ self.P @ H.T + self.R
        K = self.P @ H.T @ np.linalg.inv(S)

        # State and covariance update  (Joseph form for numerical stability)
        I_KH = np.eye(6) - K @ H
        self.x = self.x + K @ y
        self.P = I_KH @ self.P @ I_KH.T + K @ self.R @ K.T

    @property
    def position(self) -> tuple[float, float, float]:
        return (float(self.x[0]), float(self.x[1]), float(self.x[2]))


# ---------------------------------------------------------------------------
# Ball tracker
# ---------------------------------------------------------------------------

class BallTracker:
    """
    Detects a ball in IR frames from the Azure Kinect and tracks its 3-D
    position with an EKF.

    Parameters
    ----------
    fx, fy, ppx, ppy
        Depth/IR camera intrinsics (pixels).  Read from k4a.calibration or
        supply manually.
    ball_radius_min_mm, ball_radius_max_mm
        Plausible physical radius range in mm.  Defaults suit a 55 mm
        diameter ball with margin for the IR shadow halo, which inflates
        the apparent silhouette by several mm.
    ir_thresh_fraction
        A pixel counts as "dark" when below this fraction (0–1) of the
        local background level.  Lower it if dim tiles are falsely
        detected; raise it if a faintly dark ball is missed.
    dt
        Assumed time between frames in seconds for the EKF motion model.
    """

    def __init__(
        self,
        fx: float,
        fy: float,
        ppx: float,
        ppy: float,
        ball_radius_min_mm: float = 20.0,
        ball_radius_max_mm: float = 40.0,
        ir_thresh_fraction: float = _IR_THRESH_FRACTION,
        dt: float = _DEFAULT_DT,
    ):
        self.fx = fx
        self.fy = fy
        self.ppx = ppx
        self.ppy = ppy
        self.ball_radius_min_mm = ball_radius_min_mm
        self.ball_radius_max_mm = ball_radius_max_mm
        self.ir_thresh_fraction = ir_thresh_fraction
        self.dt = dt

        self._tracking = False
        self._miss_count = 0
        self._ekf = self._make_ekf(dt)
        # Rect kernels: box morphology runs in O(1) per pixel (van Herk),
        # and these two only bridge gaps / estimate background, so the
        # kernel shape is irrelevant.
        self._table_close_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (_TABLE_CLOSE_KERNEL_PX, _TABLE_CLOSE_KERNEL_PX))
        self._bg_close_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (_BG_CLOSE_KERNEL_PX, _BG_CLOSE_KERNEL_PX))
        self._grid_open_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (_GRID_OPEN_KERNEL_PX, _GRID_OPEN_KERNEL_PX))
        self._debug_frame: Optional[np.ndarray] = None
        # Per-frame candidate tally from the last _detect() call:
        # {"shape": n, "fill": n, "depth": n, "size": n, "accepted": n}
        self.last_reject_counts: dict[str, int] = {}

    @property
    def debug_frame(self) -> Optional[np.ndarray]:
        """BGR image of the last detection attempt, or None before the first frame."""
        return self._debug_frame

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def from_k4a_calibration(
        cls,
        calibration,
        camera_type=None,
        **kwargs,
    ) -> "BallTracker":
        """
        Build a tracker from a pyk4a Calibration object.

        calibration  – k4a.calibration
        camera_type  – pyk4a.CalibrationType.DEPTH (default; IR is on the
                       depth sensor).  Override only if you know you want
                       the colour camera intrinsics.
        """
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

    def reset_dt(self, dt: float) -> None:
        """Call if the measured frame interval differs significantly from 1/30 s."""
        self.dt = dt
        self._ekf = self._make_ekf(dt)
        self._tracking = False
        self._miss_count = 0

    def update(
        self,
        ir_uint16: np.ndarray,
        depth_mm: np.ndarray,
    ) -> tuple[Optional[tuple[float, float, float]], Optional[BallDetection]]:
        """
        Process one frame.  ir_uint16 and depth_mm must be the same spatial
        resolution — both come from the depth sensor so they are natively
        aligned (no aligned-depth transform needed).

        Returns
        -------
        position : EKF-smoothed (X, Y, Z) mm from camera origin, or None.
        detection : BallDetection with raw pixel data, or None if no blob found.
        """
        detection = self._detect(ir_uint16, depth_mm)

        if not self._tracking:
            if detection is not None:
                self._ekf.init(detection.x_mm, detection.y_mm, detection.z_mm)
                self._tracking = True
                self._miss_count = 0
        else:
            predicted = self._ekf.predict()
            px = float(predicted[0])
            py = float(predicted[1])

            if detection is not None:
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
                self._tracking = False
                self._miss_count = 0

        if not self._tracking:
            return None, None

        return self._ekf.position, detection

    # ------------------------------------------------------------------
    # Detection (IR)
    # ------------------------------------------------------------------

    def _detect(
        self, ir_uint16: np.ndarray, depth_mm: np.ndarray
    ) -> Optional[BallDetection]:
        counts = {"shape": 0, "fill": 0, "depth": 0, "size": 0, "accepted": 0}
        self.last_reject_counts = counts

        # --- contrast-stretch the 16-bit IR image to 8-bit ---
        # Pixels outside the Kinect's circular FOV are exactly 0 (invalid).
        # Compute contrast stretch only on valid pixels, then paint invalid
        # pixels as 255 so they are bright background in THRESH_BINARY_INV.
        ir_f = ir_uint16.astype(np.float32, copy=False)
        # Erode the valid mask slightly to exclude the dim FOV-boundary ring
        # (those pixels are technically > 0 but are vignetting artefacts).
        raw_valid = (ir_uint16 > 0).astype(np.uint8)
        fov_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
        valid_u8 = cv2.erode(raw_valid, fov_kernel)
        valid = valid_u8 > 0
        valid_px = ir_f[valid]
        if valid_px.size == 0:
            self._debug_frame = None
            return None
        p_lo = float(np.percentile(valid_px, _IR_PERCENTILE_LOW))
        p_hi = float(np.percentile(valid_px, _IR_PERCENTILE_HIGH))
        if p_hi <= p_lo:
            self._debug_frame = None
            return None
        ir8 = np.clip((ir_f - p_lo) / (p_hi - p_lo) * 255.0, 0, 255).astype(np.uint8)
        ir8[~valid] = 255   # FOV boundary + invalid pixels → white → not detected

        ir_blur = cv2.GaussianBlur(ir8, (0, 0), _IR_BLUR_SIGMA)

        # --- table mask: confine the dark-blob search to the lit tabletop ---
        # Bright tiles → close over grid gaps → largest component → convex
        # hull.  The hull also covers grid gaps, missing tiles and a ball
        # sitting at the table edge; the floor/surroundings are excluded.
        bright = cv2.inRange(ir_blur, int(_TABLE_BRIGHT_FRACTION * 255), 255)
        bright[~valid] = 0
        bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, self._table_close_kernel)
        table_mask = valid_u8 * 255  # fallback: whole valid FOV
        n_lbl, labels, stats, _ = cv2.connectedComponentsWithStats(bright)
        if n_lbl > 1:
            largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            if stats[largest, cv2.CC_STAT_AREA] >= _MIN_TABLE_AREA_FRACTION * valid_px.size:
                comp = (labels == largest).astype(np.uint8)
                t_cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if t_cnts:
                    hull = cv2.convexHull(max(t_cnts, key=cv2.contourArea))
                    table_mask = np.zeros_like(ir8)
                    cv2.fillConvexPoly(table_mask, hull, 255)

        # Local background: closing erases dark features smaller than the
        # kernel, leaving the surrounding table level.  A pixel is "dark"
        # relative to that level, so the ball is found on dim tiles too.
        bg = cv2.morphologyEx(ir_blur, cv2.MORPH_CLOSE, self._bg_close_kernel)
        dark = ir_blur.astype(np.float32) < bg.astype(np.float32) * self.ir_thresh_fraction
        mask = np.where(dark & (table_mask > 0), np.uint8(255), np.uint8(0))
        # Opening erases grid-gap lines (thinner than the kernel); the
        # ball's disc survives with its silhouette intact.
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._grid_open_kernel)

        # Build debug frame: IR image as background, mask tinted blue,
        # table boundary outlined grey.
        dbg = cv2.cvtColor(ir8, cv2.COLOR_GRAY2BGR)
        tint = np.zeros_like(dbg)
        tint[mask > 0] = (60, 20, 0)   # blue tint on masked pixels
        dbg = cv2.addWeighted(dbg, 1.0, tint, 0.5, 0)
        tm_cnts, _ = cv2.findContours(table_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(dbg, tm_cnts, -1, (128, 128, 128), 1)

        # --- Contour-based detection ---
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        radius_mid = (self.ball_radius_min_mm + self.ball_radius_max_mm) / 2.0
        best: Optional[BallDetection] = None
        best_score = float("inf")

        _CLR_SHAPE  = (0,  80, 200)   # red  — failed area/circularity
        _CLR_FILL   = (200, 0, 200)   # magenta — failed fill fraction
        _CLR_DEPTH  = (0, 165, 255)   # orange — failed depth curvature
        _CLR_SIZE   = (0, 200, 200)   # yellow — failed physical size
        _CLR_CAND   = (0, 200,  80)   # green  — accepted candidate
        _CLR_BEST   = (0, 255, 120)   # bright green — final pick

        for c in contours:
            area = float(cv2.contourArea(c))
            if area < _MIN_CONTOUR_AREA:
                continue

            perimeter = float(cv2.arcLength(c, closed=True))
            if perimeter < 1.0:
                continue

            circularity = 4.0 * np.pi * area / (perimeter * perimeter)
            if circularity < _MIN_CIRCULARITY:
                counts["shape"] += 1
                cv2.drawContours(dbg, [c], -1, _CLR_SHAPE, 1)
                continue

            # The enclosing circle gives a radius estimate that is robust to
            # nicks/highlights biting into the contour; the fill fraction
            # rejects compact-but-non-round blobs (e.g. missing-tile holes).
            (_, _), radius_px = cv2.minEnclosingCircle(c)
            radius_px = float(radius_px)
            fill = area / (np.pi * radius_px * radius_px) if radius_px > 0 else 0.0
            if fill < _MIN_FILL_FRACTION:
                counts["fill"] += 1
                cv2.drawContours(dbg, [c], -1, _CLR_FILL, 1)
                continue

            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]
            icx, icy, ir_ = int(round(cx)), int(round(cy)), int(round(radius_px))

            # Get depth at ball centre; if the ball absorbs IR (invalid),
            # fall back to the table surface just outside the silhouette.
            z_mm = self._sample_depth(depth_mm, cx, cy, radius_px)
            if z_mm is None:
                z_mm = self._sample_depth_ring(
                    depth_mm, cx, cy, radius_px, 1.0, 1.6,
                )
            if z_mm is None:
                counts["depth"] += 1
                cv2.circle(dbg, (icx, icy), ir_, _CLR_DEPTH, 1)
                continue
            x_mm = (cx - self.ppx) * z_mm / self.fx
            y_mm = (cy - self.ppy) * z_mm / self.fy

            radius_mm = radius_px * z_mm / self.fx
            if not (self.ball_radius_min_mm <= radius_mm <= self.ball_radius_max_mm):
                counts["size"] += 1
                cv2.circle(dbg, (icx, icy), ir_, _CLR_SIZE, 1)
                continue

            counts["accepted"] += 1
            cv2.circle(dbg, (icx, icy), ir_, _CLR_CAND, 1)

            score = abs(radius_mm - radius_mid)
            if score < best_score:
                best_score = score
                best = BallDetection(
                    cx=cx, cy=cy, radius_px=radius_px,
                    x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, radius_mm=radius_mm,
                )

        # Highlight the winning detection
        if best is not None:
            icx, icy, ir_ = int(round(best.cx)), int(round(best.cy)), int(round(best.radius_px))
            cv2.circle(dbg, (icx, icy), ir_, _CLR_BEST, 2)
            cv2.drawMarker(dbg, (icx, icy), _CLR_BEST,
                           cv2.MARKER_CROSS, ir_ // 2, 1, cv2.LINE_AA)

        summary = "  ".join(f"{k}:{v}" for k, v in counts.items() if v) or "no candidates"
        cv2.putText(dbg, summary, (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 255, 255), 1, cv2.LINE_AA)

        self._debug_frame = dbg
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
        valid = np.isfinite(patch) & (patch > 0)
        if int(np.count_nonzero(valid)) < max(1, int(patch.size * _MIN_VALID_DEPTH_FRACTION)):
            return None

        return float(np.median(patch[valid]))

    def _sample_depth_ring(
        self,
        depth_mm: np.ndarray,
        cx: float,
        cy: float,
        radius_px: float,
        r_low_fraction: float,
        r_high_fraction: float,
    ) -> Optional[float]:
        """Median depth in an annular region [r_low, r_high] × radius_px."""
        r_out = max(2, int(radius_px * r_high_fraction))
        r_in  = max(1, int(radius_px * r_low_fraction))
        h, w = depth_mm.shape[:2]
        cx_i, cy_i = int(round(cx)), int(round(cy))
        x0 = max(0, cx_i - r_out);  x1 = min(w, cx_i + r_out + 1)
        y0 = max(0, cy_i - r_out);  y1 = min(h, cy_i + r_out + 1)
        if x1 <= x0 or y1 <= y0:
            return None

        patch = depth_mm[y0:y1, x0:x1].astype(np.float32, copy=False)
        ys = np.arange(y0, y1, dtype=np.float32) - cy
        xs = np.arange(x0, x1, dtype=np.float32) - cx
        XX, YY = np.meshgrid(xs, ys)
        d2 = XX * XX + YY * YY
        ring = (d2 >= r_in * r_in) & (d2 <= r_out * r_out)
        valid = ring & np.isfinite(patch) & (patch > 0)
        if int(np.count_nonzero(valid)) < max(1, int(np.count_nonzero(ring) * _MIN_VALID_DEPTH_FRACTION)):
            return None

        return float(np.median(patch[valid]))

    # ------------------------------------------------------------------
    # EKF factory
    # ------------------------------------------------------------------

    def _make_ekf(self, dt: float) -> _EKF:
        # Process noise: position continuity tight, velocity can change fast.
        Q = np.diag([
            1.0,  1.0,  1.0,     # position (mm²)
            50.0, 50.0, 50.0,    # velocity (mm²/s²)
        ])

        # Measurement noise: [u_px, v_px, z_mm]
        # Pixel centre uncertainty ~1–2 px; depth ~2–3 mm.
        R = np.diag([2.0, 2.0, 9.0])

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
        p = _ap.ArgumentParser(
            description="Ball tracker terminal test — prints 3-D position live."
        )
        p.add_argument("--device-id",   type=int, default=0)
        p.add_argument("--depth-mode",  choices=sorted(_DEPTH_MODES), default="nfov_unbinned")
        p.add_argument("--fps",         choices=sorted(_FPS_VALUES, key=int), default="30")
        p.add_argument("--depth-engine-display", default=_DEPTH_ENGINE_DISPLAY)
        p.add_argument("--ball-radius-min",  type=float, default=25.0)
        p.add_argument("--ball-radius-max",  type=float, default=30.0)
        p.add_argument("--ir-thresh",        type=float, default=_IR_THRESH_FRACTION,
                       help="IR threshold fraction 0–1 (default %(default).2f)")
        return p.parse_args()

    _args = _parse()

    if _args.depth_engine_display:
        _set_display(_args.depth_engine_display, "depth engine")

    _n = _connected_device_count()
    if _n <= _args.device_id:
        print(f"No Kinect at index {_args.device_id} ({_n} found).", file=_sys.stderr)
        raise SystemExit(1)

    # IR + depth only — no colour camera needed.
    _config = _Config(
        depth_mode=_DEPTH_MODES[_args.depth_mode],
        camera_fps=_FPS_VALUES[_args.fps],
        synchronized_images_only=False,
    )
    _k4a = _PyK4A(config=_config, device_id=_args.device_id)

    try:
        _k4a.start()
        _tracker = BallTracker.from_k4a_calibration(
            _k4a.calibration,
            ball_radius_min_mm=_args.ball_radius_min,
            ball_radius_max_mm=_args.ball_radius_max,
            ir_thresh_fraction=_args.ir_thresh,
        )
        print(
            f"Tracker ready.  "
            f"IR thresh={_tracker.ir_thresh_fraction:.2f}  "
            f"detecting dark ball"
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

            _pos, _det = _tracker.update(_ir, _depth)

            if _pos is not None:
                _x, _y, _z = _pos
                _r = _det.radius_mm if _det else float("nan")
                _line = (
                    f"\r DETECTED  "
                    f"X={_x:8.1f}  Y={_y:8.1f}  Z={_z:8.1f} mm  "
                    f"r={_r:5.1f} mm   "
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
