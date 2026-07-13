# Progress

## Completed
- [x] Arcade cabinet copy pass: player-facing actions only, concise level/placement/play/result screens, 854×480 tuning, and persistent UI rule.
- [x] Arcade V1 web app: 854×480 gauntlet/practice UI, 3 levels, score/timer/retries, JSON leaderboard, synthesized audio, and module-grid adapter.
- [x] LED remount to 9 independent strands + firmware flash + `led_grid_config` remap (2026-07-12).
- [x] Migrated repo to public `TiltyTable/tiltytable` with project-neutral authorship.
- [x] Consolidated calibration mega-suite + configs into `calibration/`.
- [x] Documented trusted 12×12 LED/servo global maps (`module-grid-mapping` rule); cleared stale orientation-mismatch guidance in `tilt_table_cli`.
- [x] Remapped grid origin to `(0,0)` = top-left (row↓ col→).
- [x] Module servos: pulse-then-release only; never leave PWM latched (hardware-safety rule).
- [x] Archived obsolete Arduino sketches under `arduino/archive/`; live modules board = `servo_calib`.
- [x] `game_runner.py` + maps schema; firmware per-channel 3s HOLD timeout on R4.
- [x] LED palette (9 game colors) + per-tile gain cal tool; game_runner uses them.
- [x] Archived old `scratch/tilt-table` sandbox.
- [x] Documented correct board roles (R3 Stewart / R4 modules).
- [x] Stewart pinout PLS 2/7/11, DIR 3/8/12, ENA 4/9/13; Amazon wire colors (Brown=COM, Gray=DIR, Yellow=PLS, Blue=ENA).
- [x] Installed udev aliases `/dev/arduino-stewart` and `/dev/arduino-modules` on Jetson.
- [x] Installed Jetson Python deps into `.venv`.
- [x] Non-motion smoke test: R4 servo+LED firmware responds (`?`, `LX`, `X`).
- [x] Scaffolded `camera/mindvision_capture.py` + README for HT-SUA134GM; Arducam UVC path via `camera/uvc_focus_stream.py`.
- [x] Stewart `calibrate` gate + AccelStepper `setMinPulseWidth(20)` + MCS=32 → 6400 steps/rev.
- [x] Interactive `stewart_cli.py` for Stewart serial sessions.

## In Progress
- [ ] Live roller-ball tilt session with `roller_ball.py` (human at table).

## Next Steps
- [ ] Install `udev/99-tiltytable-rollerball.rules` so roller ball needs no sudo.
- [ ] Wire ball tracking / table vision to Arducam frames.
- [ ] Revisit KINEMATICS.md / tilt_kinematics.py for BASE=119 neutral-pose implications.

## Known Issues
- Mac cannot `git push` (DoorDash DLP hook); push from Jetson only.
- MindVision SDK prints harmless CoaXPress (`fg_cxp.cti`) warnings on USB cameras.
- UIROBOT website wire-color diagram disagrees with Amazon/Fig0-6; these motors match Amazon (no purple).
