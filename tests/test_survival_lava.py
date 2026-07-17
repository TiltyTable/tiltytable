from __future__ import annotations

import unittest

from arcade.survival_lava import (
    DEFAULT_PIT_CONFIRM_SECONDS,
    FLOOR_COLOR,
    VISITED_COLOR,
    WARN_OFF_COLOR,
    SurvivalLavaSession,
    SurvivalParams,
    tick_survival_lava,
)


class SurvivalLavaLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.row_col = {"F6": (5, 5), "G6": (5, 6), "H6": (5, 7)}
        self.params = SurvivalParams(
            survival_seconds=30.0,
            dwell_seconds=2.0,
            warn_seconds=1.5,
            points_per_tile=25,
            floor_color=FLOOR_COLOR,
            settle_seconds=0.0,
            pit_confirm_seconds=DEFAULT_PIT_CONFIRM_SECONDS,
        )

    def test_survival_win_when_timer_elapses(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        cells = ["F6", "G6", "H6", "G6"]
        for second in range(30):
            tick_survival_lava(session, cells[second % len(cells)], float(second), self.row_col)
        result = tick_survival_lava(session, None, 30.0, self.row_col)
        self.assertTrue(result.survived)
        self.assertFalse(result.ball_on_lava)
        self.assertGreaterEqual(result.visited_count, 3)

    def test_touch_turns_yellow_immediately(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        result = tick_survival_lava(session, "F6", 0.0, self.row_col)
        self.assertEqual(len(result.hardware_updates), 1)
        self.assertEqual(result.hardware_updates[0]["color"], VISITED_COLOR)
        self.assertEqual(result.hardware_updates[0]["value"], 0)
        self.assertTrue(result.hardware_updates[0]["leds_only"])
        self.assertFalse(result.ball_cell_heating)

    def test_unchanged_warning_frame_emits_no_hardware_write(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        tick_survival_lava(session, "F6", 2.0, self.row_col)
        result = tick_survival_lava(session, "F6", 2.05, self.row_col)
        self.assertEqual(result.hardware_updates, [])

    def test_warn_phase_after_arm_delay(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        result = tick_survival_lava(session, "G6", 2.0, self.row_col)
        warn_updates = [u for u in result.hardware_updates if u["key"] == "F6"]
        self.assertEqual(len(warn_updates), 1)
        self.assertEqual(warn_updates[0]["color"], "#FF0000")
        self.assertTrue(result.ball_cell_heating)

    def test_warn_blinks_red_not_floor(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        tick_survival_lava(session, "F6", 2.0, self.row_col)
        result = tick_survival_lava(session, "F6", 2.12, self.row_col)
        f6_updates = [u for u in result.hardware_updates if u["key"] == "F6"]
        self.assertEqual(f6_updates[0]["color"], WARN_OFF_COLOR)

    def test_sink_after_arm_delay_plus_warn(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        result = tick_survival_lava(session, "F6", 3.6, self.row_col)
        sunk = [u for u in result.hardware_updates if u["key"] == "F6" and u["value"] == -1]
        self.assertEqual(len(sunk), 1)
        self.assertEqual(sunk[0]["color"], "#FF0000")

    def test_ball_on_sunk_tile_is_loss(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        tick_survival_lava(session, "F6", 3.6, self.row_col)
        result = tick_survival_lava(session, "F6", 3.8, self.row_col)
        self.assertFalse(result.ball_on_lava)
        result = tick_survival_lava(session, "F6", 4.2, self.row_col)
        self.assertTrue(result.ball_on_lava)

    def test_flicker_over_pit_does_not_lose(self) -> None:
        """Single-tick Kinect overlap on a sunk tile must not game over."""
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        tick_survival_lava(session, "F6", 3.6, self.row_col)
        result = tick_survival_lava(session, "F6", 3.62, self.row_col)
        self.assertFalse(result.ball_on_lava)
        result = tick_survival_lava(session, "G6", 3.64, self.row_col)
        self.assertFalse(result.ball_on_lava)

    def test_sustained_pit_with_dropout_grace_still_loses(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        tick_survival_lava(session, "F6", 3.6, self.row_col)
        tick_survival_lava(session, "F6", 3.7, self.row_col)
        tick_survival_lava(session, None, 3.75, self.row_col)
        result = tick_survival_lava(session, "F6", 4.25, self.row_col)
        self.assertTrue(result.ball_on_lava)

    def test_low_confidence_does_not_confirm_pit(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        tick_survival_lava(session, "F6", 3.6, self.row_col)
        for t in (3.7, 3.8, 3.9, 4.0, 4.1, 4.2):
            result = tick_survival_lava(
                session, "F6", t, self.row_col, tracking_confidence=0.5
            )
            self.assertFalse(result.ball_on_lava)

    def test_leaving_tile_continues_warning_sequence(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        tick_survival_lava(session, "F6", 2.0, self.row_col)
        result = tick_survival_lava(session, "G6", 2.1, self.row_col)
        cooled = [u for u in result.hardware_updates if u["key"] == "F6" and u["color"] == VISITED_COLOR]
        self.assertEqual(cooled, [])
        self.assertTrue(result.ball_cell_heating)
        result = tick_survival_lava(session, "G6", 3.6, self.row_col)
        sunk = [u for u in result.hardware_updates if u["key"] == "F6" and u["value"] == -1]
        self.assertEqual(len(sunk), 1)

    def test_kinect_dropout_does_not_reset_timers(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        tick_survival_lava(session, "F6", 1.0, self.row_col)
        tick_survival_lava(session, None, 1.1, self.row_col)
        result = tick_survival_lava(session, "F6", 2.0, self.row_col)
        warn_updates = [u for u in result.hardware_updates if u["key"] == "F6" and u["color"] == "#FF0000"]
        self.assertEqual(len(warn_updates), 1)

    def test_first_detection_does_not_backdate_touch(self) -> None:
        """Ball on cell since placement must not inherit touch time from session start."""
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        result = tick_survival_lava(session, "F6", 5.0, self.row_col)
        self.assertFalse(result.ball_on_lava)
        self.assertFalse(result.ball_cell_heating)
        result = tick_survival_lava(session, "F6", 6.9, self.row_col)
        self.assertFalse(result.ball_on_lava)
        self.assertFalse(result.ball_cell_heating)
        result = tick_survival_lava(session, "F6", 7.0, self.row_col)
        self.assertTrue(result.ball_cell_heating)

    def test_unique_tiles_visited_for_scoring(self) -> None:
        session = SurvivalLavaSession(params=self.params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        result = tick_survival_lava(session, "G6", 1.0, self.row_col)
        self.assertEqual(result.visited_count, 2)

    def test_kinect_jitter_does_not_reset_touch_timer(self) -> None:
        """Brief neighbor flicker must not reset an armed tile's touch clock."""
        params = SurvivalParams(
            survival_seconds=30.0,
            dwell_seconds=2.0,
            warn_seconds=1.5,
            points_per_tile=25,
            floor_color=FLOOR_COLOR,
            settle_seconds=0.4,
        )
        session = SurvivalLavaSession(params=params, started_at=0.0)
        tick_survival_lava(session, "F6", 0.0, self.row_col)
        tick_survival_lava(session, "F6", 0.5, self.row_col)
        tick_survival_lava(session, "G6", 2.1, self.row_col)
        tick_survival_lava(session, "F6", 2.22, self.row_col)
        result = tick_survival_lava(session, "F6", 2.5, self.row_col)
        self.assertTrue(result.ball_cell_heating)
        warn_updates = [u for u in result.hardware_updates if u["key"] == "F6"]
        self.assertEqual(len(warn_updates), 1)
        self.assertEqual(warn_updates[0]["color"], "#FF0000")


if __name__ == "__main__":
    unittest.main()
