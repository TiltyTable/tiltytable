#!/usr/bin/env python3
"""
Tilt-table (3-RRS crank platform) kinematics + tilt-envelope analysis.

PURPOSE
-------
Single documented source of truth for the platform's tilt envelope and how it
depends on the mechanism geometry. The archived Arduino firmware
(`archive/stewart_legacy/arduino/uim5756pm_stewart/uim5756pm_stewart.ino`)
hard-codes the same geometry constants but contains no derivation; this script reproduces that
inverse kinematics, states every assumption explicitly, and computes the
maximum achievable tilt and what limits it.

MECHANISM (per leg, 3 legs at 120 deg)
--------------------------------------
  motor/gearbox  --R-->  crank  --R-->  arm(coupler)  --S-->  moving platform
  (driven revolute)      (revolute)                    (spherical, GE8C)

  - The crank rotates in the vertical plane that contains the leg's azimuth
    (a radial-vertical plane).
  - In the arm-vertical design variant, the NEUTRAL pose intent is:
        * crank horizontal  (crank pin at z = 0)
        * arm perfectly vertical
    Those two facts FORCE two geometric relationships:
        BASE_MOTOR_RADIUS - CRANK_RADIUS = PLATFORM_ROD_RADIUS   (radii align)
        NEUTRAL_TOP_Z = ARM_LENGTH                               (arm spans z gap)
    => You cannot change ARM_LENGTH while keeping BOTH "crank horizontal" and
       "arm vertical" unless you also move the platform neutral height to match.
       This coupling is the key to the "shorter arm" question (see report).
    The as-built firmware geometry is different: BASE_MOTOR_RADIUS =
    PLATFORM_ROD_RADIUS = 119 mm after the motors moved inward. At its fixed
    20 mm operating heave, the crank and arm are intentionally diagonal.

  - The spherical bearing (GE8C) has a hard articulation limit. The firmware
    uses ROD_END_LIMIT_DEG = 14 deg; the SKF GE8C datasheet allows 15 deg.

CONVENTIONS
-----------
  - Roll = rotation about world X, Pitch = rotation about world Y, applied with
    the same composition as the firmware `rotateRollPitch()`.
  - "Tilt" = angle between the tilted platform normal and vertical
        tilt = acos(cos(roll) * cos(pitch)).
  - Rod-end misalignment is modeled exactly as the firmware does: the angle of
    the arm vector away from vertical (a conservative proxy for the GE8C swing).

All lengths in mm, angles in degrees unless noted.
"""

import math
from dataclasses import dataclass


# --------------------------------------------------------------------------
# Geometry (defaults mirror the firmware as of this writing)
# --------------------------------------------------------------------------
@dataclass
class Geometry:
    platform_rod_radius_mm: float = 119.0   # firmware TABLE_ROD_RADIUS_MM
    crank_radius_mm: float = 30.0
    arm_length_mm: float = 110.0
    neutral_top_z_mm: float = 110.0
    rod_end_limit_deg: float = 14.0
    neutral_crank_deg: float = 180.0
    leg_azimuth_deg: tuple = (0.0, 120.0, 240.0)
    # As built 2026-07-09: motors moved inward so BASE == TABLE.
    base_motor_radius_mm: float = 119.0

    @classmethod
    def arm_vertical(cls, platform_rod_radius_mm, crank_radius_mm, arm_length_mm,
                     rod_end_limit_deg=14.0):
        """Build a geometry that holds crank-horizontal + arm-vertical at neutral.

        Enforces NEUTRAL_TOP_Z = ARM_LENGTH (the only neutral height that keeps
        the arm vertical when the radii are aligned)."""
        return cls(
            platform_rod_radius_mm=platform_rod_radius_mm,
            crank_radius_mm=crank_radius_mm,
            arm_length_mm=arm_length_mm,
            neutral_top_z_mm=arm_length_mm,
            rod_end_limit_deg=rod_end_limit_deg,
            base_motor_radius_mm=platform_rod_radius_mm + crank_radius_mm,
        )


