"""Experimental branch-aware Stewart IK with free common heave.

This module is intentionally separate from the production fixed-heave model.
It exposes both crank closures, unwraps them toward the current physical crank
positions, and searches common heave for a continuous path.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from analysis.tilt_kinematics import Geometry

STEPS_PER_CRANK_REV = 16_000  # MCS=4, 20:1 gearbox
CALIBRATE_CRANK_DEG = 90.0
DEFAULT_PAYLOAD_KG = 22.6796  # 50 lb
PREFERRED_DEAD_CENTER_MARGIN_DEG = 15.0
EXP_LEG_AZIMUTH_DEG = (120.0, 240.0, 0.0)  # axis 2 defines cardinal 0°


def experimental_geometry() -> Geometry:
    """As-built symmetric geometry rotated so motor axis 2 is cardinal."""
    return Geometry(leg_azimuth_deg=EXP_LEG_AZIMUTH_DEG)


@dataclass(frozen=True)
class CrankBranch:
    wrapped_deg: float
    advisory_joint_deg: float
    closure_margin_mm: float
    crank_pin: tuple[float, float, float]
    arm_unit: tuple[float, float, float]


@dataclass(frozen=True)
class PoseSolution:
    roll_deg: float
    pitch_deg: float
    heave_mm: float
    crank_deg: tuple[float, float, float]
    branch_index: tuple[int, int, int]
    closure_margin_mm: float
    worst_advisory_joint_deg: float
    max_crank_delta_deg: float
    dead_center_margin_deg: float
    max_static_torque_nm: float

    @property
    def steps(self) -> tuple[int, int, int]:
        return tuple(crank_deg_to_steps(value) for value in self.crank_deg)


class NoSolutionError(ValueError):
    pass


def crank_deg_to_steps(crank_unwrapped_deg: float) -> int:
    """Absolute experimental steps; zero is calibrated crank-straight-up."""
    return round(
        (crank_unwrapped_deg - CALIBRATE_CRANK_DEG)
        * STEPS_PER_CRANK_REV
        / 360.0
    )


def steps_to_crank_deg(steps: int) -> float:
    return CALIBRATE_CRANK_DEG + steps * 360.0 / STEPS_PER_CRANK_REV


def unwrap_toward(candidate_deg: float, reference_deg: float) -> float:
    """Return the equivalent candidate nearest an unwrapped reference."""
    turns = round((reference_deg - candidate_deg) / 360.0)
    return candidate_deg + 360.0 * turns


def _rotate_roll_pitch(
    point: tuple[float, float, float],
    roll_rad: float,
    pitch_rad: float,
) -> tuple[float, float, float]:
    cr, sr = math.cos(roll_rad), math.sin(roll_rad)
    cp, sp = math.cos(pitch_rad), math.sin(pitch_rad)
    x1 = point[0]
    y1 = point[1] * cr - point[2] * sr
    z1 = point[1] * sr + point[2] * cr
    return x1 * cp + z1 * sp, y1, -x1 * sp + z1 * cp


def top_joint_position(
    geometry: Geometry,
    leg: int,
    roll_deg: float,
    pitch_deg: float,
    heave_mm: float,
) -> tuple[float, float, float]:
    azimuth = math.radians(geometry.leg_azimuth_deg[leg])
    neutral = (
        geometry.platform_rod_radius_mm * math.cos(azimuth),
        geometry.platform_rod_radius_mm * math.sin(azimuth),
        0.0,
    )
    rotated = _rotate_roll_pitch(
        neutral, math.radians(roll_deg), math.radians(pitch_deg)
    )
    return (
        rotated[0],
        rotated[1],
        rotated[2] + geometry.neutral_top_z_mm + heave_mm,
    )


def solve_crank_branches(
    geometry: Geometry,
    leg: int,
    top: tuple[float, float, float],
) -> tuple[CrankBranch, ...]:
    """Return every crank/arm triangle closure for one leg."""
    azimuth = math.radians(geometry.leg_azimuth_deg[leg])
    ux, uy = math.cos(azimuth), math.sin(azimuth)
    vx, vy = -math.sin(azimuth), math.cos(azimuth)

    top_r = top[0] * ux + top[1] * uy
    top_t = top[0] * vx + top[1] * vy
    arm_sq = geometry.arm_length_mm**2
    tangential_margin = geometry.arm_length_mm - abs(top_t)
    if tangential_margin < 0.0:
        return ()

    effective_arm = math.sqrt(max(0.0, arm_sq - top_t * top_t))
    radial = top_r - geometry.base_motor_radius_mm
    vertical = top[2]
    distance = math.hypot(radial, vertical)
    if distance < 1e-9:
        return ()

    low_reach = abs(geometry.arm_length_mm - geometry.crank_radius_mm)
    high_reach = geometry.arm_length_mm + geometry.crank_radius_mm
    radial_margin = min(distance - low_reach, high_reach - distance)
    closure_margin = min(tangential_margin, radial_margin)
    if closure_margin < -1e-7:
        return ()

    cosine = (
        distance * distance
        + geometry.crank_radius_mm**2
        - effective_arm**2
    ) / (2.0 * geometry.crank_radius_mm * distance)
    if cosine < -1.0 - 1e-8 or cosine > 1.0 + 1e-8:
        return ()
    cosine = max(-1.0, min(1.0, cosine))

    phi = math.atan2(vertical, radial)
    alpha = math.acos(cosine)
    branches: list[CrankBranch] = []
    for crank_rad in (phi + alpha, phi - alpha):
        pin = (
            geometry.base_motor_radius_mm * ux
            + geometry.crank_radius_mm * math.cos(crank_rad) * ux,
            geometry.base_motor_radius_mm * uy
            + geometry.crank_radius_mm * math.cos(crank_rad) * uy,
            geometry.crank_radius_mm * math.sin(crank_rad),
        )
        dx, dy, dz = top[0] - pin[0], top[1] - pin[1], top[2] - pin[2]
        arm_length = math.sqrt(dx * dx + dy * dy + dz * dz)
        advisory = math.degrees(math.atan2(math.hypot(dx, dy), dz))
        branches.append(
            CrankBranch(
                wrapped_deg=math.degrees(crank_rad),
                advisory_joint_deg=advisory,
                closure_margin_mm=max(0.0, closure_margin),
                crank_pin=pin,
                arm_unit=(dx / arm_length, dy / arm_length, dz / arm_length),
            )
        )
    return tuple(branches)


def distance_from_vertical_dead_center(crank_deg: float) -> float:
    """Angular distance from either +90° or -90° crank dead center."""
    return abs((crank_deg - 90.0 + 90.0) % 180.0 - 90.0)


def estimate_static_motor_torque(
    geometry: Geometry,
    roll_deg: float,
    pitch_deg: float,
    heave_mm: float,
    selected: Sequence[CrankBranch],
    payload_kg: float,
) -> float:
    """Estimate worst crank torque for gravity using a 3-leg wrench solve."""
    columns: list[list[float]] = []
    top_center_z = geometry.neutral_top_z_mm + heave_mm
    for leg, branch in enumerate(selected):
        top = top_joint_position(geometry, leg, roll_deg, pitch_deg, heave_mm)
        radius = np.array(
            [top[0], top[1], top[2] - top_center_z], dtype=float
        )
        arm = np.array(branch.arm_unit, dtype=float)
        moment = np.cross(radius, arm)
        columns.append([*arm, *moment])

    wrench_matrix = np.array(columns, dtype=float).T
    desired_wrench = np.array(
        [0.0, 0.0, payload_kg * 9.80665, 0.0, 0.0, 0.0], dtype=float
    )
    forces, *_ = np.linalg.lstsq(wrench_matrix, desired_wrench, rcond=None)

    torques: list[float] = []
    for leg, (branch, axial_force) in enumerate(zip(selected, forces)):
        azimuth = math.radians(geometry.leg_azimuth_deg[leg])
        motor = np.array(
            [
                geometry.base_motor_radius_mm * math.cos(azimuth),
                geometry.base_motor_radius_mm * math.sin(azimuth),
                0.0,
            ]
        )
        crank = (np.array(branch.crank_pin) - motor) / 1000.0
        force = -float(axial_force) * np.array(branch.arm_unit)
        tangential_axis = np.array(
            [-math.sin(azimuth), math.cos(azimuth), 0.0]
        )
        torque = abs(float(np.dot(np.cross(crank, force), tangential_axis)))
        torques.append(torque)
    return max(torques)


def solve_pose_at_heave(
    geometry: Geometry,
    roll_deg: float,
    pitch_deg: float,
    heave_mm: float,
    previous_crank_deg: Sequence[float],
    payload_kg: float = DEFAULT_PAYLOAD_KG,
    estimate_torque: bool = True,
) -> PoseSolution | None:
    """Choose the continuous combination among all 2^3 crank branches."""
    if len(previous_crank_deg) != 3:
        raise ValueError("previous_crank_deg must contain three axes")

    per_leg: list[tuple[CrankBranch, ...]] = []
    for leg in range(3):
        branches = solve_crank_branches(
            geometry,
            leg,
            top_joint_position(geometry, leg, roll_deg, pitch_deg, heave_mm),
        )
        if not branches:
            return None
        per_leg.append(branches)

    best: tuple[tuple[float, float, float], PoseSolution] | None = None
    for indices in itertools.product(*(range(len(branches)) for branches in per_leg)):
        selected = [per_leg[leg][indices[leg]] for leg in range(3)]
        unwrapped = tuple(
            unwrap_toward(selected[leg].wrapped_deg, previous_crank_deg[leg])
            for leg in range(3)
        )
        deltas = tuple(
            abs(unwrapped[leg] - previous_crank_deg[leg]) for leg in range(3)
        )
        dead_center_margin = min(
            distance_from_vertical_dead_center(value) for value in unwrapped
        )
        max_torque = (
            estimate_static_motor_torque(
                geometry,
                roll_deg,
                pitch_deg,
                heave_mm,
                selected,
                payload_kg,
            )
            if estimate_torque
            else 0.0
        )
        solution = PoseSolution(
            roll_deg=roll_deg,
            pitch_deg=pitch_deg,
            heave_mm=heave_mm,
            crank_deg=unwrapped,
            branch_index=tuple(indices),
            closure_margin_mm=min(item.closure_margin_mm for item in selected),
            worst_advisory_joint_deg=max(
                item.advisory_joint_deg for item in selected
            ),
            max_crank_delta_deg=max(deltas),
            dead_center_margin_deg=dead_center_margin,
            max_static_torque_nm=max_torque,
        )
        score = (
            solution.max_crank_delta_deg,
            sum(deltas),
            -solution.dead_center_margin_deg,
            solution.max_static_torque_nm,
            -solution.closure_margin_mm,
        )
        if best is None or score < best[0]:
            best = (score, solution)
    return None if best is None else best[1]


def optimize_heave(
    geometry: Geometry,
    roll_deg: float,
    pitch_deg: float,
    previous: PoseSolution,
    *,
    heave_min_mm: float = -15.0,
    heave_max_mm: float = 30.0,
    heave_step_mm: float = 0.25,
    max_heave_step_mm: float | None = 1.0,
    payload_kg: float = DEFAULT_PAYLOAD_KG,
    preferred_dead_center_margin_deg: float = PREFERRED_DEAD_CENTER_MARGIN_DEG,
    estimate_torque: bool = True,
    objective: str = "margin",
) -> PoseSolution | None:
    """Find a closure-valid heave while preserving crank/heave continuity."""
    if heave_step_mm <= 0:
        raise ValueError("heave_step_mm must be positive")
    low, high = heave_min_mm, heave_max_mm
    if max_heave_step_mm is not None:
        low = max(low, previous.heave_mm - max_heave_step_mm)
        high = min(high, previous.heave_mm + max_heave_step_mm)
    if low > high:
        return None

    first = math.ceil((low - 1e-9) / heave_step_mm)
    last = math.floor((high + 1e-9) / heave_step_mm)
    best: tuple[tuple[float, float, float], PoseSolution] | None = None
    for index in range(first, last + 1):
        heave = index * heave_step_mm
        solution = solve_pose_at_heave(
            geometry,
            roll_deg,
            pitch_deg,
            heave,
            previous.crank_deg,
            payload_kg,
            estimate_torque,
        )
        if solution is None:
            continue
        heave_delta = abs(heave - previous.heave_mm)
        dead_center_penalty = max(
            0.0,
            preferred_dead_center_margin_deg - solution.dead_center_margin_deg,
        )
        if objective == "agile":
            closure_penalty = max(0.0, 2.0 - solution.closure_margin_mm) * 5.0
            agility_cost = (
                solution.max_crank_delta_deg
                + 0.25 * heave_delta
                + closure_penalty
                + 0.10 * dead_center_penalty
                + 0.05 * solution.max_static_torque_nm
            )
            score = (
                agility_cost,
                solution.max_crank_delta_deg,
                heave_delta,
                -solution.closure_margin_mm,
            )
        elif objective == "margin":
            score = (
                -solution.closure_margin_mm,
                solution.max_crank_delta_deg,
                heave_delta,
                dead_center_penalty,
                solution.max_static_torque_nm,
            )
        else:
            raise ValueError("objective must be agile or margin")
        if best is None or score < best[0]:
            best = (score, solution)
    return None if best is None else best[1]


def calibrated_solution() -> PoseSolution:
    return PoseSolution(
        roll_deg=0.0,
        pitch_deg=0.0,
        heave_mm=30.0,
        crank_deg=(90.0, 90.0, 90.0),
        branch_index=(0, 0, 0),
        closure_margin_mm=0.0,
        worst_advisory_joint_deg=0.0,
        max_crank_delta_deg=0.0,
        dead_center_margin_deg=0.0,
        max_static_torque_nm=0.0,
    )


def linear_targets(
    start_roll: float,
    start_pitch: float,
    end_roll: float,
    end_pitch: float,
    points: int,
) -> list[tuple[float, float]]:
    if points < 1:
        raise ValueError("points must be >= 1")
    return [
        (
            start_roll + (end_roll - start_roll) * index / points,
            start_pitch + (end_pitch - start_pitch) * index / points,
        )
        for index in range(1, points + 1)
    ]


def plan_heave_transition(
    initial: PoseSolution,
    target_heave_mm: float,
    *,
    geometry: Geometry | None = None,
    step_mm: float = 0.25,
    max_crank_step_deg: float = 12.0,
) -> list[PoseSolution]:
    """Move common heave at fixed roll/pitch with branch continuity."""
    if step_mm <= 0:
        raise ValueError("step_mm must be positive")
    geometry = geometry or experimental_geometry()
    distance = target_heave_mm - initial.heave_mm
    count = max(1, math.ceil(abs(distance) / step_mm))
    previous = initial
    planned: list[PoseSolution] = []
    for index in range(1, count + 1):
        heave = initial.heave_mm + distance * index / count
        solution = solve_pose_at_heave(
            geometry,
            initial.roll_deg,
            initial.pitch_deg,
            heave,
            previous.crank_deg,
            estimate_torque=False,
        )
        if solution is None:
            raise NoSolutionError(f"no level closure at heave={heave:.3f}")
        if solution.max_crank_delta_deg > max_crank_step_deg:
            raise NoSolutionError(
                f"heave transition crank jump "
                f"{solution.max_crank_delta_deg:.2f}°"
            )
        planned.append(solution)
        previous = solution
    return planned


def circle_targets(radius_deg: float, points: int) -> list[tuple[float, float]]:
    if radius_deg <= 0 or points < 12:
        raise ValueError("radius must be positive and points >= 12")
    return [
        (
            radius_deg * math.sin(2.0 * math.pi * index / points),
            radius_deg * math.cos(2.0 * math.pi * index / points),
        )
        for index in range(points + 1)
    ]


def plan_targets(
    targets: Iterable[tuple[float, float]],
    *,
    geometry: Geometry | None = None,
    initial: PoseSolution | None = None,
    heave_min_mm: float = -15.0,
    heave_max_mm: float = 30.0,
    heave_step_mm: float = 0.25,
    max_heave_step_mm: float = 0.25,
    max_crank_step_deg: float = 12.0,
    payload_kg: float = DEFAULT_PAYLOAD_KG,
    preferred_dead_center_margin_deg: float = PREFERRED_DEAD_CENTER_MARGIN_DEG,
    estimate_torque: bool = True,
    objective: str = "margin",
) -> list[PoseSolution]:
    geometry = geometry or experimental_geometry()
    previous = initial or calibrated_solution()
    planned: list[PoseSolution] = []
    for waypoint, (roll, pitch) in enumerate(targets, start=1):
        solution = optimize_heave(
            geometry,
            roll,
            pitch,
            previous,
            heave_min_mm=heave_min_mm,
            heave_max_mm=heave_max_mm,
            heave_step_mm=heave_step_mm,
            max_heave_step_mm=max_heave_step_mm,
            payload_kg=payload_kg,
            preferred_dead_center_margin_deg=preferred_dead_center_margin_deg,
            estimate_torque=estimate_torque,
            objective=objective,
        )
        if solution is None:
            raise NoSolutionError(
                f"no closure at waypoint {waypoint}: roll={roll:.3f}, "
                f"pitch={pitch:.3f}"
            )
        if solution.max_crank_delta_deg > max_crank_step_deg:
            raise NoSolutionError(
                f"crank jump {solution.max_crank_delta_deg:.2f}° exceeds "
                f"{max_crank_step_deg:.2f}° at waypoint {waypoint}"
            )
        planned.append(solution)
        previous = solution
    return planned


def plan_circle(
    radius_deg: float,
    *,
    geometry: Geometry | None = None,
    initial: PoseSolution | None = None,
    ramp_points: int = 240,
    circle_points: int = 240,
    **kwargs,
) -> list[PoseSolution]:
    start = initial or calibrated_solution()
    targets = linear_targets(
        start.roll_deg, start.pitch_deg, 0.0, radius_deg, ramp_points
    )
    targets.extend(circle_targets(radius_deg, circle_points)[1:])
    return plan_targets(targets, geometry=geometry, initial=start, **kwargs)


def endpoint_heave_range(
    roll_deg: float,
    pitch_deg: float,
    *,
    geometry: Geometry | None = None,
    heave_min_mm: float = -15.0,
    heave_max_mm: float = 30.0,
    heave_step_mm: float = 0.25,
) -> tuple[float, float] | None:
    """Closure-only endpoint heave range, independent of path continuity."""
    geometry = geometry or experimental_geometry()
    valid: list[float] = []
    count = round((heave_max_mm - heave_min_mm) / heave_step_mm)
    for index in range(count + 1):
        heave = heave_min_mm + index * heave_step_mm
        if solve_pose_at_heave(
            geometry,
            roll_deg,
            pitch_deg,
            heave,
            (90.0, 90.0, 90.0),
            estimate_torque=False,
        ):
            valid.append(heave)
    if not valid:
        return None
    return min(valid), max(valid)
