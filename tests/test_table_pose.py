from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import table_pose as tp


def _rotation_from_euler_deg(x_deg: float, y_deg: float, z_deg: float) -> np.ndarray:
    x, y, z = np.radians([x_deg, y_deg, z_deg])
    Rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
    Ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    Rz = np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _world_points_array() -> np.ndarray:
    return np.array([tp.TABLE_MARKER_WORLD_POINTS[k] for k in tp.TABLE_MARKER_WORLD_POINTS], dtype=np.float64)


class _FakeBlob:
    def __init__(self, x: float, y: float, z: float):
        self.x_mm = x
        self.y_mm = y
        self.z_mm = z


class KabschFitTests(unittest.TestCase):
    def test_recovers_known_rotation_and_translation(self):
        R0 = _rotation_from_euler_deg(6.0, -4.0, 12.0)
        t0 = np.array([40.0, -25.0, 950.0])

        world_pts = _world_points_array()
        camera_pts = (np.linalg.inv(R0) @ (world_pts - t0).T).T

        fit = tp.fit_rigid_transform(camera_pts, world_pts)

        np.testing.assert_allclose(fit.R, R0, atol=1e-8)
        np.testing.assert_allclose(fit.t, t0, atol=1e-8)
        self.assertLess(fit.rms_residual_mm, 1e-6)
        self.assertLess(fit.max_residual_mm, 1e-6)

    def test_noise_tolerance_stays_bounded(self):
        # NOTE: the current marker layout spans only ~277-416mm (vs. a full
        # ~832mm table wall), so it's a compact, weakly-conditioned point
        # cluster for estimating rotation. A small rotation error is nearly
        # free in residual terms at the cluster itself, but couples with the
        # ~900mm camera-to-table distance to produce a much larger *raw* R/t
        # deviation from ground truth than the fit residual alone suggests.
        # So this test checks the residual (what TablePoseTracker actually
        # exposes as rms/max_residual_mm), not raw R/t vs. ground truth,
        # which would be misleadingly strict for this geometry.
        rng = np.random.default_rng(42)
        R0 = _rotation_from_euler_deg(3.0, 8.0, -5.0)
        t0 = np.array([10.0, 5.0, 900.0])

        world_pts = _world_points_array()
        camera_pts = (np.linalg.inv(R0) @ (world_pts - t0).T).T
        camera_pts += rng.normal(scale=1.0, size=camera_pts.shape)

        fit = tp.fit_rigid_transform(camera_pts, world_pts)

        self.assertLess(fit.rms_residual_mm, 5.0)
        self.assertLess(fit.max_residual_mm, 8.0)

    def test_reflection_guard_on_planar_points(self):
        # All world points share Z = MARKER_HEIGHT_MM (planar, wall-mounted
        # markers all at the same height) -- exactly this layout's
        # configuration, a case where SVD conditioning on the reflection
        # sign matters.
        R0 = _rotation_from_euler_deg(0.0, 0.0, 33.0)
        t0 = np.array([0.0, 0.0, 800.0])

        world_pts = _world_points_array()
        self.assertTrue(np.allclose(world_pts[:, 2], world_pts[0, 2]))
        camera_pts = (np.linalg.inv(R0) @ (world_pts - t0).T).T

        fit = tp.fit_rigid_transform(camera_pts, world_pts)

        self.assertAlmostEqual(np.linalg.det(fit.R), 1.0, places=6)
        np.testing.assert_allclose(fit.R, R0, atol=1e-6)


