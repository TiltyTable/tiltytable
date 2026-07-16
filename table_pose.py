#!/usr/bin/env python3
"""
Continuous tracking of the table's pose relative to the (tripod-fixed) Azure
Kinect, using six retroreflective fiducial markers: one at each table
corner, plus one additional marker on each of two adjacent edges.

The table sits on a 3-leg Stewart platform and tilts/heaves during normal
operation, so the camera-to-table transform is *not* fixed for a session —
it changes continuously as the platform moves. Mount the fiducials where they
remain visible across the platform's tilt range; that is what makes
*continuous* re-tracking (rather than a one-time calibration) practical.

Marker layout (world frame = the table's own body-fixed frame) uses
``corner_origin``, ``corner_x``, ``corner_xy``, and ``corner_y`` at the four
play-surface corners, plus ``edge_x`` and ``edge_y`` on adjacent edges. The named
corner coordinates are both the pose-fit geometry and the authoritative grid
boundary: ``world_to_cell()`` derives its axes directly from them, so it does
not need independently configured table-length values. Update the measured
corner coordinates in ``TableGeometry._rebuild()`` if the physical build
changes. Markers remain at ``marker_height_mm`` above the play surface.

World-frame convention: Z=0 is defined at the table's own play surface, not
at the markers — the markers sit at Z=h. +X runs from ``corner_origin`` to
``corner_x`` (negative X in the current layout), +Y runs from
``corner_origin`` to ``corner_y`` (positive Y), and +Z is "up" out of the
table via the right-hand rule (X cross Y). Getting the physical orientation
backwards silently mirrors the fitted pose; the Kabsch reflection-guard in
fit_rigid_transform only corrects the SVD's internal sign ambiguity, it
cannot detect a wrong physical convention.

Point identity is recovered automatically (no operator input) via a
pairwise-distance-signature match: since all 5 points' relative distances are
fixed and known in advance, each detected marker can be matched to its known
point by finding the assignment whose pairwise distances best reproduce the
known distance matrix — no assumption about right angles or arm shapes is
needed.

Because `fit_rigid_transform` fits directly against the markers' known
positions in the table's own body-fixed frame, running it repeatedly (not
just once) gives the *current* camera-to-table pose at all times, correctly
reflecting whatever tilt/heave the platform is at right now. This also means
there is no "reference pose" precondition anymore — pose tracking works
regardless of platform state, since the table's local frame is defined by
the markers themselves.

Usage (single attempt):
    attempt = run_pose_fit(ir_uint16, depth_mm, fx, fy, ppx, ppy)

Usage (continuous tracking, called repeatedly e.g. every few camera frames):
    tracker = TablePoseTracker()
    tracker.update(ir_uint16, depth_mm, fx, fy, ppx, ppy)
    world_xyz, stale, age_s = tracker.apply(ball_cam_xyz)
    # `stale` is True iff the most recent update() attempt failed (e.g. a
    # marker was briefly occluded) — the tracker holds the last successful
    # R/t rather than dropping position_world for one bad frame, but callers
    # can see `stale`/`age_s` and decide whether to trust it.
"""

import itertools
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

import camera_geometry


# ---------------------------------------------------------------------------
# Table marker geometry — single source of truth for where the 5 permanent
# wall-mounted markers sit relative to the table's own origin corner, and
# the point-identity matching that recognizes them in a detected blob list.
# ---------------------------------------------------------------------------

