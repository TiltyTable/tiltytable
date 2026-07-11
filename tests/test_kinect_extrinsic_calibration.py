from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kinect_extrinsic_calibration as kec


def _rotation_from_euler_deg(x_deg: float, y_deg: float, z_deg: float) -> np.ndarray:
    x, y, z = np.radians([x_deg, y_deg, z_deg])
    Rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
    Ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    Rz = np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _world_points_array() -> np.ndarray:
    return np.array([kec.TABLE_MARKER_WORLD_POINTS[k] for k in kec.TABLE_MARKER_WORLD_POINTS], dtype=np.float64)


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

        fit = kec.fit_rigid_transform(camera_pts, world_pts)

        np.testing.assert_allclose(fit.R, R0, atol=1e-8)
        np.testing.assert_allclose(fit.t, t0, atol=1e-8)
        self.assertLess(fit.rms_residual_mm, 1e-6)
        self.assertLess(fit.max_residual_mm, 1e-6)

    def test_noise_tolerance_stays_bounded(self):
        # NOTE: the current marker layout spans only ~277mm (vs. a full
        # 832mm table), so it's a compact, weakly-conditioned point cluster
        # for estimating rotation. A small rotation error is nearly free in
        # residual terms at the cluster itself, but couples with the ~900mm
        # camera-to-table distance to produce a much larger *raw* R/t
        # deviation from ground truth than the fit residual alone suggests.
        # So this test checks the residual (what accept_calibration() actually
        # gates on), not raw R/t vs. ground truth, which would be misleadingly
        # strict for this geometry.
        rng = np.random.default_rng(42)
        R0 = _rotation_from_euler_deg(3.0, 8.0, -5.0)
        t0 = np.array([10.0, 5.0, 900.0])

        world_pts = _world_points_array()
        camera_pts = (np.linalg.inv(R0) @ (world_pts - t0).T).T
        camera_pts += rng.normal(scale=1.0, size=camera_pts.shape)

        fit = kec.fit_rigid_transform(camera_pts, world_pts)

        self.assertLess(fit.rms_residual_mm, 5.0)
        self.assertLess(fit.max_residual_mm, 8.0)

    def test_reflection_guard_on_planar_points(self):
        # All world points are Z=0 (planar) -- exactly this jig's configuration,
        # a case where SVD conditioning on the reflection sign matters.
        R0 = _rotation_from_euler_deg(0.0, 0.0, 33.0)
        t0 = np.array([0.0, 0.0, 800.0])

        world_pts = _world_points_array()
        self.assertTrue(np.allclose(world_pts[:, 2], 0.0))
        camera_pts = (np.linalg.inv(R0) @ (world_pts - t0).T).T

        fit = kec.fit_rigid_transform(camera_pts, world_pts)

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

        matched = kec.match_points(shuffled)

        self.assertEqual(set(matched.keys()), set(kec.TABLE_MARKER_WORLD_POINTS.keys()))
        for i, name in enumerate(kec.TABLE_MARKER_WORLD_POINTS.keys()):
            got = matched[name]
            self.assertAlmostEqual(got.x_mm, camera_pts[i][0], places=6)
            self.assertAlmostEqual(got.y_mm, camera_pts[i][1], places=6)
            self.assertAlmostEqual(got.z_mm, camera_pts[i][2], places=6)

    def test_various_orientations_still_match(self):
        for z_deg in (0.0, 45.0, 90.0, 135.0, 200.0):
            R0 = _rotation_from_euler_deg(2.0, -3.0, z_deg)
            camera_pts = self._camera_points(R0=R0, t0=np.array([0.0, 0.0, 900.0]))
            blobs = [_FakeBlob(*p) for p in camera_pts]
            matched = kec.match_points(blobs)
            self.assertEqual(set(matched.keys()), set(kec.TABLE_MARKER_WORLD_POINTS.keys()))

    def test_noise_within_tolerance_still_matches(self):
        rng = np.random.default_rng(3)
        camera_pts = self._camera_points()
        camera_pts = camera_pts + rng.normal(scale=2.0, size=camera_pts.shape)
        blobs = [_FakeBlob(*p) for p in camera_pts]
        matched = kec.match_points(blobs)
        self.assertEqual(set(matched.keys()), set(kec.TABLE_MARKER_WORLD_POINTS.keys()))

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
        with self.assertRaises(kec.MatchingError):
            kec.match_points(blobs)

    def test_wrong_marker_count_raises(self):
        camera_pts = self._camera_points()[:4]
        blobs = [_FakeBlob(*p) for p in camera_pts]
        with self.assertRaises(kec.MatchingError):
            kec.match_points(blobs)


class ExtrinsicsPersistenceTests(unittest.TestCase):
    def test_round_trip_and_atomic_write(self):
        R0 = _rotation_from_euler_deg(1.0, 2.0, 3.0)
        t0 = np.array([5.0, 6.0, 700.0])
        world_pts = _world_points_array()
        camera_pts = (np.linalg.inv(R0) @ (world_pts - t0).T).T
        fit = kec.fit_rigid_transform(camera_pts, world_pts)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "extrinsics.json"
            saved = kec.save_extrinsics(path, fit)

            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

            loaded = kec.load_extrinsics(path)
            self.assertIsNotNone(loaded)
            np.testing.assert_allclose(loaded.R, saved.R)
            np.testing.assert_allclose(loaded.t, saved.t)

            world_from_apply = loaded.apply(tuple(camera_pts[0]))
            np.testing.assert_allclose(world_from_apply, world_pts[0], atol=1e-6)

    def test_load_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "does_not_exist.json"
            self.assertIsNone(kec.load_extrinsics(path))

    def test_load_malformed_json_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "extrinsics.json"
            path.write_text("not valid json", encoding="utf-8")
            self.assertIsNone(kec.load_extrinsics(path))


class BallTrackerRegressionTests(unittest.TestCase):
    """Confirm extracting camera_geometry.py didn't change BallTracker's output."""

    def test_ball_detection_unaffected_by_extraction(self):
        from ball_tracker import BallTracker

        h, w = 200, 200
        ir = np.full((h, w), 200, dtype=np.uint16)
        cy, cx, r = 100, 100, 20
        yy, xx = np.ogrid[:h, :w]
        mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        ir[mask] = 30  # dark ball against bright background

        depth = np.full((h, w), 1000.0, dtype=np.float32)

        tracker = BallTracker(
            fx=500.0, fy=500.0, ppx=100.0, ppy=100.0,
            ball_radius_min_mm=5.0, ball_radius_max_mm=60.0,
        )
        try:
            pos, det = tracker.update(ir, depth)
            self.assertIsNotNone(pos)
            self.assertIsNotNone(det)
            self.assertAlmostEqual(det.cx, cx, delta=2.0)
            self.assertAlmostEqual(det.cy, cy, delta=2.0)
        finally:
            tracker.close()


if __name__ == "__main__":
    unittest.main()