class MatchPointsTests(unittest.TestCase):
    def _camera_points(self, R0=None, t0=None):
        if R0 is None:
            R0 = _rotation_from_euler_deg(5.0, -10.0, 20.0)
        if t0 is None:
            t0 = np.array([15.0, -8.0, 870.0])
        world_pts = _world_points_array()
        camera_pts = (np.linalg.inv(R0) @ (world_pts - t0).T).T
        return camera_pts

    def test_recovers_correct_assignment_regardless_of_input_order(self):
        camera_pts = self._camera_points()
        blobs = [_FakeBlob(*p) for p in camera_pts]

        # Shuffle blobs so the matcher can't rely on input order, then verify
        # each name's matched blob actually corresponds to that name's known
        # camera-frame point (not just that all 5 names showed up).
        order = list(range(len(blobs)))
        random.Random(7).shuffle(order)
        shuffled = [blobs[i] for i in order]

        matched = tp.match_points(shuffled)

        self.assertEqual(set(matched.keys()), set(tp.TABLE_MARKER_WORLD_POINTS.keys()))
        for i, name in enumerate(tp.TABLE_MARKER_WORLD_POINTS.keys()):
            got = matched[name]
            self.assertAlmostEqual(got.x_mm, camera_pts[i][0], places=6)
            self.assertAlmostEqual(got.y_mm, camera_pts[i][1], places=6)
            self.assertAlmostEqual(got.z_mm, camera_pts[i][2], places=6)

    def test_various_orientations_still_match(self):
        for z_deg in (0.0, 45.0, 90.0, 135.0, 200.0):
            R0 = _rotation_from_euler_deg(2.0, -3.0, z_deg)
            camera_pts = self._camera_points(R0=R0, t0=np.array([0.0, 0.0, 900.0]))
            blobs = [_FakeBlob(*p) for p in camera_pts]
            matched = tp.match_points(blobs)
            self.assertEqual(set(matched.keys()), set(tp.TABLE_MARKER_WORLD_POINTS.keys()))

    def test_noise_within_tolerance_still_matches(self):
        rng = np.random.default_rng(3)
        camera_pts = self._camera_points()
        camera_pts = camera_pts + rng.normal(scale=2.0, size=camera_pts.shape)
        blobs = [_FakeBlob(*p) for p in camera_pts]
        matched = tp.match_points(blobs)
        self.assertEqual(set(matched.keys()), set(tp.TABLE_MARKER_WORLD_POINTS.keys()))

    def test_non_matching_geometry_raises(self):
        # An arbitrary point cloud with no relation to the known table marker
        # layout must not be silently forced into an assignment.
        blobs = [
            _FakeBlob(0.0, 0.0, 900.0),
            _FakeBlob(50.0, 10.0, 905.0),
            _FakeBlob(-30.0, 40.0, 895.0),
            _FakeBlob(20.0, -60.0, 910.0),
            _FakeBlob(-10.0, -20.0, 890.0),
        ]
        with self.assertRaises(tp.MatchingError):
            tp.match_points(blobs)

    def test_wrong_marker_count_raises(self):
        camera_pts = self._camera_points()[:4]
        blobs = [_FakeBlob(*p) for p in camera_pts]
        with self.assertRaises(tp.MatchingError):
            tp.match_points(blobs)


