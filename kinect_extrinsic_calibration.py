#!/usr/bin/env python3
"""
Extrinsic calibration of the Azure Kinect relative to the Stewart-platform
table, using 5 retroreflective markers permanently mounted at fixed, known
positions on the platform's table surface — not a removable fixture.

Calibration is performed by driving the platform to its one reproducible
physical reference pose (cranks-up / max heave / roll=pitch=0 — see
arduino/uim5756pm_stewart's `calibrate` command) and capturing a single frame.
Because the markers never move relative to the table, their relative
geometry is always known in advance and detection/matching is fully
automatic — no per-session measurement or manual placement.

Marker layout (world frame, all Z=0 — markers are flat retroreflective tape
on a square table, side length TABLE_SIDE_LENGTH_MM = l):

    4 (0, l/6) ------ 2 (l/6, l/6) ------ 3 (l/3, l/3)
        |
        |
    0 (0, 0) ---------------------- 1 (l/3, 0)

Point 0 is the table corner arbitrarily defined as the world origin once the
platform reaches its reference pose. Point 1 sits 1/3 of a side length along
one edge from 0. Point 4 sits 1/6 of a side length along the other edge.
Point 2 sits at (l/6, l/6), and point 3 at (l/3, l/3) — both further into
the table interior along the diagonal from 0.

World-frame convention: +X runs from point 0 toward point 1, +Y runs from
point 0 toward point 4, +Z is "up" out of the table via the right-hand rule
(X cross Y). Getting the physical orientation backwards silently mirrors the
fitted pose; the Kabsch reflection-guard in fit_rigid_transform only
corrects the SVD's internal sign ambiguity, it cannot detect a wrong
physical convention.

Note: this layout has more internal distance symmetry than a generic
scattering of points (several pairwise distances coincide, e.g. |0-1|=|1-3|
and |0-4|=|2-4|) — the full 5-point distance signature is still unique (no
exact permutation ties), but the margin between the correct match and the
next-best wrong one is thinner than it would be for a more irregular
layout, so matching may be more sensitive to detection noise.

Point identity is recovered automatically (no operator input) via a
pairwise-distance-signature match: since all 5 points' relative distances are
fixed and known in advance, and this specific layout has no full permutation
symmetry, each detected marker can be matched to its known point by finding
the assignment whose pairwise distances best reproduce the known distance
matrix — no assumption about right angles or arm shapes is needed.

Usage:
    attempt = run_calibration(ir_uint16, depth_mm, fx, fy, ppx, ppy)
    if attempt.ok:
        extrinsics = save_extrinsics(path, attempt.fit)
"""

import itertools
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

import camera_geometry

# ---------------------------------------------------------------------------
# Table marker geometry — single source of truth for where the 5 permanent
# markers sit on the table. Measured directly (rounded to whole mm), not
# derived by dividing the side length, to keep the numbers simple and exact.
# ---------------------------------------------------------------------------
TABLE_SIDE_LENGTH_MM = 832.0

TABLE_MARKER_WORLD_POINTS: dict[str, tuple[float, float, float]] = {
    "0": (0.0, 0.0, 0.0),
    "1": (277.0, 0.0, 0.0),
    "2": (139.0, 139.0, 0.0),
    "3": (277.0, 277.0, 0.0),
    "4": (0.0, 139.0, 0.0),
}

_EXPECTED_MARKER_COUNT = len(TABLE_MARKER_WORLD_POINTS)

# Matching tolerances (pairwise-distance-signature match).
_DISTANCE_MATCH_TOL_MM = 30.0        # max per-pair distance error allowed for the best assignment
_DISTANCE_MATCH_AMBIGUITY_MM = 15.0  # runner-up assignment must be at least this much worse