# --------------------------------------------------------------------------
# Kinematics (faithful reproduction of the firmware)
# --------------------------------------------------------------------------
def _rotate_roll_pitch(p, roll_rad, pitch_rad):
    cr, sr = math.cos(roll_rad), math.sin(roll_rad)
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    x1 = p[0]
    y1 = p[1] * cr - p[2] * sr
    z1 = p[1] * sr + p[2] * cr
    return (x1 * cp + z1 * sp, y1, -x1 * sp + z1 * cp)


def _top_rod_position(g, i, roll_deg, pitch_deg, heave_mm):
    a = math.radians(g.leg_azimuth_deg[i])
    neutral = (g.platform_rod_radius_mm * math.cos(a),
               g.platform_rod_radius_mm * math.sin(a),
               0.0)
    p = _rotate_roll_pitch(neutral, math.radians(roll_deg), math.radians(pitch_deg))
    return (p[0], p[1], p[2] + g.neutral_top_z_mm + heave_mm)


def _solve_crank_angle(g, i, top):
    """Return (ok, crank_deg, misalign_deg). ok=False if the leg can't reach."""
    a = math.radians(g.leg_azimuth_deg[i])
    ux, uy = math.cos(a), math.sin(a)          # radial unit
    vx, vy = -math.sin(a), math.cos(a)         # tangential unit

    top_r = top[0] * ux + top[1] * uy
    top_t = top[0] * vx + top[1] * vy
    arm_sq = g.arm_length_mm ** 2
    if top_t * top_t > arm_sq:
        return (False, None, None)             # tangential offset exceeds arm

    eff_arm = math.sqrt(arm_sq - top_t * top_t)
    aa = top_r - g.base_motor_radius_mm
    bb = top[2]
    dist = math.hypot(aa, bb)
    if dist < 1e-6:
        return (False, None, None)

    cos_term = (dist * dist + g.crank_radius_mm ** 2 - eff_arm ** 2) / \
               (2.0 * g.crank_radius_mm * dist)
    if cos_term < -1.0 or cos_term > 1.0:
        return (False, None, None)             # crank cannot close the loop

    phi = math.atan2(bb, aa)
    alpha = math.acos(cos_term)
    c0 = math.degrees(phi + alpha)
    c1 = math.degrees(phi - alpha)

    def near(c):
        d = c - g.neutral_crank_deg
        while d > 180.0:
            d -= 360.0
        while d < -180.0:
            d += 360.0
        return abs(d)

    crank_deg = c0 if near(c0) <= near(c1) else c1

    # Crank pin position for the chosen angle.
    cr = math.radians(crank_deg)
    pin = (g.base_motor_radius_mm * ux + g.crank_radius_mm * math.cos(cr) * ux,
           g.base_motor_radius_mm * uy + g.crank_radius_mm * math.cos(cr) * uy,
           g.crank_radius_mm * math.sin(cr))
    dx, dy, dz = top[0] - pin[0], top[1] - pin[1], top[2] - pin[2]
    misalign = math.degrees(math.atan2(math.hypot(dx, dy), dz))
    return (True, crank_deg, misalign)


def pose_feasible(g, roll_deg, pitch_deg, heave_mm=0.0):
    """(ok, worst_misalign_deg, limiting_reason)."""
    worst = 0.0
    for i in range(3):
        top = _top_rod_position(g, i, roll_deg, pitch_deg, heave_mm)
        ok, _crank, mis = _solve_crank_angle(g, i, top)
        if not ok:
            return (False, None, "unreachable (crank/arm cannot close)")
        worst = max(worst, mis)
    if worst > g.rod_end_limit_deg:
        return (False, worst, "rod-end angle exceeded")
    return (True, worst, "ok")


def tilt_magnitude_deg(roll_deg, pitch_deg):
    return math.degrees(math.acos(
        math.cos(math.radians(roll_deg)) * math.cos(math.radians(pitch_deg))))