class TableGeometry:
    """Physical layout of the 6 named table fiducials, plus the
    pairwise-distance-signature matching that recovers point identity.

    The four named corner markers define the play-surface rectangle. The two
    edge markers are not grid boundaries; together they break the square's
    mirror ambiguity for image-space marker matching.
    """

    # Matching tolerances (pairwise-distance-signature match).
    DISTANCE_MATCH_TOL_MM = 50.0        # max per-pair distance error allowed for the best assignment
    DISTANCE_MATCH_AMBIGUITY_MM = 15.0  # runner-up assignment must be at least this much worse
    REQUIRED_POINT_NAMES = (
        "corner_origin", "corner_x", "corner_xy", "corner_y", "edge_x", "edge_y",
    )

    def __init__(
        self,
        marker_height_mm: float = 50.8,
        marker_world_points: Optional[dict] = None,
    ):
        self.marker_height_mm = marker_height_mm
        self.marker_world_points = marker_world_points
        self._rebuild()

    def _rebuild(self) -> None:
        """Recompute the named world points and the lookup tables built from
        them. Called once at construction and again by reconfigure()."""
        h = self.marker_height_mm
        # Default marker-center locations for the current 775 mm square
        # table. Deployments should override these through config.json's
        # table_pose.marker_world_points setting.
        side_mm = 775.0
        default_points = {
            "corner_origin": (0.0, 0.0, h),
            "corner_x": (-side_mm, 0.0, h),
            "corner_xy": (-side_mm, side_mm, h),
            "corner_y": (0.0, side_mm, h),
            "edge_x": (-side_mm / 3.0, 0.0, h),
            "edge_y": (0.0, side_mm * 0.6, h),
        }
        self.world_points = self._validated_world_points(
            default_points if self.marker_world_points is None else self.marker_world_points
        )
        self.point_names = list(self.world_points.keys())
        pts = np.array([self.world_points[name] for name in self.point_names], dtype=np.float64)
        self.dist_matrix = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=-1)
        self.expected_marker_count = len(self.world_points)

    def reconfigure(
        self,
        marker_height_mm: Optional[float] = None,
        marker_world_points: Optional[dict] = None,
    ) -> None:
        """Override the physical marker measurements (normally sourced from
        config.json) and rebuild every lookup table derived from them.
        Intended to be called once at startup, before tracking begins."""
        if marker_height_mm is not None:
            self.marker_height_mm = marker_height_mm
        if marker_world_points is not None:
            self.marker_world_points = marker_world_points
        self._rebuild()

    @classmethod
    def _validated_world_points(cls, points: dict) -> dict[str, tuple[float, float, float]]:
        """Validate and normalize the six named marker centers from config."""
        if not isinstance(points, dict) or set(points) != set(cls.REQUIRED_POINT_NAMES):
            raise ValueError(
                "marker_world_points must contain exactly: "
                + ", ".join(cls.REQUIRED_POINT_NAMES)
            )
        normalized = {}
        for name in cls.REQUIRED_POINT_NAMES:
            point = points[name]
            if not isinstance(point, (list, tuple)) or len(point) != 3:
                raise ValueError(f"marker_world_points.{name} must be an [x_mm, y_mm, z_mm] triple")
            try:
                xyz = tuple(float(value) for value in point)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"marker_world_points.{name} must contain numeric coordinates") from exc
            if not np.all(np.isfinite(xyz)):
                raise ValueError(f"marker_world_points.{name} must contain finite coordinates")
            normalized[name] = xyz

        origin = np.asarray(normalized["corner_origin"][:2])
        x_corner = np.asarray(normalized["corner_x"][:2])
        y_corner = np.asarray(normalized["corner_y"][:2])
        x_axis = x_corner - origin
        y_axis = y_corner - origin
        area = x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0]
        if abs(float(area)) < 1e-6:
            raise ValueError("corner_origin, corner_x, and corner_y must define a non-degenerate table plane")
        return normalized

    def select_inliers(
        self, blobs: list["MarkerBlob"], R: np.ndarray, t: np.ndarray,
    ) -> list["MarkerBlob"]:
        """Given more candidate blobs than markers, greedily pick the
        closest blob (in camera-frame mm) to each known marker's position as
        predicted by the prior pose (R, t maps camera -> world, so the
        inverse camera prediction is R.T @ (world - t)). Returns exactly
        `expected_marker_count` blobs, or fewer if there aren't enough
        candidates left to pick from.

        This is a lightweight, prior-pose-based stand-in for RANSAC: rather
        than failing the whole frame when a stray reflection also clears the
        IR threshold, it uses the last successfully fit pose to predict
        where each marker should be right now and discards whatever doesn't
        match. Final point *identity* is still left to match() below — this
        step only decides which candidates are worth handing to it.
        """
        predicted_cam = {
            name: R.T @ (np.array(world_pt, dtype=np.float64) - t)
            for name, world_pt in self.world_points.items()
        }
        remaining_blobs = list(blobs)
        remaining_names = list(predicted_cam.keys())
        selected: list["MarkerBlob"] = []

        while remaining_names and remaining_blobs:
            best_dist = None
            best_name = None
            best_blob = None
            for name in remaining_names:
                pred = predicted_cam[name]
                for blob in remaining_blobs:
                    cam_pt = np.array([blob.x_mm, blob.y_mm, blob.z_mm])
                    dist = float(np.linalg.norm(pred - cam_pt))
                    if best_dist is None or dist < best_dist:
                        best_dist, best_name, best_blob = dist, name, blob
            selected.append(best_blob)
            remaining_names.remove(best_name)
            remaining_blobs.remove(best_blob)

        return selected

    def match(self, blobs: list) -> dict:
        """
        Match detected marker blobs (or any object exposing .x_mm/.y_mm/.z_mm)
        to named table fiducial points using
        only the blobs' relative 3D geometry — no assumption about right
        angles or arm shapes.

        Tries every assignment of blobs to known points (5! = 120, trivially
        cheap) and picks the one whose pairwise distances best reproduce the
        known distance matrix. Raises MatchingError if the best assignment
        isn't a good match, or isn't clearly better than the runner-up,
        rather than guessing.
        """
        if len(blobs) != self.expected_marker_count:
            raise MatchingError(f"expected {self.expected_marker_count} markers to match, got {len(blobs)}")

        n = len(blobs)
        points = np.array([[b.x_mm, b.y_mm, b.z_mm] for b in blobs], dtype=np.float64)
        detected_dist = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)

        best_perm = None
        best_cost = float("inf")
        second_best_cost = float("inf")
        for perm in itertools.permutations(range(n)):
            # perm[i] = index of the detected blob assigned to known point i
            permuted = detected_dist[np.ix_(perm, perm)]
            cost = float(np.max(np.abs(permuted - self.dist_matrix)))
            if cost < best_cost:
                second_best_cost = best_cost
                best_cost = cost
                best_perm = perm
            elif cost < second_best_cost:
                second_best_cost = cost

        if best_cost > self.DISTANCE_MATCH_TOL_MM:
            raise MatchingError(
                f"no assignment matches the known table marker geometry within "
                f"{self.DISTANCE_MATCH_TOL_MM:.0f}mm (best max-error {best_cost:.1f}mm)"
            )
        if second_best_cost - best_cost < self.DISTANCE_MATCH_AMBIGUITY_MM:
            raise MatchingError(
                f"ambiguous match: best assignment max-error {best_cost:.1f}mm, "
                f"runner-up {second_best_cost:.1f}mm"
            )

        return {self.point_names[i]: blobs[best_perm[i]] for i in range(n)}


