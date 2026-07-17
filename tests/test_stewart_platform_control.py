from __future__ import annotations

import argparse
import math
import threading
import time
import unittest
from types import SimpleNamespace

from analysis.stewart_exp_kinematics import (
    NoSolutionError,
    calibrated_solution,
    experimental_geometry,
    plan_heave_transition,
    solve_pose_at_heave,
)
from stewart_platform_control_common import (
    EV_REL,
    EV_KEY,
    EV_SYN,
    BTN_LEFT,
    BTN_RIGHT,
    INPUT_EVENT,
    REL_X,
    REL_Y,
    SYN_REPORT,
    TrackballAccumulator,
    StewartPlatformController,
    add_common_arguments,
    decay_velocity,
    integrate_velocity,
)
from stewart_platform_control_position import (
    apply_position_counts,
    build_parser,
    command_or_retain_last_valid,
)
from stewart_platform_control_velocity import apply_velocity_counts
from arcade.stewart_tilt import (
    StewartTiltService,
    load_navigation_counts_per_step,
    navigation_steps,
)


def event(event_type: int, code: int, value: int) -> bytes:
    return INPUT_EVENT.pack(0, 0, event_type, code, value & 0xFFFFFFFF)


class SharedStewartControlTests(unittest.TestCase):
    @staticmethod
    def controller_at_zero() -> StewartPlatformController:
        controller = StewartPlatformController.__new__(StewartPlatformController)
        controller.args = SimpleNamespace(
            startup_heave=0.0,
            heave_step=0.25,
            max_heave_step=0.5,
            platform_step=1.0,
            heave_min=-15.0,
            heave_max=30.0,
        )
        controller.geometry = experimental_geometry()
        controller.step_offsets = (0, 0, 0)
        controller.current = plan_heave_transition(calibrated_solution(), 0.0)[-1]
        controller.armed = True

        class FakeLink:
            def __init__(self) -> None:
                self.targets = []

            def target(self, pose, offsets) -> None:
                self.targets.append((pose, offsets))

            def wait_idle(self):
                return None

        controller.link = FakeLink()
        return controller

    def test_position_x_maps_to_pitch_and_y_maps_to_roll(self) -> None:
        roll, pitch = apply_position_counts(
            0.0,
            0.0,
            10,
            -4,
            degrees_per_count=0.1,
            roll_sign=1.0,
            pitch_sign=1.0,
            max_tilt_deg=10.0,
        )
        self.assertAlmostEqual(roll, -0.4)
        self.assertAlmostEqual(pitch, 1.0)

    def test_default_startup_resumes_held_pose(self) -> None:
        parser = argparse.ArgumentParser()
        add_common_arguments(parser)
        args = parser.parse_args([])
        self.assertFalse(args.zero_on_start)

    def test_position_controller_live_defaults(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.max_tilt, 9.0)
        self.assertEqual(args.degrees_per_count, 0.03)
        self.assertEqual(args.crank_accel, 500.0)
        self.assertEqual(args.crank_speed, 60.0)
        self.assertEqual(args.deadband, 0)
        self.assertEqual(args.rate_hz, 90.0)
        self.assertTrue(args.yes)
        self.assertFalse(build_parser().parse_args(["--no-yes"]).yes)

    def test_unreachable_position_retains_last_valid_target(self) -> None:
        class BoundaryController:
            current = SimpleNamespace(roll_deg=-4.5, pitch_deg=6.25)
            rebased = False

            @staticmethod
            def at_target(_roll: float, _pitch: float) -> bool:
                return False

            @staticmethod
            def command_toward(_roll: float, _pitch: float) -> None:
                raise NoSolutionError("outside continuous workspace")

            @classmethod
            def hold_and_rebase(cls):
                cls.rebased = True
                cls.current = SimpleNamespace(roll_deg=0.25, pitch_deg=1.5)
                return cls.current

        roll, pitch, warning = command_or_retain_last_valid(
            BoundaryController(), -6.0, 7.0
        )
        self.assertEqual((roll, pitch), (0.25, 1.5))
        self.assertTrue(BoundaryController.rebased)
        self.assertIn("outside continuous workspace", warning or "")

    def test_position_target_is_radially_clamped(self) -> None:
        roll, pitch = apply_position_counts(
            0.0,
            0.0,
            100,
            100,
            degrees_per_count=1.0,
            roll_sign=1.0,
            pitch_sign=1.0,
            max_tilt_deg=10.0,
        )
        self.assertAlmostEqual(math.hypot(roll, pitch), 10.0)

    def test_swipe_adds_angular_velocity_on_matching_axes(self) -> None:
        roll_velocity, pitch_velocity = apply_velocity_counts(
            0.0,
            0.0,
            8,
            -3,
            velocity_per_count=0.5,
            roll_sign=1.0,
            pitch_sign=1.0,
            max_velocity_deg_s=30.0,
        )
        self.assertAlmostEqual(roll_velocity, -1.5)
        self.assertAlmostEqual(pitch_velocity, 4.0)

    def test_velocity_decay_is_exponential(self) -> None:
        roll_velocity, pitch_velocity = decay_velocity(10.0, -5.0, 0.5, 0.5)
        self.assertAlmostEqual(roll_velocity, 10.0 / math.e)
        self.assertAlmostEqual(pitch_velocity, -5.0 / math.e)

    def test_outward_velocity_is_removed_at_tilt_limit(self) -> None:
        roll, pitch, roll_velocity, pitch_velocity = integrate_velocity(
            9.9, 0.0, 10.0, 2.0, 0.1, 10.0
        )
        self.assertAlmostEqual(math.hypot(roll, pitch), 10.0)
        radial_velocity = roll_velocity * roll + pitch_velocity * pitch
        self.assertLessEqual(radial_velocity, 1e-8)

    def test_partial_trackball_frame_is_not_published_early(self) -> None:
        accumulator = TrackballAccumulator()
        accumulator.feed_bytes(event(EV_REL, REL_X, 7))
        self.assertEqual(accumulator.pop(), (0, 0))
        accumulator.feed_bytes(
            event(EV_REL, REL_Y, -4) + event(EV_SYN, SYN_REPORT, 0)
        )
        self.assertEqual(accumulator.pop(), (7, -4))

    def test_trackball_buttons_publish_once_per_press_frame(self) -> None:
        accumulator = TrackballAccumulator()
        accumulator.feed_bytes(event(EV_KEY, BTN_RIGHT, 1))
        self.assertEqual(accumulator.pop_buttons(), (0, 0))
        accumulator.feed_bytes(event(EV_SYN, SYN_REPORT, 0))
        self.assertEqual(accumulator.pop_buttons(), (0, 1))
        accumulator.feed_bytes(
            event(EV_KEY, BTN_RIGHT, 0)
            + event(EV_KEY, BTN_LEFT, 1)
            + event(EV_SYN, SYN_REPORT, 0)
        )
        self.assertEqual(accumulator.pop_buttons(), (1, 0))

    def test_vertical_trackball_motion_becomes_menu_navigation(self) -> None:
        counts = load_navigation_counts_per_step()
        self.assertEqual(counts, 36)
        self.assertEqual(navigation_steps(15, 0, counts), (0, 0, 15))
        self.assertEqual(navigation_steps(24, 15, counts), (0, 1, 3))
        self.assertEqual(navigation_steps(-73, 0, counts), (2, 0, -1))

    def test_symmetric_calibration_transition_uses_one_branch(self) -> None:
        final = plan_heave_transition(calibrated_solution(), 0.0)[-1]
        self.assertEqual(final.branch_index, (0, 0, 0))
        self.assertEqual(final.steps[0], final.steps[1])
        self.assertEqual(final.steps[1], final.steps[2])

    def test_exact_zero_requires_canonical_startup_heave(self) -> None:
        controller = self.controller_at_zero()
        controller.current = solve_pose_at_heave(
            experimental_geometry(),
            0.0,
            0.0,
            0.5,
            (172.0, 172.0, 172.0),
            estimate_torque=False,
        )
        assert controller.current is not None
        self.assertFalse(controller.at_target(0.0, 0.0))
        controller.current = plan_heave_transition(calibrated_solution(), 0.0)[-1]
        self.assertTrue(controller.at_target(0.0, 0.0))

    def test_zero_pose_round_trip_returns_to_canonical_heave(self) -> None:
        controller = self.controller_at_zero()
        controller.move_to(6.0, 4.0)
        self.assertFalse(controller.at_target(0.0, 0.0))
        final = controller.move_to(0.0, 0.0)
        self.assertEqual((final.roll_deg, final.pitch_deg), (0.0, 0.0))
        self.assertAlmostEqual(final.heave_mm, 0.0)
        self.assertTrue(controller.at_target(0.0, 0.0))


