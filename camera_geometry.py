#!/usr/bin/env python3
"""
Shared pinhole camera geometry helpers for the Azure Kinect IR/depth sensor.

Extracted from ball_tracker.py so other detectors (e.g. the extrinsic
calibration marker detector) can reuse the same unprojection and depth
sampling logic without duplicating it.
"""

from typing import Optional

import numpy as np


def unproject_pixel(
    cx: float,
    cy: float,
    z_mm: float,
    fx: float,
    fy: float,
    ppx: float,
    ppy: float,
) -> tuple[float, float, float]:
    """Pinhole unprojection: pixel (cx, cy) + depth z_mm -> camera-frame (X, Y, Z) mm."""
    x_mm = (cx - ppx) * z_mm / fx
    y_mm = (cy - ppy) * z_mm / fy
    return x_mm, y_mm, z_mm


def sample_depth_patch(
    depth_mm: np.ndarray,
    cx: float,
    cy: float,
    radius_px: float,
    sample_fraction: float,
    min_valid_fraction: float,
) -> Optional[float]:
    """Median depth in a square patch of half-width radius_px * sample_fraction."""
    r = max(1, int(radius_px * sample_fraction))
    h, w = depth_mm.shape[:2]
    x0, x1 = max(0, int(cx) - r), min(w, int(cx) + r + 1)
    y0, y1 = max(0, int(cy) - r), min(h, int(cy) + r + 1)
    if x1 <= x0 or y1 <= y0:
        return None

    patch = depth_mm[y0:y1, x0:x1].astype(np.float32, copy=False)
    valid = np.isfinite(patch) & (patch > 0)
    if int(np.count_nonzero(valid)) < max(1, int(patch.size * min_valid_fraction)):
        return None

    return float(np.median(patch[valid]))


def sample_depth_ring(
    depth_mm: np.ndarray,
    cx: float,
    cy: float,
    radius_px: float,
    r_low_fraction: float,
    r_high_fraction: float,
    min_valid_fraction: float,
) -> Optional[float]:
    """Median depth in an annular region [r_low, r_high] x radius_px."""
    r_out = max(2, int(radius_px * r_high_fraction))
    r_in = max(1, int(radius_px * r_low_fraction))
    h, w = depth_mm.shape[:2]
    cx_i, cy_i = int(round(cx)), int(round(cy))
    x0 = max(0, cx_i - r_out)
    x1 = min(w, cx_i + r_out + 1)
    y0 = max(0, cy_i - r_out)
    y1 = min(h, cy_i + r_out + 1)
    if x1 <= x0 or y1 <= y0:
        return None

    patch = depth_mm[y0:y1, x0:x1].astype(np.float32, copy=False)
    ys = np.arange(y0, y1, dtype=np.float32) - cy
    xs = np.arange(x0, x1, dtype=np.float32) - cx
    XX, YY = np.meshgrid(xs, ys)
    d2 = XX * XX + YY * YY
    ring = (d2 >= r_in * r_in) & (d2 <= r_out * r_out)
    valid = ring & np.isfinite(patch) & (patch > 0)
    if int(np.count_nonzero(valid)) < max(1, int(np.count_nonzero(ring) * min_valid_fraction)):
        return None

    return float(np.median(patch[valid]))