_GEOMETRY = TableGeometry()


def configure_table_geometry(
    marker_height_mm: Optional[float] = None,
    marker_world_points: Optional[dict] = None,
    max_marker_radius_mm: Optional[float] = None,
) -> None:
    """Override the physical marker measurements (normally sourced from
    config.json) and rebuild every lookup table derived from them. Intended
    to be called once at startup, before tracking begins."""
    _GEOMETRY.reconfigure(
        marker_height_mm=marker_height_mm,
        marker_world_points=marker_world_points,
    )
    if max_marker_radius_mm is not None:
        _DETECTOR.max_marker_radius_mm = max_marker_radius_mm


# ---------------------------------------------------------------------------
# Cell grid — same 12x12 logical game grid, overlaid on the physical table so
# ball position can be reported as (row, col) instead of just raw mm.
#
# Grid (0, 0) is ``corner_xy``, diagonal from ``corner_origin``. Column and
# row increase toward ``corner_origin``. The corner fiducials supply both
# grid axes and their extents.
# ---------------------------------------------------------------------------
GRID_ROWS = 12
GRID_COLS = 12


def world_to_cell(x_mm: float, y_mm: float) -> tuple[int, int]:
    """Convert a table-frame (world) X/Y position in mm to a (row, col) cell
    index on the GRID_ROWS x GRID_COLS grid. Out-of-bounds positions are
    clamped to the nearest edge cell rather than raising."""
    origin = np.asarray(_GEOMETRY.world_points["corner_origin"][:2])
    x_corner = np.asarray(_GEOMETRY.world_points["corner_x"][:2])
    y_corner = np.asarray(_GEOMETRY.world_points["corner_y"][:2])
    position = np.asarray((x_mm, y_mm))
    x_axis = origin - x_corner
    y_axis = origin - y_corner
    col_frac = float(np.dot(position - x_corner, x_axis) / np.dot(x_axis, x_axis))
    row_frac = float(np.dot(position - y_corner, y_axis) / np.dot(y_axis, y_axis))
    col = int(np.clip(np.floor(col_frac * GRID_COLS), 0, GRID_COLS - 1))
    row = int(np.clip(np.floor(row_frac * GRID_ROWS), 0, GRID_ROWS - 1))
    return row, col


class DetectionError(ValueError):
    """Raised when detect_markers doesn't find at least the expected marker count."""

    def __init__(
        self,
        message: str,
        debug_frame: Optional[np.ndarray] = None,
    ):
        super().__init__(message)
        self.debug_frame = debug_frame


class MatchingError(ValueError):
    """Raised when detected marker blobs can't be unambiguously matched to table points."""


@dataclass
class MarkerBlob:
    """A detected retroreflective marker blob."""
    cx: float
    cy: float
    radius_px: float
    x_mm: float   # camera-frame 3D (right)
    y_mm: float   # camera-frame 3D (down)
    z_mm: float   # camera-frame 3D (depth)


@dataclass
class ImageHomographyFitResult:
    """Image-space fit from normalized table coordinates to IR pixels."""
    H_table_to_image: np.ndarray
    residuals_px: list[float]
    rms_residual_px: float
    max_residual_px: float