class ArcadeStewartTiltTests(unittest.TestCase):
    class FakeController:
        def __init__(self) -> None:
            self.current = SimpleNamespace(roll_deg=0.0, pitch_deg=0.0)
            self.link = object()
            self.armed = False
            self.open_args = None
            self.commands: list[tuple[float, float]] = []
            self.holds = 0
            self.closed = False

        def open(self, **kwargs) -> None:
            self.open_args = kwargs

        def move_to(self, roll: float, pitch: float):
            self.current = SimpleNamespace(roll_deg=roll, pitch_deg=pitch)
            self.armed = True
            return self.current

        def at_target(self, roll: float, pitch: float) -> bool:
            return (
                self.current.roll_deg == roll
                and self.current.pitch_deg == pitch
            )

        def command_toward(self, roll: float, pitch: float):
            self.commands.append((roll, pitch))
            self.current = SimpleNamespace(roll_deg=roll, pitch_deg=pitch)
            return self.current

        def hold_and_rebase(self):
            self.holds += 1
            self.armed = False
            return self.current

        def hold_and_close(self) -> None:
            self.closed = True

    class FakeTrackball:
        def __init__(self) -> None:
            self.opened = False
            self.closed = False
            self._lock = threading.Lock()
            self._events: list[tuple[int, int]] = []
            self._buttons: list[tuple[int, int]] = []

        def open(self) -> None:
            self.opened = True

        def close(self) -> None:
            self.closed = True

        def wait(self, timeout: float) -> None:
            time.sleep(min(timeout, 0.002))

        def pop(self) -> tuple[int, int]:
            with self._lock:
                return self._events.pop(0) if self._events else (0, 0)

        def push(self, dx: int, dy: int) -> None:
            with self._lock:
                self._events.append((dx, dy))

        def pop_buttons(self) -> tuple[int, int]:
            with self._lock:
                return self._buttons.pop(0) if self._buttons else (0, 0)

        def push_buttons(self, left: int = 0, right: int = 0) -> None:
            with self._lock:
                self._buttons.append((left, right))

    @staticmethod
    def wait_until(predicate, timeout: float = 0.5) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("condition did not become true")

    def test_arcade_tilt_only_commands_during_level_scope(self) -> None:
        controller = self.FakeController()
        controller.current = SimpleNamespace(roll_deg=2.0, pitch_deg=-1.0)
        trackball = self.FakeTrackball()
        service = StewartTiltService(
            controller=controller,
            trackball=trackball,
        )
        service.start()
        self.assertEqual(
            controller.open_args,
            {"arm": False, "calibrate_if_needed": False},
        )
        self.assertFalse(service.status().active)

        trackball.push(8, -4)
        time.sleep(0.03)
        self.assertEqual(controller.commands, [])

        trackball.push(0, 72)
        self.wait_until(lambda: service.status().navigation_down == 1)

        service.set_active(True)
        self.wait_until(lambda: service.status().active)
        self.assertEqual(
            (controller.current.roll_deg, controller.current.pitch_deg),
            (0.0, 0.0),
        )
        trackball.push(8, -4)
        self.wait_until(lambda: bool(controller.commands))

        trackball.push_buttons(right=1)
        self.wait_until(lambda: service.status().confirm_presses == 1)
        trackball.push_buttons(left=1)
        self.wait_until(lambda: service.status().back_presses == 1)

        service.set_active(False)
        self.wait_until(lambda: not service.status().active)
        self.assertGreaterEqual(controller.holds, 1)
        service.stop()
        self.assertTrue(controller.closed)
        self.assertTrue(trackball.closed)


if __name__ == "__main__":
    unittest.main()