# ---------------------------------------------------------------------------
# Marker detection.  Retroreflective tape returns far more IR than the
# diffuse tabletop, so — unlike ball_tracker.py's *relative*, local-background
# dark threshold — this thresholds the raw 16-bit IR frame directly at a
# high *absolute* count.  The contrast-stretched 8-bit image ball_tracker.py
# builds would clip both the bright table and the marker to 255 and lose the
# distinction entirely.
# ---------------------------------------------------------------------------
_MARKER_IR_MIN_COUNTS = 3600.0  # empirically the point where retroreflective tape pops against the table
_MIN_MARKER_AREA_PX = 4.0
_MAX_MARKER_AREA_PX = 4000.0
_MIN_MARKER_CIRCULARITY = 0.6

_DEPTH_SAMPLE_FRACTION = 0.6
_MIN_VALID_DEPTH_FRACTION = 0.10


# Candidate thresholds reported alongside every detection attempt (success or
# failure) so the operator can tune _MARKER_IR_MIN_COUNTS from real sensor
# data instead of guessing — pixel counts at each threshold reveal where the
# markers actually separate from the table background.
_DIAGNOSTIC_THRESHOLDS = (300.0, 500.0, 800.0, 1000.0, 1500.0, 2000.0, 2500.0, 3000.0, 3500.0, 4000.0)


@dataclass
class DetectionDiagnostics:
    ir_max: float
    threshold_counts: dict[float, int]


class DetectionError(ValueError):
    """Raised when detect_markers doesn't find exactly the expected marker count."""

    def __init__(
        self,
        message: str,
        debug_frame: Optional[np.ndarray] = None,
        diagnostics: Optional[DetectionDiagnostics] = None,
    ):
        super().__init__(message)
        self.debug_frame = debug_frame
        self.diagnostics = diagnostics


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
class RigidFitResult:
    R: np.ndarray
    t: np.ndarray
    residuals_mm: list[float]
    rms_residual_mm: float
    max_residual_mm: float


@dataclass
class CalibrationAttempt:
    ok: bool
    error: Optional[str]
    debug_frame: Optional[np.ndarray]
    fit: Optional[RigidFitResult]
    matched_points: Optional[dict] = field(default=None)
    diagnostics: Optional[DetectionDiagnostics] = field(default=None)