class ImageCellTracker:
    """Tracks table cells from fiducial image positions, without using
    depth-derived marker X/Y/Z coordinates for the fit.

    Corner marker centers are inset diagonally from the real surface corners.
    Their normalized coordinates encode that inset, so the homography maps
    directly into the full table's [0, 1] x [0, 1] cell space. The ball is
    intentionally mapped onto that marker plane, as configured for this
    tracker; no ball depth is used for cell selection.
    """

    MAX_REPROJECTION_ERROR_PX = 20.0
    AMBIGUITY_MARGIN_PX = 3.0

    def __init__(
        self,
        marker_world_points: dict,
    ):
        self.world_points = TableGeometry._validated_world_points(marker_world_points)
        self.table_points = {name: point[:2] for name, point in self.world_points.items()}
        self.point_names = list(self.table_points)
        self.H_table_to_image: Optional[np.ndarray] = None
        self.last_fit: Optional[ImageHomographyFitResult] = None
        self.last_error: Optional[str] = None
        self.last_success_monotonic: Optional[float] = None
        self.last_attempt: Optional[PoseFitAttempt] = None

    @property
    def is_tracking(self) -> bool:
        return self.H_table_to_image is not None

    def age_seconds(self, now: Optional[float] = None) -> Optional[float]:
        if self.last_success_monotonic is None:
            return None
        return (time.monotonic() if now is None else now) - self.last_success_monotonic

    def _match_and_fit(self, blobs: list[MarkerBlob]) -> tuple[dict[str, MarkerBlob], ImageHomographyFitResult]:
        if len(blobs) != len(self.point_names):
            raise MatchingError(f"expected {len(self.point_names)} markers to match, got {len(blobs)}")
        table_pts = np.asarray([self.table_points[name] for name in self.point_names], dtype=np.float32)
        pixels = np.asarray([[blob.cx, blob.cy] for blob in blobs], dtype=np.float32)
        best = second = None
        # The first four named points are the corners. Solve their exact
        # perspective transform with OpenCV's inexpensive four-point routine,
        # then score the two remaining edge points. This avoids running a
        # general homography solve for all 6! assignments every frame.
        corner_pts = table_pts[:4]
        for corner_indices in itertools.permutations(range(len(blobs)), 4):
            H = cv2.getPerspectiveTransform(corner_pts, pixels[list(corner_indices)])
            remaining = [index for index in range(len(blobs)) if index not in corner_indices]
            for edge_indices in itertools.permutations(remaining):
                perm = corner_indices + edge_indices
                observed = pixels[list(perm)]
                projected = cv2.perspectiveTransform(table_pts.reshape(-1, 1, 2), H).reshape(-1, 2)
                residuals = np.linalg.norm(projected - observed, axis=1)
                candidate = (float(np.max(residuals)), perm, H, residuals)
                if best is None or candidate[0] < best[0]:
                    second, best = best, candidate
                elif second is None or candidate[0] < second[0]:
                    second = candidate
        if best is None or best[0] > self.MAX_REPROJECTION_ERROR_PX:
            error = float("inf") if best is None else best[0]
            raise MatchingError(
                f"no image-space assignment matches the fiducial layout within "
                f"{self.MAX_REPROJECTION_ERROR_PX:.0f}px (best max-error {error:.1f}px)"
            )
        if second is not None and second[0] - best[0] < self.AMBIGUITY_MARGIN_PX:
            raise MatchingError(
                f"ambiguous image-space marker match: best max-error {best[0]:.1f}px, "
                f"runner-up {second[0]:.1f}px"
            )
        _, perm, H, residuals = best
        matched = {name: blobs[perm[i]] for i, name in enumerate(self.point_names)}
        fit = ImageHomographyFitResult(
            H_table_to_image=H,
            residuals_px=residuals.tolist(),
            rms_residual_px=float(np.sqrt(np.mean(residuals ** 2))),
            max_residual_px=float(np.max(residuals)),
        )
        return matched, fit

    def update(
        self, ir_uint16: np.ndarray, depth_mm: np.ndarray, fx: float, fy: float,
        ppx: float, ppy: float, marker_ir_threshold: Optional[float] = None,
        now: Optional[float] = None,
    ) -> "PoseFitAttempt":
        try:
            blobs, debug_frame = detect_markers(
                ir_uint16, depth_mm, fx, fy, ppx, ppy, ir_min_counts=marker_ir_threshold,
            )
            matched, fit = self._match_and_fit(blobs)
        except DetectionError as exc:
            attempt = PoseFitAttempt(False, str(exc), exc.debug_frame, None)
        except MatchingError as exc:
            attempt = PoseFitAttempt(False, str(exc), debug_frame if "debug_frame" in locals() else None, None)
        else:
            attempt = PoseFitAttempt(
                True, None, debug_frame, fit,
                matched_points={name: {"pixel": [blob.cx, blob.cy], "residual_px": fit.residuals_px[i]}
                                for i, (name, blob) in enumerate(matched.items())},
            )
            self.H_table_to_image = fit.H_table_to_image
            self.last_fit = fit
            self.last_success_monotonic = time.monotonic() if now is None else now
            self.last_error = None
        if not attempt.ok:
            self.last_error = attempt.error
        self.last_attempt = attempt
        return attempt

    def cell_from_pixel(self, cx: float, cy: float) -> Optional[tuple[int, int]]:
        if self.H_table_to_image is None:
            return None
        H_image_to_table = np.linalg.inv(self.H_table_to_image)
        xy = cv2.perspectiveTransform(np.array([[[cx, cy]]], dtype=np.float32), H_image_to_table)[0, 0]
        origin = np.asarray(self.table_points["corner_origin"])
        x_corner = np.asarray(self.table_points["corner_x"])
        y_corner = np.asarray(self.table_points["corner_y"])
        x_axis = origin - x_corner
        y_axis = origin - y_corner
        col_fraction = float(np.dot(xy - x_corner, x_axis) / np.dot(x_axis, x_axis))
        row_fraction = float(np.dot(xy - y_corner, y_axis) / np.dot(y_axis, y_axis))
        col = int(np.clip(np.floor(col_fraction * GRID_COLS), 0, GRID_COLS - 1))
        row = int(np.clip(np.floor(row_fraction * GRID_ROWS), 0, GRID_ROWS - 1))
        return row, col


@dataclass
class RigidFitResult:
    R: np.ndarray
    t: np.ndarray
    residuals_mm: list[float]
    rms_residual_mm: float
    max_residual_mm: float


@dataclass
class PoseFitAttempt:
    ok: bool
    error: Optional[str]
    debug_frame: Optional[np.ndarray]
    fit: Optional[RigidFitResult]
    matched_points: Optional[dict] = field(default=None)