def max_tilt(g, heave_mm=0.0, step=0.1):
    """Scan all tilt directions; return (max_tilt_deg, reason_at_limit).

    For each direction theta, push tilt outward until a pose becomes infeasible.
    The smallest breaking tilt over all directions is the guaranteed envelope;
    we report that (worst direction) plus which constraint bound it.
    """
    worst_dir_tilt = 999.0
    worst_reason = ""
    best_dir_tilt = 0.0
    theta = 0.0
    while theta < 360.0:
        ct, st = math.cos(math.radians(theta)), math.sin(math.radians(theta))
        last_ok = 0.0
        reason = "ok"
        phi = step
        while phi < 25.0:
            roll = phi * st
            pitch = phi * ct
            ok, _w, why = pose_feasible(g, roll, pitch, heave_mm)
            if not ok:
                reason = why
                break
            last_ok = tilt_magnitude_deg(roll, pitch)
            phi += step
        if last_ok < worst_dir_tilt:
            worst_dir_tilt = last_ok
            worst_reason = reason
        best_dir_tilt = max(best_dir_tilt, last_ok)
        theta += 5.0
    return worst_dir_tilt, best_dir_tilt, worst_reason


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def _report():
    print("=" * 72)
    print("TILT ENVELOPE ANALYSIS")
    print("=" * 72)

    base = Geometry()
    print("\n[1] Firmware geometry (as built in the .ino)")
    print(f"    platform rod radius = {base.platform_rod_radius_mm:.1f} mm "
          f"(= {2*base.platform_rod_radius_mm:.0f} mm diameter)")
    print(f"    base motor radius   = {base.base_motor_radius_mm:.1f} mm")
    print(f"    crank radius        = {base.crank_radius_mm:.1f} mm")
    print(f"    arm length          = {base.arm_length_mm:.1f} mm")
    print(f"    neutral top height  = {base.neutral_top_z_mm:.1f} mm")
    print(f"    rod-end limit       = {base.rod_end_limit_deg:.1f} deg")
    operating_heave = 20.0
    worst, best, reason = max_tilt(base, heave_mm=operating_heave)
    print(f"    operating heave                    : {operating_heave:.1f} mm")
    print(f"    --> guaranteed tilt (any direction): {worst:.2f} deg")
    print(f"    --> best-direction tilt            : {best:.2f} deg")
    print(f"    --> limited by                     : {reason}")

    print("\n[2] If 238 mm is actually the RADIUS (not diameter)")
    big = Geometry.arm_vertical(platform_rod_radius_mm=238.0,
                                crank_radius_mm=30.0,
                                arm_length_mm=110.0)
    worst, best, reason = max_tilt(big)
    print(f"    platform rod radius = 238.0 mm, base = {big.base_motor_radius_mm:.1f} mm")
    print(f"    --> guaranteed tilt: {worst:.2f} deg  (best {best:.2f}); limited by {reason}")

    print("\n[3] Arm-length sweep (crank horizontal + arm vertical held;")
    print("    so NEUTRAL_TOP_Z is lowered to equal the arm length)")
    for radius_label, R in (("R=119 mm (firmware)", 119.0), ("R=238 mm", 238.0)):
        print(f"\n    {radius_label}, crank=30 mm, rod-end limit=14 deg")
        print(f"    {'arm(mm)':>8} {'neutralZ':>9} {'guar.tilt':>10} {'best.tilt':>10}  limit")
        for arm in (70, 80, 90, 100, 110, 120, 130):
            g = Geometry.arm_vertical(R, 30.0, float(arm))
            worst, best, reason = max_tilt(g)
            print(f"    {arm:>8} {g.neutral_top_z_mm:>9.0f} "
                  f"{worst:>9.2f}  {best:>9.2f}   {reason}")

    print("\n[4] Crank-radius sensitivity at R=119, arm=110 (the real tilt driver)")
    print(f"    {'crank(mm)':>9} {'guar.tilt':>10} {'best.tilt':>10}  limit")
    for crank in (20, 25, 30, 35, 40):
        g = Geometry.arm_vertical(119.0, float(crank), 110.0)
        worst, best, reason = max_tilt(g)
        print(f"    {crank:>9} {worst:>9.2f}  {best:>9.2f}   {reason}")
    print()


if __name__ == "__main__":
    _report()