@dataclass
class Extrinsics:
    R: list          # 3x3, JSON-serializable
    t: list          # 3
    timestamp: str
    residuals_mm: list[float]
    rms_residual_mm: float
    max_residual_mm: float

    def apply(self, cam_xyz: tuple[float, float, float]) -> tuple[float, float, float]:
        R = np.array(self.R, dtype=np.float64)
        t = np.array(self.t, dtype=np.float64)
        world = R @ np.array(cam_xyz, dtype=np.float64) + t
        return float(world[0]), float(world[1]), float(world[2])


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_markers(
    ir_uint16: np.ndarray,
    depth_mm: np.ndarray,
    fx: float,
    fy: float,
    ppx: float,
    ppy: float,
    ir_min_counts: float = _MARKER_IR_MIN_COUNTS,
) -> tuple[list[MarkerBlob], np.ndarray, DetectionDiagnostics]:
    """
    Find retroreflective marker blobs in a raw 16-bit IR frame.

    Returns (blobs, debug_frame_bgr, diagnostics).  Raises DetectionError
    (carrying the debug frame and diagnostics so a partial/failed detection
    can still be shown to the operator) if the count isn't exactly
    _EXPECTED_MARKER_COUNT.
    """
    diagnostics = DetectionDiagnostics(
        ir_max=float(ir_uint16.max()) if ir_uint16.size else 0.0,
        threshold_counts={
            t: int(np.count_nonzero(ir_uint16 >= t)) for t in _DIAGNOSTIC_THRESHOLDS
        },
    )

    bright = (ir_uint16 >= ir_min_counts).astype(np.uint8) * 255

    ir_max_disp = max(1.0, diagnostics.ir_max)
    ir_disp = np.clip(ir_uint16.astype(np.float32) / ir_max_disp * 255.0, 0, 255).astype(np.uint8)
    dbg = cv2.cvtColor(ir_disp, cv2.COLOR_GRAY2BGR)

    _CLR_AREA = (0, 80, 200)     # red — failed area filter
    _CLR_SHAPE = (200, 0, 200)   # magenta — failed circularity
    _CLR_DEPTH = (0, 165, 255)   # orange — failed depth sample
    _CLR_OK = (0, 200, 80)       # green — accepted

    blobs: list[MarkerBlob] = []
    contours, _ = cv2.findContours(bright, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        area = float(cv2.contourArea(c))
        if area < _MIN_MARKER_AREA_PX or area > _MAX_MARKER_AREA_PX:
            cv2.drawContours(dbg, [c], -1, _CLR_AREA, 1)
            continue

        perimeter = float(cv2.arcLength(c, closed=True))
        if perimeter < 1.0:
            continue

        circularity = 4.0 * np.pi * area / (perimeter * perimeter)
        if circularity < _MIN_MARKER_CIRCULARITY:
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
            depth_mm, cx, cy, radius_px, _DEPTH_SAMPLE_FRACTION, _MIN_VALID_DEPTH_FRACTION,
        )
        if z_mm is None:
            z_mm = camera_geometry.sample_depth_ring(
                depth_mm, cx, cy, radius_px, 1.0, 1.6, _MIN_VALID_DEPTH_FRACTION,
            )
        if z_mm is None:
            cv2.circle(dbg, (int(round(cx)), int(round(cy))), int(round(radius_px)), _CLR_DEPTH, 1)
            continue

        x_mm, y_mm, _ = camera_geometry.unproject_pixel(cx, cy, z_mm, fx, fy, ppx, ppy)
        blobs.append(MarkerBlob(cx=cx, cy=cy, radius_px=radius_px, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm))
        cv2.circle(dbg, (int(round(cx)), int(round(cy))), int(round(radius_px)), _CLR_OK, 2)

    if len(blobs) != _EXPECTED_MARKER_COUNT:
        raise DetectionError(
            f"expected {_EXPECTED_MARKER_COUNT} retroreflective markers, found {len(blobs)}",
            debug_frame=dbg,
            diagnostics=diagnostics,
        )

    return blobs, dbg, diagnostics


# ---------------------------------------------------------------------------
# Point-identity matching — pure, camera-free, independently unit-testable.
# ---------------------------------------------------------------------------

_KNOWN_POINT_NAMES = list(TABLE_MARKER_WORLD_POINTS.keys())
_KNOWN_WORLD_PTS = np.array([TABLE_MARKER_WORLD_POINTS[name] for name in _KNOWN_POINT_NAMES], dtype=np.float64)
_KNOWN_DIST_MATRIX = np.linalg.norm(
    _KNOWN_WORLD_PTS[:, None, :] - _KNOWN_WORLD_PTS[None, :, :], axis=-1
)


def match_points(blobs: list) -> dict:
    """
    Match detected marker blobs (or any object exposing .x_mm/.y_mm/.z_mm) to
    named table marker points ("0" .. "4") using only the blobs' relative 3D
    geometry — no assumption about right angles or arm shapes.

    Tries every assignment of blobs to known points (5! = 120, trivially
    cheap) and picks the one whose pairwise distances best reproduce the
    known distance matrix. Raises MatchingError if the best assignment isn't
    a good match, or isn't clearly better than the runner-up, rather than
    guessing.
    """
    if len(blobs) != _EXPECTED_MARKER_COUNT:
        raise MatchingError(f"expected {_EXPECTED_MARKER_COUNT} markers to match, got {len(blobs)}")

    n = len(blobs)
    points = np.array([[b.x_mm, b.y_mm, b.z_mm] for b in blobs], dtype=np.float64)
    detected_dist = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)

    best_perm = None
    best_cost = float("inf")
    second_best_cost = float("inf")
    for perm in itertools.permutations(range(n)):
        # perm[i] = index of the detected blob assigned to known point i
        permuted = detected_dist[np.ix_(perm, perm)]
        cost = float(np.max(np.abs(permuted - _KNOWN_DIST_MATRIX)))
        if cost < best_cost:
            second_best_cost = best_cost
            best_cost = cost
            best_perm = perm
        elif cost < second_best_cost:
            second_best_cost = cost

    if best_cost > _DISTANCE_MATCH_TOL_MM:
        raise MatchingError(
            f"no assignment matches the known table marker geometry within "
            f"{_DISTANCE_MATCH_TOL_MM:.0f}mm (best max-error {best_cost:.1f}mm)"
        )
    if second_best_cost - best_cost < _DISTANCE_MATCH_AMBIGUITY_MM:
        raise MatchingError(
            f"ambiguous match: best assignment max-error {best_cost:.1f}mm, "
            f"runner-up {second_best_cost:.1f}mm"
        )

    return {_KNOWN_POINT_NAMES[i]: blobs[best_perm[i]] for i in range(n)}