# ---------------------------------------------------------------------------
# Marker detection.  Retroreflective tape returns far more IR than the
# diffuse background, so — unlike ball_tracker.py's *relative*, local-
# background dark threshold — this thresholds the raw 16-bit IR frame
# directly at a high *absolute* count.  The contrast-stretched 8-bit image
# ball_tracker.py builds would clip both the bright background and the
# marker to 255 and lose the distinction entirely.
#
# Mirrors ball_tracker.py's BallDetector: tuning lives on the instance
# (mutated live via the UI slider), detect() takes just the frame pair.
# ---------------------------------------------------------------------------

class MarkerDetector:
    """Finds retroreflective marker blobs in a raw 16-bit IR frame."""

    MIN_AREA_PX = 4.0
    MAX_AREA_PX = 4000.0
    MIN_CIRCULARITY = 0.6
    DEPTH_SAMPLE_FRACTION = 0.6
    MIN_VALID_DEPTH_FRACTION = 0.10

    def __init__(self, ir_threshold: float = 3800.0, max_marker_radius_mm: float = 15.0):
        # ir_threshold: module default; overridden live via the UI slider in practice.
        self.ir_threshold = ir_threshold
        # The ball is also IR-reflective (see ball_tracker.py), so a bright,
        # roughly circular blob alone isn't enough to tell it apart from a
        # marker — pixel area alone is ambiguous too, since it conflates
        # real size with distance from the camera. Once we have a depth
        # sample we convert radius_px to a physical radius_mm and reject
        # anything bigger than a marker has any business being. The ball's
        # radius is 20-40mm (ball_tracker.py); markers (retroreflective
        # tape) are much smaller, so this threshold sits well below the
        # ball's minimum radius with margin to spare.
        self.max_marker_radius_mm = max_marker_radius_mm

    def detect(
        self,
        ir_uint16: np.ndarray,
        depth_mm: np.ndarray,
        fx: float,
        fy: float,
        ppx: float,
        ppy: float,
        expected_marker_count: int,
        ir_min_counts: Optional[float] = None,
    ) -> tuple[list[MarkerBlob], np.ndarray]:
        """
        Run the detection pipeline on one camera frame.

        Returns (blobs, debug_frame_bgr). Raises DetectionError (carrying
        the debug frame so a partial/failed detection can still be shown to
        the operator) if fewer than `expected_marker_count` blobs are found
        — too few is unrecoverable (some marker just wasn't seen this
        frame). Too many (e.g. a stray reflection also crossed the IR
        threshold) is returned as-is for the caller to resolve, e.g. via
        TableGeometry.select_inliers().
        """
        threshold = self.ir_threshold if ir_min_counts is None else ir_min_counts

        bright = (ir_uint16 >= threshold).astype(np.uint8) * 255

        ir_max = float(ir_uint16.max()) if ir_uint16.size else 0.0
        ir_max_disp = max(1.0, ir_max)
        ir_disp = np.clip(ir_uint16.astype(np.float32) / ir_max_disp * 255.0, 0, 255).astype(np.uint8)
        dbg = cv2.cvtColor(ir_disp, cv2.COLOR_GRAY2BGR)

        _CLR_AREA = (0, 80, 200)     # red — failed area filter
        _CLR_SHAPE = (255, 80, 0)    # blue — failed circularity
        _CLR_DEPTH = (0, 165, 255)   # orange — failed depth sample
        _CLR_TOO_BIG = (255, 255, 0)  # cyan — too large to be a marker (likely the ball)
        _CLR_OK = (255, 0, 255)      # magenta — accepted

        blobs: list[MarkerBlob] = []
        contours, _ = cv2.findContours(bright, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = float(cv2.contourArea(c))
            if area < self.MIN_AREA_PX or area > self.MAX_AREA_PX:
                cv2.drawContours(dbg, [c], -1, _CLR_AREA, 1)
                continue

            perimeter = float(cv2.arcLength(c, closed=True))
            if perimeter < 1.0:
                continue

            circularity = 4.0 * np.pi * area / (perimeter * perimeter)
            if circularity < self.MIN_CIRCULARITY:
                cv2.drawContours(dbg, [c], -1, _CLR_SHAPE, 1)
                continue

            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]
            (_, _), radius_px = cv2.minEnclosingCircle(c)
            radius_px = float(radius_px)

            z_mm = camera_geometry.sample_depth_patch(
                depth_mm, cx, cy, radius_px, self.DEPTH_SAMPLE_FRACTION, self.MIN_VALID_DEPTH_FRACTION,
            )
            if z_mm is None:
                z_mm = camera_geometry.sample_depth_ring(
                    depth_mm, cx, cy, radius_px, 1.0, 1.6, self.MIN_VALID_DEPTH_FRACTION,
                )
            if z_mm is None:
                cv2.circle(dbg, (int(round(cx)), int(round(cy))), int(round(radius_px)), _CLR_DEPTH, 1)
                continue

            # Pixel area alone can't distinguish "small and close" from "big
            # and far", so re-check size in real-world mm now that depth is
            # known — this is what actually keeps the (much larger) ball
            # from being mistaken for a marker.
            radius_mm = radius_px * z_mm / fx
            if radius_mm > self.max_marker_radius_mm:
                cv2.circle(dbg, (int(round(cx)), int(round(cy))), int(round(radius_px)), _CLR_TOO_BIG, 1)
                continue

            x_mm, y_mm, _ = camera_geometry.unproject_pixel(cx, cy, z_mm, fx, fy, ppx, ppy)
            blobs.append(MarkerBlob(cx=cx, cy=cy, radius_px=radius_px, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm))
            cv2.circle(dbg, (int(round(cx)), int(round(cy))), int(round(radius_px)), _CLR_OK, 2)

        if len(blobs) < expected_marker_count:
            raise DetectionError(
                f"expected {expected_marker_count} retroreflective markers, found {len(blobs)}",
                debug_frame=dbg,
            )

        return blobs, dbg