class WorldToCellTests(unittest.TestCase):
    """Grid (0,0) is the table corner diagonal from the marker "origin"
    corner: col increases toward origin along the long/X wall (world X runs
    0..-TABLE_LONG_SIDE_MM), row increases toward origin along the short/Y
    wall (world Y runs 0..+TABLE_SHORT_SIDE_MM)."""

    def test_far_corner_from_origin_is_grid_zero_zero(self):
        row, col = tp.world_to_cell(-tp.TABLE_LONG_SIDE_MM, tp.TABLE_SHORT_SIDE_MM)
        self.assertEqual((row, col), (0, 0))

    def test_marker_origin_corner_is_last_row_and_col(self):
        row, col = tp.world_to_cell(0.0, 0.0)
        self.assertEqual((row, col), (tp.GRID_ROWS - 1, tp.GRID_COLS - 1))

    def test_center_of_table_is_middle_cell(self):
        row, col = tp.world_to_cell(-tp.TABLE_LONG_SIDE_MM / 2.0, tp.TABLE_SHORT_SIDE_MM / 2.0)
        self.assertEqual((row, col), (tp.GRID_ROWS // 2, tp.GRID_COLS // 2))

    def test_out_of_bounds_positions_are_clamped_not_raised(self):
        row, col = tp.world_to_cell(1000.0, -1000.0)
        self.assertEqual((row, col), (tp.GRID_ROWS - 1, tp.GRID_COLS - 1))
        row, col = tp.world_to_cell(-tp.TABLE_LONG_SIDE_MM - 1000.0, tp.TABLE_SHORT_SIDE_MM + 1000.0)
        self.assertEqual((row, col), (0, 0))


class DetectMarkersBallRejectionTests(unittest.TestCase):
    """The ball is also IR-reflective, so a bright circular blob alone isn't
    enough to tell it apart from a marker -- detect_markers must reject it
    on physical size (mm), not just pixel area, since pixel area conflates
    size with distance from the camera."""

    def _synthetic_frame(self, blobs_px, depth_mm=900.0, size=400):
        """blobs_px: list of (cx, cy, radius_px). Draws bright filled circles
        on a dim background at a uniform depth."""
        ir = np.full((size, size), 200, dtype=np.uint16)
        yy, xx = np.ogrid[:size, :size]
        for cx, cy, r in blobs_px:
            mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
            ir[mask] = 4000
        depth = np.full((size, size), depth_mm, dtype=np.float32)
        return ir, depth

    def test_ball_sized_blob_is_excluded_even_though_pixel_area_is_in_range(self):
        fx = fy = 500.0
        ppx = ppy = 200.0
        z_mm = 900.0

        # 5 small marker-sized blobs (radius_mm well under _MAX_MARKER_RADIUS_MM).
        marker_radius_px = 5.0
        marker_centers = [(60, 60), (140, 60), (220, 60), (60, 140), (60, 220)]
        blobs_px = [(cx, cy, marker_radius_px) for cx, cy in marker_centers]

        # One much larger ball-sized blob: its radius in mm exceeds the
        # marker cutoff, but its pixel *area* alone is still comfortably
        # inside [_MIN_MARKER_AREA_PX, _MAX_MARKER_AREA_PX], so only the new
        # physical-size (mm) check catches it.
        ball_radius_px = 30.0
        ball_radius_mm = ball_radius_px * z_mm / fx
        self.assertGreater(ball_radius_mm, tp._MAX_MARKER_RADIUS_MM)
        ball_area_px = np.pi * ball_radius_px ** 2
        self.assertLess(ball_area_px, tp._MAX_MARKER_AREA_PX)
        blobs_px.append((300, 300, ball_radius_px))

        ir, depth = self._synthetic_frame(blobs_px, depth_mm=z_mm)

        found, _debug_frame, _diag = tp.detect_markers(ir, depth, fx, fy, ppx, ppy)

        self.assertEqual(len(found), len(marker_centers))
        for blob in found:
            self.assertLess(blob.radius_px, ball_radius_px)
            dist_to_ball = ((blob.cx - 300) ** 2 + (blob.cy - 300) ** 2) ** 0.5
            self.assertGreater(dist_to_ball, ball_radius_px)


class SelectInlierMarkersTests(unittest.TestCase):
    """select_inlier_markers() is the RANSAC-ish gate: given more candidate
    blobs than markers, it uses a prior pose to predict each known marker's
    camera-frame position and keeps only the closest blob to each -- the
    rest (e.g. a stray reflection) are dropped rather than failing the
    whole frame."""

    def _camera_points_for_pose(self, R: np.ndarray, t: np.ndarray) -> dict:
        """camera = R.T @ (world - t) is the inverse of world = R @ camera + t."""
        return {
            name: R.T @ (np.array(world_pt, dtype=np.float64) - t)
            for name, world_pt in tp.TABLE_MARKER_WORLD_POINTS.items()
        }

    def test_extra_spurious_blob_is_dropped(self):
        R0 = _rotation_from_euler_deg(3.0, -5.0, 8.0)
        t0 = np.array([50.0, -30.0, 900.0])
        cam_pts = self._camera_points_for_pose(R0, t0)

        blobs = [_FakeBlob(*cam_pts[name]) for name in cam_pts]
        # A spurious blob nowhere near any predicted marker position.
        spurious = _FakeBlob(cam_pts["origin"][0] + 500.0, cam_pts["origin"][1] + 500.0, cam_pts["origin"][2])
        blobs.append(spurious)

        selected = tp.select_inlier_markers(blobs, R0, t0)

        self.assertEqual(len(selected), tp._EXPECTED_MARKER_COUNT)
        self.assertNotIn(spurious, selected)
        for name, pred in cam_pts.items():
            closest = min(selected, key=lambda b: np.linalg.norm(pred - np.array([b.x_mm, b.y_mm, b.z_mm])))
            self.assertLess(np.linalg.norm(pred - np.array([closest.x_mm, closest.y_mm, closest.z_mm])), 1e-6)

    def test_multiple_extra_blobs_still_selects_the_five_closest(self):
        R0 = _rotation_from_euler_deg(0.0, 0.0, 0.0)
        t0 = np.array([0.0, 0.0, 900.0])
        cam_pts = self._camera_points_for_pose(R0, t0)

        blobs = [_FakeBlob(*cam_pts[name]) for name in cam_pts]
        spurious_a = _FakeBlob(cam_pts["x1"][0] + 300.0, cam_pts["x1"][1], cam_pts["x1"][2])
        spurious_b = _FakeBlob(cam_pts["y2"][0], cam_pts["y2"][1] + 300.0, cam_pts["y2"][2])
        blobs.extend([spurious_a, spurious_b])

        selected = tp.select_inlier_markers(blobs, R0, t0)

        self.assertEqual(len(selected), tp._EXPECTED_MARKER_COUNT)
        self.assertNotIn(spurious_a, selected)
        self.assertNotIn(spurious_b, selected)


class RunPoseFitPriorPoseTests(unittest.TestCase):
    """run_pose_fit() only tolerates an over-count of candidate blobs when
    given a prior pose to disambiguate with; otherwise it fails the attempt
    rather than guessing."""

    def _blobs_with_one_spurious(self, R0, t0):
        cam_pts = {
            name: R0.T @ (np.array(world_pt, dtype=np.float64) - t0)
            for name, world_pt in tp.TABLE_MARKER_WORLD_POINTS.items()
        }
        blobs = [_FakeBlob(*cam_pts[name]) for name in cam_pts]
        blobs.append(_FakeBlob(cam_pts["origin"][0] + 500.0, cam_pts["origin"][1] + 500.0, cam_pts["origin"][2]))
        return blobs

    def test_over_count_without_prior_pose_fails(self):
        R0 = _rotation_from_euler_deg(2.0, -3.0, 5.0)
        t0 = np.array([10.0, 20.0, 900.0])
        blobs = self._blobs_with_one_spurious(R0, t0)

        with mock.patch.object(tp, "detect_markers", return_value=(blobs, None, None)):
            attempt = tp.run_pose_fit(None, None, 1.0, 1.0, 1.0, 1.0, prior_pose=None)

        self.assertFalse(attempt.ok)
        self.assertIn("no prior pose", attempt.error)

    def test_over_count_with_prior_pose_succeeds(self):
        R0 = _rotation_from_euler_deg(2.0, -3.0, 5.0)
        t0 = np.array([10.0, 20.0, 900.0])
        blobs = self._blobs_with_one_spurious(R0, t0)

        with mock.patch.object(tp, "detect_markers", return_value=(blobs, None, None)):
            attempt = tp.run_pose_fit(None, None, 1.0, 1.0, 1.0, 1.0, prior_pose=(R0, t0))

        self.assertTrue(attempt.ok)
        np.testing.assert_allclose(attempt.fit.R, R0, atol=1e-6)
        np.testing.assert_allclose(attempt.fit.t, t0, atol=1e-6)


class TablePoseTrackerTests(unittest.TestCase):
    """These test the tracker's state machine (hold-last-pose + stale flag)
    in isolation from real image detection, by mocking run_pose_fit."""

    def _successful_attempt(self, rms=2.0, max_r=3.0):
        R0 = _rotation_from_euler_deg(0.0, 0.0, 10.0)
        fit = tp.RigidFitResult(
            R=R0, t=np.array([1.0, 2.0, 3.0]),
            residuals_mm=[rms] * 5, rms_residual_mm=rms, max_residual_mm=max_r,
        )
        return tp.PoseFitAttempt(ok=True, error=None, debug_frame=None, fit=fit)

    def _failed_attempt(self, error="expected 5 retroreflective markers, found 4"):
        return tp.PoseFitAttempt(ok=False, error=error, debug_frame=None, fit=None)

    def test_before_any_success_apply_reports_untracked_and_stale(self):
        tracker = tp.TablePoseTracker()
        world, stale, age_s = tracker.apply((0.0, 0.0, 0.0))
        self.assertFalse(tracker.is_tracking)
        self.assertIsNone(world)
        self.assertTrue(stale)
        self.assertIsNone(age_s)

    def test_successful_update_starts_tracking_and_is_not_stale(self):
        tracker = tp.TablePoseTracker()
        with mock.patch.object(tp, "run_pose_fit", return_value=self._successful_attempt()):
            attempt = tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=100.0)

        self.assertTrue(attempt.ok)
        self.assertTrue(tracker.is_tracking)
        world, stale, age_s = tracker.apply((0.0, 0.0, 0.0), now=100.0)
        self.assertIsNotNone(world)
        self.assertFalse(stale)
        self.assertAlmostEqual(age_s, 0.0)

    def test_failed_update_after_success_holds_last_pose_but_flags_stale(self):
        tracker = tp.TablePoseTracker()
        with mock.patch.object(tp, "run_pose_fit", return_value=self._successful_attempt()):
            tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=100.0)
        R_before, t_before = tracker.R.copy(), tracker.t.copy()

        with mock.patch.object(tp, "run_pose_fit", return_value=self._failed_attempt()):
            attempt = tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=105.0)

        self.assertFalse(attempt.ok)
        self.assertTrue(tracker.is_tracking)  # still holds the old pose
        np.testing.assert_allclose(tracker.R, R_before)
        np.testing.assert_allclose(tracker.t, t_before)

        world, stale, age_s = tracker.apply((0.0, 0.0, 0.0), now=105.0)
        self.assertIsNotNone(world)  # coasting on the held pose
        self.assertTrue(stale)
        self.assertAlmostEqual(age_s, 5.0)  # age is since the last *success*

    def test_recovering_after_a_failure_clears_stale(self):
        tracker = tp.TablePoseTracker()
        with mock.patch.object(tp, "run_pose_fit", return_value=self._successful_attempt()):
            tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=100.0)
        with mock.patch.object(tp, "run_pose_fit", return_value=self._failed_attempt()):
            tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=105.0)
        with mock.patch.object(tp, "run_pose_fit", return_value=self._successful_attempt()):
            tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=110.0)

        world, stale, age_s = tracker.apply((0.0, 0.0, 0.0), now=110.0)
        self.assertFalse(stale)
        self.assertAlmostEqual(age_s, 0.0)

    def test_first_update_passes_no_prior_pose(self):
        tracker = tp.TablePoseTracker()
        with mock.patch.object(tp, "run_pose_fit", return_value=self._successful_attempt()) as mocked:
            tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=100.0)
        self.assertIsNone(mocked.call_args.kwargs["prior_pose"])

    def test_later_update_passes_previously_held_pose_as_prior(self):
        tracker = tp.TablePoseTracker()
        with mock.patch.object(tp, "run_pose_fit", return_value=self._successful_attempt()):
            tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=100.0)
        R_before, t_before = tracker.R.copy(), tracker.t.copy()

        with mock.patch.object(tp, "run_pose_fit", return_value=self._successful_attempt()) as mocked:
            tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=105.0)

        prior_R, prior_t = mocked.call_args.kwargs["prior_pose"]
        np.testing.assert_allclose(prior_R, R_before)
        np.testing.assert_allclose(prior_t, t_before)