# ---------------------------------------------------------------------------
# Rigid registration (Kabsch)
# ---------------------------------------------------------------------------

def fit_rigid_transform(camera_pts: np.ndarray, world_pts: np.ndarray) -> RigidFitResult:
    """
    Solve for the rigid transform (R, t) mapping camera-frame points to
    world-frame points: world = R @ camera + t.  Classic Kabsch/Umeyama
    solution via SVD, with reflection correction.
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
# Top-level orchestration
# ---------------------------------------------------------------------------

def run_calibration(
    ir_uint16: np.ndarray,
    depth_mm: np.ndarray,
    fx: float,
    fy: float,
    ppx: float,
    ppy: float,
    marker_ir_min_counts: float = _MARKER_IR_MIN_COUNTS,
) -> CalibrationAttempt:
    """One calibration attempt end to end: detect -> match -> fit. Never raises."""
    try:
        blobs, debug_frame, diagnostics = detect_markers(
            ir_uint16, depth_mm, fx, fy, ppx, ppy, ir_min_counts=marker_ir_min_counts,
        )
    except DetectionError as exc:
        return CalibrationAttempt(
            ok=False, error=str(exc), debug_frame=exc.debug_frame, fit=None, diagnostics=exc.diagnostics,
        )

    try:
        matched = match_points(blobs)
    except MatchingError as exc:
        return CalibrationAttempt(
            ok=False, error=str(exc), debug_frame=debug_frame, fit=None, diagnostics=diagnostics,
        )

    point_ids = sorted(matched.keys())
    camera_pts = np.array([[matched[pid].x_mm, matched[pid].y_mm, matched[pid].z_mm] for pid in point_ids])
    world_pts = np.array([TABLE_MARKER_WORLD_POINTS[pid] for pid in point_ids])

    fit = fit_rigid_transform(camera_pts, world_pts)

    matched_points = {
        pid: {
            "camera": camera_pts[i].tolist(),
            "world": world_pts[i].tolist(),
            "residual_mm": fit.residuals_mm[i],
        }
        for i, pid in enumerate(point_ids)
    }

    return CalibrationAttempt(
        ok=True, error=None, debug_frame=debug_frame, fit=fit,
        matched_points=matched_points, diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_extrinsics(path) -> Optional[Extrinsics]:
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not load {path}: {exc}", file=sys.stderr)
        return None
    try:
        return Extrinsics(
            R=data["R"],
            t=data["t"],
            timestamp=data["timestamp"],
            residuals_mm=data.get("residuals_mm", []),
            rms_residual_mm=data.get("rms_residual_mm", 0.0),
            max_residual_mm=data.get("max_residual_mm", 0.0),
        )
    except (KeyError, TypeError) as exc:
        print(f"Malformed extrinsics file {path}: {exc}", file=sys.stderr)
        return None


def save_extrinsics(path, fit: RigidFitResult) -> Extrinsics:
    path = Path(path)
    extrinsics = Extrinsics(
        R=fit.R.tolist(),
        t=fit.t.tolist(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        residuals_mm=fit.residuals_mm,
        rms_residual_mm=fit.rms_residual_mm,
        max_residual_mm=fit.max_residual_mm,
    )
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(extrinsics), indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)
    return extrinsics