_DETECTOR = MarkerDetector()


def detect_markers(
    ir_uint16: np.ndarray,
    depth_mm: np.ndarray,
    fx: float,
    fy: float,
    ppx: float,
    ppy: float,
    ir_min_counts: Optional[float] = None,
) -> tuple[list[MarkerBlob], np.ndarray]:
    """Free-function wrapper around the module's default MarkerDetector."""
    return _DETECTOR.detect(
        ir_uint16, depth_mm, fx, fy, ppx, ppy,
        expected_marker_count=_GEOMETRY.expected_marker_count, ir_min_counts=ir_min_counts,
    )


def select_inlier_markers(blobs: list[MarkerBlob], R: np.ndarray, t: np.ndarray) -> list[MarkerBlob]:
    """Free-function wrapper around the module's default TableGeometry."""
    return _GEOMETRY.select_inliers(blobs, R, t)


def match_points(blobs: list) -> dict:
    """Free-function wrapper around the module's default TableGeometry."""
    return _GEOMETRY.match(blobs)


# ---------------------------------------------------------------------------
# Rigid registration (Kabsch)
# ---------------------------------------------------------------------------

def fit_rigid_transform(camera_pts: np.ndarray, world_pts: np.ndarray) -> RigidFitResult:
    """
    Solve for the rigid transform (R, t) mapping camera-frame points to
    world-frame (table-frame) points: world = R @ camera + t.  Classic
    Kabsch/Umeyama solution via SVD, with reflection correction.
    """
    camera_pts = np.asarray(camera_pts, dtype=np.float64)
    world_pts = np.asarray(world_pts, dtype=np.float64)

    c_cam = camera_pts.mean(axis=0)
    c_world = world_pts.mean(axis=0)
    A = camera_pts - c_cam
    B = world_pts - c_world

    H = A.T @ B
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    t = c_world - R @ c_cam

    fitted = (R @ camera_pts.T).T + t
    residuals = np.linalg.norm(fitted - world_pts, axis=1)

    return RigidFitResult(
        R=R,
        t=t,
        residuals_mm=residuals.tolist(),
        rms_residual_mm=float(np.sqrt(np.mean(residuals ** 2))),
        max_residual_mm=float(np.max(residuals)),
    )


# ---------------------------------------------------------------------------
# Single-attempt orchestration
# ---------------------------------------------------------------------------

def run_pose_fit(
    ir_uint16: np.ndarray,
    depth_mm: np.ndarray,
    fx: float,
    fy: float,
    ppx: float,
    ppy: float,
    marker_ir_threshold: Optional[float] = None,
    prior_pose: Optional[tuple[np.ndarray, np.ndarray]] = None,
) -> PoseFitAttempt:
    """One pose-fit attempt end to end: detect -> match -> fit. Never raises.

    prior_pose, if given, is the (R, t) of the last successfully fit pose
    (see TablePoseTracker). It's only used when detect_markers() returns
    more than the expected marker count (e.g. a stray reflection also
    cleared the IR threshold) — select_inlier_markers() uses it to predict
    where the real markers should be and discard the extra candidate(s),
    instead of failing the whole frame. Without a prior pose, an over-count
    is treated as a failure.
    """
    try:
        blobs, debug_frame = detect_markers(
            ir_uint16, depth_mm, fx, fy, ppx, ppy, ir_min_counts=marker_ir_threshold,
        )
    except DetectionError as exc:
        return PoseFitAttempt(
            ok=False, error=str(exc), debug_frame=exc.debug_frame, fit=None,
        )

    if len(blobs) > _GEOMETRY.expected_marker_count:
        if prior_pose is None:
            return PoseFitAttempt(
                ok=False,
                error=f"found {len(blobs)} candidate markers (expected {_GEOMETRY.expected_marker_count}) "
                      f"and no prior pose available to disambiguate",
                debug_frame=debug_frame, fit=None,
            )
        prior_R, prior_t = prior_pose
        blobs = select_inlier_markers(blobs, prior_R, prior_t)

    try:
        matched = match_points(blobs)
    except MatchingError as exc:
        return PoseFitAttempt(
            ok=False, error=str(exc), debug_frame=debug_frame, fit=None,
        )

    point_ids = sorted(matched.keys())
    camera_pts = np.array([[matched[pid].x_mm, matched[pid].y_mm, matched[pid].z_mm] for pid in point_ids])
    world_pts = np.array([_GEOMETRY.world_points[pid] for pid in point_ids])

    fit = fit_rigid_transform(camera_pts, world_pts)

    matched_points = {
        pid: {
            "camera": camera_pts[i].tolist(),
            "world": world_pts[i].tolist(),
            "residual_mm": fit.residuals_mm[i],
        }
        for i, pid in enumerate(point_ids)
    }

    return PoseFitAttempt(
        ok=True, error=None, debug_frame=debug_frame, fit=fit,
        matched_points=matched_points,
    )