class TiltDegFromGravityTests(unittest.TestCase):
    """tilt_deg_from_gravity compares the table's fitted Z axis (expressed
    in camera-frame coordinates via R.T) against a camera-frame "up" vector
    -- both expressed in the same (camera) frame, so the camera's own
    unknown mounting angle cancels out without needing separate
    calibration."""

    def test_level_table_reads_zero(self):
        # R = identity: table's Z axis in camera frame is exactly (0,0,1).
        # If gravity "up" is also exactly (0,0,1), the table is level.
        R = np.eye(3)
        self.assertAlmostEqual(tp.tilt_deg_from_gravity(R, np.array([0.0, 0.0, 1.0])), 0.0, places=6)

    def test_known_tilt_angle_is_recovered(self):
        # Tilt the table 15 degrees about the camera-frame X axis; gravity
        # stays fixed at the camera's original "up" -- the angle between the
        # table's rotated Z axis and that fixed "up" should be 15 degrees.
        R = _rotation_from_euler_deg(15.0, 0.0, 0.0)
        tilt = tp.tilt_deg_from_gravity(R, np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(tilt, 15.0, places=4)

    def test_90_degree_tilt(self):
        R = _rotation_from_euler_deg(90.0, 0.0, 0.0)
        tilt = tp.tilt_deg_from_gravity(R, np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(tilt, 90.0, places=4)

    def test_gravity_vector_need_not_be_normalized(self):
        R = _rotation_from_euler_deg(20.0, 0.0, 0.0)
        tilt_unit = tp.tilt_deg_from_gravity(R, np.array([0.0, 0.0, 1.0]))
        tilt_scaled = tp.tilt_deg_from_gravity(R, np.array([0.0, 0.0, 9.81]))
        self.assertAlmostEqual(tilt_unit, tilt_scaled, places=6)


class RollPitchDegFromGravityTests(unittest.TestCase):
    """Per ball_balancer.py's convention: pitch is rotation about the
    table's own X axis, roll is rotation about the table's own Y axis.
    _rotation_from_euler_deg(x_deg, 0, 0) is a pure-X rotation (Rz(0)@Ry(0)@Rx(x_deg)
    == Rx(x_deg)), and (0, y_deg, 0) is pure-Y, so each isolates one axis
    cleanly for testing."""

    def test_level_table_reads_zero_zero(self):
        R = np.eye(3)
        roll, pitch = tp.roll_pitch_deg_from_gravity(R, np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(roll, 0.0, places=6)
        self.assertAlmostEqual(pitch, 0.0, places=6)

    def test_pure_x_rotation_is_pitch_only(self):
        R = _rotation_from_euler_deg(12.0, 0.0, 0.0)
        roll, pitch = tp.roll_pitch_deg_from_gravity(R, np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(pitch, 12.0, places=4)
        self.assertAlmostEqual(roll, 0.0, places=4)

    def test_pure_y_rotation_is_roll_only(self):
        R = _rotation_from_euler_deg(0.0, 15.0, 0.0)
        roll, pitch = tp.roll_pitch_deg_from_gravity(R, np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(roll, 15.0, places=4)
        self.assertAlmostEqual(pitch, 0.0, places=4)

    def test_negative_x_rotation_gives_negative_pitch(self):
        R = _rotation_from_euler_deg(-9.0, 0.0, 0.0)
        roll, pitch = tp.roll_pitch_deg_from_gravity(R, np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(pitch, -9.0, places=4)
        self.assertAlmostEqual(roll, 0.0, places=4)

    def test_combined_roll_and_pitch(self):
        # roll = atan2(gx, gz) is exact regardless of pitch (the pitch-only
        # cos6 factor cancels in that ratio), but pitch = atan2(-gy, gz)
        # picks up a small 1/cos(roll) coupling term from the composed
        # rotation order -- expected for a two-angle decomposition of a
        # combined tilt, not a bug, so pitch gets a looser tolerance here.
        R = _rotation_from_euler_deg(6.0, -4.0, 0.0)
        roll, pitch = tp.roll_pitch_deg_from_gravity(R, np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(roll, -4.0, places=6)
        self.assertAlmostEqual(pitch, 6.0, delta=0.02)


class GravityEstimatorTests(unittest.TestCase):
    def test_no_samples_yet_has_no_up_vector(self):
        est = tp.GravityEstimator()
        self.assertIsNone(est.up_vector)

    def test_single_sample_is_normalized(self):
        est = tp.GravityEstimator()
        est.add_sample((0.0, 0.0, 9.81))
        np.testing.assert_allclose(est.up_vector, [0.0, 0.0, 1.0], atol=1e-9)

    def test_sign_flips_the_reading(self):
        est = tp.GravityEstimator(sign=-1.0)
        est.add_sample((0.0, 0.0, 9.81))
        np.testing.assert_allclose(est.up_vector, [0.0, 0.0, -1.0], atol=1e-9)

    def test_repeated_consistent_samples_converge_and_stay_unit_length(self):
        est = tp.GravityEstimator(smoothing=0.1)
        for _ in range(200):
            est.add_sample((0.0, 0.3, 9.81))
        self.assertAlmostEqual(float(np.linalg.norm(est.up_vector)), 1.0, places=6)
        expected = np.array([0.0, 0.3, 9.81])
        expected = expected / np.linalg.norm(expected)
        np.testing.assert_allclose(est.up_vector, expected, atol=1e-3)

    def test_zero_sample_is_ignored(self):
        est = tp.GravityEstimator()
        est.add_sample((0.0, 0.0, 9.81))
        before = est.up_vector.copy()
        est.add_sample((0.0, 0.0, 0.0))
        np.testing.assert_allclose(est.up_vector, before)


class TablePoseTrackerTiltTests(unittest.TestCase):
    def test_tilt_deg_is_none_before_tracking_or_without_gravity(self):
        tracker = tp.TablePoseTracker()
        self.assertIsNone(tracker.tilt_deg(np.array([0.0, 0.0, 1.0])))
        self.assertIsNone(tracker.tilt_deg(None))

    def test_tilt_deg_uses_current_fit_and_gravity(self):
        tracker = tp.TablePoseTracker()
        R0 = _rotation_from_euler_deg(8.0, 0.0, 0.0)
        fit = tp.RigidFitResult(
            R=R0, t=np.array([0.0, 0.0, 900.0]),
            residuals_mm=[1.0] * 5, rms_residual_mm=1.0, max_residual_mm=1.0,
        )
        with mock.patch.object(
            tp, "run_pose_fit",
            return_value=tp.PoseFitAttempt(ok=True, error=None, debug_frame=None, fit=fit),
        ):
            tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=100.0)

        tilt = tracker.tilt_deg(np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(tilt, 8.0, places=4)

    def test_roll_pitch_deg_is_none_before_tracking_or_without_gravity(self):
        tracker = tp.TablePoseTracker()
        self.assertIsNone(tracker.roll_pitch_deg(np.array([0.0, 0.0, 1.0])))
        self.assertIsNone(tracker.roll_pitch_deg(None))

    def test_roll_pitch_deg_uses_current_fit_and_gravity(self):
        tracker = tp.TablePoseTracker()
        R0 = _rotation_from_euler_deg(0.0, -7.0, 0.0)
        fit = tp.RigidFitResult(
            R=R0, t=np.array([0.0, 0.0, 900.0]),
            residuals_mm=[1.0] * 5, rms_residual_mm=1.0, max_residual_mm=1.0,
        )
        with mock.patch.object(
            tp, "run_pose_fit",
            return_value=tp.PoseFitAttempt(ok=True, error=None, debug_frame=None, fit=fit),
        ):
            tracker.update(None, None, 1.0, 1.0, 1.0, 1.0, now=100.0)

        roll, pitch = tracker.roll_pitch_deg(np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(roll, -7.0, places=4)
        self.assertAlmostEqual(pitch, 0.0, places=4)


class BallTrackerRegressionTests(unittest.TestCase):
    """Confirm extracting camera_geometry.py didn't change BallTracker's output."""

    def test_ball_detection_unaffected_by_extraction(self):
        from ball_tracker import BallDetector, BallTracker

        h, w = 200, 200
        ir = np.full((h, w), 200, dtype=np.uint16)
        cy, cx, r = 100, 100, 20
        yy, xx = np.ogrid[:h, :w]
        mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        ir[mask] = 4000  # bright retro-reflective ball against a dark background

        depth = np.full((h, w), 1000.0, dtype=np.float32)

        intrinsics = dict(fx=500.0, fy=500.0, ppx=100.0, ppy=100.0)
        detector = BallDetector(**intrinsics, ball_radius_min_mm=5.0, ball_radius_max_mm=60.0)
        tracker = BallTracker(**intrinsics)
        try:
            detection = detector.detect(ir, depth)
            self.assertIsNotNone(detection)
            pos, det = tracker.update(detection)
            self.assertIsNotNone(pos)
            self.assertIsNotNone(det)
            self.assertAlmostEqual(det.cx, cx, delta=2.0)
            self.assertAlmostEqual(det.cy, cy, delta=2.0)
        finally:
            tracker.close()


if __name__ == "__main__":
    unittest.main()
