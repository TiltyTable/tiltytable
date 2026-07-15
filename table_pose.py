#!/usr/bin/env python3
"""
Continuous tracking of the table's pose relative to the (tripod-fixed) Azure
Kinect, using 5 retroreflective markers mounted on the vertical faces of
~2 inch tall walls built around two adjacent edges of the table — not flat
tape on the table's top surface.

The table sits on a 3-leg Stewart platform and tilts/heaves during normal
operation, so the camera-to-table transform is *not* fixed for a session —
it changes continuously as the platform moves. Because the markers are on
raised wall faces (above the play surface, facing outward/upward) they stay
visible to the camera across the platform's tilt range, which is what makes
*continuous* re-tracking (rather than a one-time calibration) practical.

Marker layout (world frame = the table's own body-fixed frame; long wall is
the X axis, short wall is the Y axis, meeting at the origin corner). Each
marker is a physical disc mounted flush against the *outside* face of a foam
wall, so its center sits r = (wall thickness + marker radius) off the nominal
wall line, perpendicular to that wall — the origin marker touches both walls
and so is offset on both axes, split evenly between them (45 degrees):

                                              y2 (r, measured, h)
                                                |
                                              y1 (r, measured, h)
                                                |
    x2 (-measured, -r, h) ------ x1 (-measured, -r, h) ------ origin (r/sqrt2, -r/sqrt2, h)

h = MARKER_HEIGHT_MM (~50.8mm, 2 inch wall height). x1/x2/y1/y2's along-wall
distance from origin is hardcoded directly in _build_geometry() from measured
tape-measure readings (not derived from an overall wall length or assumed
fractional spacing) — update those literals there when remeasured. r =
MARKER_MOUNT_RADIUS_MM + WALL_THICKNESS_MM. TABLE_LONG_SIDE_MM/
TABLE_SHORT_SIDE_MM are a separate measurement (the table's overall play-
surface extent) used only by world_to_cell()'s grid mapping below, not by
the marker geometry. marker_height_mm/marker_mount_radius_mm/
wall_thickness_mm/max_marker_radius_mm are set from config.json's
"table_pose" section via configure_table_geometry() at startup; everything
else derives from them.

World-frame convention: Z=0 is defined at the table's own play surface, not
at the markers — the markers sit at Z=h, i.e. h above the table, since
they're mounted on top of the walls. +X runs from origin *away* from the
x-wall markers (x1/x2 sit at negative X), +Y runs from origin *toward*
the y-wall markers (y1/y2 sit at positive Y), +Z is "up" out of the table
via the right-hand rule (X cross Y). Getting the physical orientation
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
# wall-mounted markers sit relative to the table's own origin corner.
#
# Each marker is a physical disc of radius MARKER_MOUNT_RADIUS_MM mounted
# flush against the *outside* face of a foam wall of thickness
# WALL_THICKNESS_MM built up around the table's edge, so its center isn't on
# the nominal wall/corner line — it's pushed out by r = (wall thickness +
# marker radius), perpendicular to whichever wall(s) it's mounted against:
#   - x1/x2 sit only against the X wall (whose outward normal is -Y, since
#     the table's short arm runs toward +Y) -> shifted -r in Y.
#   - y1/y2 sit only against the Y wall (whose outward normal is +X, since
#     the table's long arm runs toward -X) -> shifted +r in X.
#   - origin sits at the corner shared by both walls, at 45 degrees, so its
#     total displacement is r but split evenly between the two axes:
#     (r/sqrt(2) in X, -r/sqrt(2) in Y), i.e. each component is sqrt(r^2/2).
# ---------------------------------------------------------------------------
def _build_geometry(
    marker_height_mm: float,
    marker_mount_radius_mm: float,
    wall_thickness_mm: float,
):
    """Derive the named marker world points (and the lookup tables built from
    them) from the physical measurements. Split out so the geometry can be
    reconfigured at startup from config.json via configure_table_geometry()
    instead of only ever matching the module-load-time defaults below."""
    IN_TO_MM = 25.4
    r = marker_mount_radius_mm + wall_thickness_mm
    world_points: dict[str, tuple[float, float, float]] = {
        "origin": (0.625 * IN_TO_MM, -0.625 * IN_TO_MM, marker_height_mm),   # 5/8"
        "x1": (-10.9375 * IN_TO_MM, -r, marker_height_mm),                   # 10 15/16"
        "x2": (-21.75 * IN_TO_MM, -r, marker_height_mm),                    # 21 3/4"
        "y1": (r, 8.375 * IN_TO_MM, marker_height_mm),                      # 8 3/8"
        "y2": (r, 16.5625 * IN_TO_MM, marker_height_mm),                   # 16 9/16"
    }
    names = list(world_points.keys())
    pts = np.array([world_points[name] for name in names], dtype=np.float64)
    dist_matrix = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=-1)
    return world_points, names, pts, dist_matrix


MARKER_HEIGHT_MM = 50.8  # 2 inch wall height where markers are mounted, above the table (Z=0) surface

MARKER_MOUNT_RADIUS_MM = 12.7  # 0.5 inch physical marker disc radius
WALL_THICKNESS_MM = 4.7625     # 3/16 inch foam wall thickness

# The table's own physical extent (distinct from where markers happen to be
# mounted along the walls, which are hardcoded measured positions above) —
# used only by world_to_cell() below to normalize a ball position into the
# GRID_ROWS x GRID_COLS grid.
TABLE_LONG_SIDE_MM = 831.85   # measured: 32 3/4 inches
TABLE_SHORT_SIDE_MM = 831.85  # measured: 32 3/4 inches

(
    TABLE_MARKER_WORLD_POINTS,
    _KNOWN_POINT_NAMES,
    _KNOWN_WORLD_PTS,
    _KNOWN_DIST_MATRIX,
) = _build_geometry(MARKER_HEIGHT_MM, MARKER_MOUNT_RADIUS_MM, WALL_THICKNESS_MM)

_EXPECTED_MARKER_COUNT = len(TABLE_MARKER_WORLD_POINTS)


def configure_table_geometry(
    marker_height_mm: Optional[float] = None,
    marker_mount_radius_mm: Optional[float] = None,
    wall_thickness_mm: Optional[float] = None,
    max_marker_radius_mm: Optional[float] = None,
) -> None:
    """Override the physical marker measurements (normally sourced from
    config.json) and rebuild every lookup table derived from them. Intended
    to be called once at startup, before tracking begins. Does not touch
    TABLE_LONG_SIDE_MM/TABLE_SHORT_SIDE_MM (world_to_cell's grid extent) —
    those are independent of marker mounting and edited directly above."""
    global MARKER_HEIGHT_MM, MARKER_MOUNT_RADIUS_MM, WALL_THICKNESS_MM, _MAX_MARKER_RADIUS_MM
    global TABLE_MARKER_WORLD_POINTS, _KNOWN_POINT_NAMES, _KNOWN_WORLD_PTS, _KNOWN_DIST_MATRIX
    global _EXPECTED_MARKER_COUNT

    if marker_height_mm is not None:
        MARKER_HEIGHT_MM = marker_height_mm
    if marker_mount_radius_mm is not None:
        MARKER_MOUNT_RADIUS_MM = marker_mount_radius_mm
    if wall_thickness_mm is not None:
        WALL_THICKNESS_MM = wall_thickness_mm
    if max_marker_radius_mm is not None:
        _MAX_MARKER_RADIUS_MM = max_marker_radius_mm

    (
        TABLE_MARKER_WORLD_POINTS,
        _KNOWN_POINT_NAMES,
        _KNOWN_WORLD_PTS,
        _KNOWN_DIST_MATRIX,
    ) = _build_geometry(MARKER_HEIGHT_MM, MARKER_MOUNT_RADIUS_MM, WALL_THICKNESS_MM)
    _EXPECTED_MARKER_COUNT = len(TABLE_MARKER_WORLD_POINTS)


# ---------------------------------------------------------------------------
# Cell grid — same 12x12 logical grid the LED/servo modules address (see
# .cursor/rules/module-grid-mapping.mdc), overlaid on the physical table so
# ball position can be reported as (row, col) instead of just raw mm.
#
# Grid (0, 0) is the table corner *diagonal* from the marker "origin" corner
# (i.e. the far corner along both walls) -- not the marker origin itself.
# Column increases toward the marker-origin corner along the long/X wall;
# row increases toward the marker-origin corner along the short/Y wall.
# World X runs 0 (origin) to -TABLE_LONG_SIDE_MM (far corner, x2 side); world
# Y runs 0 (origin) to +TABLE_SHORT_SIDE_MM (far corner, y2 side).
# ---------------------------------------------------------------------------
GRID_ROWS = 12
GRID_COLS = 12


def world_to_cell(x_mm: float, y_mm: float) -> tuple[int, int]:
    """Convert a table-frame (world) X/Y position in mm to a (row, col) cell
    index on the GRID_ROWS x GRID_COLS grid. Out-of-bounds positions are
    clamped to the nearest edge cell rather than raising."""
    col_frac = (x_mm + TABLE_LONG_SIDE_MM) / TABLE_LONG_SIDE_MM
    row_frac = (TABLE_SHORT_SIDE_MM - y_mm) / TABLE_SHORT_SIDE_MM
    col = int(np.clip(np.floor(col_frac * GRID_COLS), 0, GRID_COLS - 1))
    row = int(np.clip(np.floor(row_frac * GRID_ROWS), 0, GRID_ROWS - 1))
    return row, col

# Matching tolerances (pairwise-distance-signature match).
_DISTANCE_MATCH_TOL_MM = 50.0        # max per-pair distance error allowed for the best assignment
_DISTANCE_MATCH_AMBIGUITY_MM = 15.0  # runner-up assignment must be at least this much worse

# ---------------------------------------------------------------------------
# Marker detection.  Retroreflective tape returns far more IR than the
# diffuse background, so — unlike ball_tracker.py's *relative*, local-
# background dark threshold — this thresholds the raw 16-bit IR frame
# directly at a high *absolute* count.  The contrast-stretched 8-bit image
# ball_tracker.py builds would clip both the bright background and the
# marker to 255 and lose the distinction entirely.
# ---------------------------------------------------------------------------
_MARKER_IR_MIN_COUNTS = 3800.0  # module default; overridden live via UI slider in practice
_MIN_MARKER_AREA_PX = 4.0
_MAX_MARKER_AREA_PX = 4000.0
_MIN_MARKER_CIRCULARITY = 0.6

# The ball is also IR-reflective (see ball_tracker.py), so a bright, roughly
# circular blob alone isn't enough to tell it apart from a marker — pixel
# area alone is ambiguous too, since it conflates real size with distance
# from the camera. Once we have a depth sample we convert radius_px to a
# physical radius_mm and reject anything bigger than a marker has any
# business being. The ball's radius is 20-40mm (ball_tracker.py); markers
# (retroreflective tape) are much smaller, so this threshold sits well below
# the ball's minimum radius with margin to spare.
_MAX_MARKER_RADIUS_MM = 15.0

_DEPTH_SAMPLE_FRACTION = 0.6
_MIN_VALID_DEPTH_FRACTION = 0.10


# Candidate thresholds reported alongside every detection attempt (success or
# failure) so the operator can tune the live threshold slider from real
# sensor data instead of guessing — pixel counts at each threshold reveal
# where the markers actually separate from the background.
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
class PoseFitAttempt:
    ok: bool
    error: Optional[str]
    debug_frame: Optional[np.ndarray]
    fit: Optional[RigidFitResult]
    matched_points: Optional[dict] = field(default=None)
    diagnostics: Optional[DetectionDiagnostics] = field(default=None)


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
    _CLR_TOO_BIG = (255, 255, 0)  # cyan — too large to be a marker (likely the ball)
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

        # Pixel area alone can't distinguish "small and close" from "big and
        # far", so re-check size in real-world mm now that depth is known —
        # this is what actually keeps the (much larger) ball from being
        # mistaken for a marker.
        radius_mm = radius_px * z_mm / fx
        if radius_mm > _MAX_MARKER_RADIUS_MM:
            cv2.circle(dbg, (int(round(cx)), int(round(cy))), int(round(radius_px)), _CLR_TOO_BIG, 1)
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
# _KNOWN_POINT_NAMES/_KNOWN_WORLD_PTS/_KNOWN_DIST_MATRIX are built above by
# _build_geometry() and kept in sync with TABLE_MARKER_WORLD_POINTS by
# configure_table_geometry().
# ---------------------------------------------------------------------------

def match_points(blobs: list) -> dict:
    """
    Match detected marker blobs (or any object exposing .x_mm/.y_mm/.z_mm) to
    named table marker points ("origin", "x1", "x2", "y1", "y2") using only
    the blobs' relative 3D geometry — no assumption about right angles or
    arm shapes.

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
    marker_ir_min_counts: float = _MARKER_IR_MIN_COUNTS,
) -> PoseFitAttempt:
    """One pose-fit attempt end to end: detect -> match -> fit. Never raises."""
    try:
        blobs, debug_frame, diagnostics = detect_markers(
            ir_uint16, depth_mm, fx, fy, ppx, ppy, ir_min_counts=marker_ir_min_counts,
        )
    except DetectionError as exc:
        return PoseFitAttempt(
            ok=False, error=str(exc), debug_frame=exc.debug_frame, fit=None, diagnostics=exc.diagnostics,
        )

    try:
        matched = match_points(blobs)
    except MatchingError as exc:
        return PoseFitAttempt(
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

    return PoseFitAttempt(
        ok=True, error=None, debug_frame=debug_frame, fit=fit,
        matched_points=matched_points, diagnostics=diagnostics,
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
        marker_ir_min_counts: float = _MARKER_IR_MIN_COUNTS,
        now: Optional[float] = None,
    ) -> PoseFitAttempt:
        attempt = run_pose_fit(ir_uint16, depth_mm, fx, fy, ppx, ppy, marker_ir_min_counts=marker_ir_min_counts)
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