# ---------------------------------------------------------------------------
# Continuous tracking
# ---------------------------------------------------------------------------

class TablePoseTracker:
    """
    Holds the most recently fitted camera-to-table pose and refreshes it via
    repeated calls to `update()` (intended to be called every few camera
    frames, not necessarily every single one).

    If an update attempt fails (a marker briefly occluded, a bad frame,
    etc.), the previous successful R/t is kept rather than discarded — the
    table doesn't stop existing just because one frame's detection failed —
    but `apply()` reports `stale=True` so callers can decide whether to
    trust the position for that duration.

    Also passes its held pose into run_pose_fit() as `prior_pose` on every
    call, so a frame with a spurious extra IR blob (see
    select_inlier_markers()) gets resolved instead of failing outright —
    only the very first update(), before anything has ever succeeded, has no
    prior pose to fall back on.
    """

    def __init__(self):
        self.R: Optional[np.ndarray] = None
        self.t: Optional[np.ndarray] = None
        self.last_fit: Optional[RigidFitResult] = None
        self.last_attempt: Optional[PoseFitAttempt] = None
        self.last_success_monotonic: Optional[float] = None
        self.last_error: Optional[str] = None

    @property
    def is_tracking(self) -> bool:
        return self.R is not None

    def age_seconds(self, now: Optional[float] = None) -> Optional[float]:
        if self.last_success_monotonic is None:
            return None
        now = time.monotonic() if now is None else now
        return now - self.last_success_monotonic

    def update(
        self,
        ir_uint16: np.ndarray,
        depth_mm: np.ndarray,
        fx: float,
        fy: float,
        ppx: float,
        ppy: float,
        marker_ir_threshold: Optional[float] = None,
        now: Optional[float] = None,
    ) -> PoseFitAttempt:
        prior_pose = (self.R, self.t) if self.R is not None else None
        attempt = run_pose_fit(
            ir_uint16, depth_mm, fx, fy, ppx, ppy,
            marker_ir_threshold=marker_ir_threshold, prior_pose=prior_pose,
        )
        self.last_attempt = attempt
        if attempt.ok:
            self.R = attempt.fit.R
            self.t = attempt.fit.t
            self.last_fit = attempt.fit
            self.last_success_monotonic = time.monotonic() if now is None else now
            self.last_error = None
        else:
            self.last_error = attempt.error
        return attempt

    def apply(
        self, cam_xyz: tuple[float, float, float], now: Optional[float] = None
    ) -> tuple[Optional[tuple[float, float, float]], bool, Optional[float]]:
        """Returns (world_xyz | None, stale, age_s). world_xyz is None only if
        there has never been a successful fit yet."""
        if self.R is None:
            return None, True, None
        world = self.R @ np.array(cam_xyz, dtype=np.float64) + self.t
        age_s = self.age_seconds(now)
        stale = self.last_error is not None
        return (float(world[0]), float(world[1]), float(world[2])), stale, age_s

    def tilt_deg(self, gravity_up_cam: Optional[np.ndarray]) -> Optional[float]:
        """Table tilt in degrees relative to true vertical (0 = level), given
        the camera-frame "up" direction from GravityEstimator. None if there's
        no fitted pose yet or no gravity reading yet."""
        if self.R is None or gravity_up_cam is None:
            return None
        return tilt_deg_from_gravity(self.R, gravity_up_cam)

    def roll_pitch_deg(self, gravity_up_cam: Optional[np.ndarray]) -> Optional[tuple[float, float]]:
        """(roll_deg, pitch_deg) — see roll_pitch_deg_from_gravity(). None if
        there's no fitted pose yet or no gravity reading yet."""
        if self.R is None or gravity_up_cam is None:
            return None
        return roll_pitch_deg_from_gravity(self.R, gravity_up_cam)


# ---------------------------------------------------------------------------
# Tilt relative to gravity — separate from the table-frame pose fit above,
# which only ever reports the table's orientation relative to the camera.
# The camera itself is tripod-mounted at some arbitrary, unknown angle, so
# "how tilted is the table" in any absolute sense requires an independent
# reference: the Azure Kinect's onboard accelerometer, which (while the
# camera is stationary) reads the reaction to gravity -- i.e. it points
# "up", away from the pull of gravity. Because both the fitted table
# orientation and the gravity reading are expressed in the *depth camera's*
# coordinate frame, the camera's own (unknown) mounting angle cancels out
# automatically; no separate camera-to-gravity calibration step is needed.
#
# IMPORTANT: the accelerometer is a separate physical sensor from the depth
# camera and is *not* guaranteed to share its axes -- raw IMU samples must
# first be rotated into the depth camera's frame using the factory extrinsic
# calibration (pyk4a's Calibration.get_extrinsic_parameters(ACCEL, DEPTH));
# see kinect_web_control.py's KinectFrameHub._run_imu(). This module's
# GravityEstimator only does the smoothing, and assumes its input is already
# in the depth camera's frame -- feeding it raw, un-rotated accelerometer
# samples will silently produce a wrong (but plausible-looking) tilt.
#
# NOTE: even after that rotation, the accelerometer's sign convention
# (whether it points "up" or "down") is a hardware detail this module can't
# verify without a real device. `GravityEstimator(sign=...)` exists so that
# can be flipped at runtime if a level table doesn't read ~0 degrees.
# ---------------------------------------------------------------------------

def tilt_deg_from_gravity(R: np.ndarray, gravity_up_cam: np.ndarray) -> float:
    """Angle (degrees) between the table's own Z axis (straight up out of
    the play surface, per the fitted rotation R mapping camera -> world) and
    true vertical, as given by a camera-frame "up" vector from gravity. 0
    means the table is level; 90 would mean it's on its side."""
    table_z_cam = R.T @ np.array([0.0, 0.0, 1.0])
    g = np.asarray(gravity_up_cam, dtype=np.float64)
    g_unit = g / np.linalg.norm(g)
    cos_angle = float(np.clip(np.dot(table_z_cam, g_unit), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_angle)))


def roll_pitch_deg_from_gravity(R: np.ndarray, gravity_up_cam: np.ndarray) -> tuple[float, float]:
    """(roll_deg, pitch_deg) of the table relative to true vertical, given
    the fitted rotation R (camera -> world) and a camera-frame "up" vector
    from gravity. Matches ball_balancer.py's axis convention: pitch is
    rotation about the table's own X axis, roll is rotation about the
    table's own Y axis. (0, 0) means level; sign matches a right-handed
    rotation about each axis (see fit_rigid_transform / TABLE_MARKER_WORLD_POINTS
    for the table's X/Y/Z convention).

    Unlike tilt_deg_from_gravity (which collapses tilt to a single magnitude
    via the angle between two vectors), this expresses gravity's "up"
    direction *in the table's own body-fixed frame* (g_table = R @ gravity),
    then reads off each axis's contribution independently -- the standard
    accelerometer-tilt decomposition, valid for combined roll+pitch as well
    as pure single-axis tilts.
    """
    g = np.asarray(gravity_up_cam, dtype=np.float64)
    g_unit = g / np.linalg.norm(g)
    gx, gy, gz = R @ g_unit
    pitch_deg = float(np.degrees(np.arctan2(-gy, gz)))
    roll_deg = float(np.degrees(np.arctan2(gx, gz)))
    return roll_deg, pitch_deg


class GravityEstimator:
    """Smoothed estimate of the "up" direction (opposite gravity's pull) in
    camera-frame coordinates, built from a stream of raw accelerometer
    samples (e.g. Azure Kinect IMU acc_sample readings). Camera-agnostic and
    pure — reading the IMU itself requires the camera device handle, so that
    loop lives wherever that handle does (see kinect_web_control.py); this
    class only does the smoothing math, independently testable without
    hardware.
    """

    def __init__(self, smoothing: float = 0.02, sign: float = 1.0):
        # smoothing: EMA weight given to each new sample (0..1) -- smaller is
        # slower to respond but rejects vibration/noise better, appropriate
        # for a tripod-mounted camera where "up" should barely move at all.
        self.smoothing = smoothing
        self.sign = sign
        self._up: Optional[np.ndarray] = None

    def add_sample(self, acc_sample: tuple[float, float, float]) -> None:
        v = self.sign * np.array(acc_sample, dtype=np.float64)
        norm = np.linalg.norm(v)
        if norm < 1e-9:
            return
        v = v / norm
        if self._up is None:
            self._up = v
        else:
            blended = (1.0 - self.smoothing) * self._up + self.smoothing * v
            self._up = blended / np.linalg.norm(blended)

    @property
    def up_vector(self) -> Optional[np.ndarray]:
        return self._up


def __getattr__(name):
    """Backward-compatible read-only views onto the module's default
    TableGeometry/MarkerDetector instances, for code (and tests) that still
    reads e.g. table_pose.TABLE_MARKER_WORLD_POINTS as a plain module-level
    value rather than going through configure_table_geometry()."""
    proxies = {
        "TABLE_MARKER_WORLD_POINTS": lambda: _GEOMETRY.world_points,
        "MARKER_HEIGHT_MM": lambda: _GEOMETRY.marker_height_mm,
        "_EXPECTED_MARKER_COUNT": lambda: _GEOMETRY.expected_marker_count,
        "_MARKER_IR_THRESHOLD": lambda: _DETECTOR.ir_threshold,
        "_MAX_MARKER_RADIUS_MM": lambda: _DETECTOR.max_marker_radius_mm,
        "_MIN_MARKER_AREA_PX": lambda: _DETECTOR.MIN_AREA_PX,
        "_MAX_MARKER_AREA_PX": lambda: _DETECTOR.MAX_AREA_PX,
        "_MIN_MARKER_CIRCULARITY": lambda: _DETECTOR.MIN_CIRCULARITY,
    }
    if name in proxies:
        return proxies[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
