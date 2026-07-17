# Progress

## Completed
- [x] Shared pit confirmation for every mode, immediate neutral-floor tracking, configurable escalating Hex collapse stages, and larger booth legend/footer/instruction text.
- [x] Human-review cabinet simplification: no character panels/restart counters, larger controls/status text, LED color legends, common A1 start, and two-frame Snake food capture.
- [x] Latency-first Open Sauce arcade: three-game selector, timed Lava/Hex touch scoring, timerless Snake escalation, no shipped reach-finish maps, and main's frame-driven/lock-free tracking path preserved.
- [x] Archived Mode Forge and LevelPackage tooling; active games are edited directly with AI-assisted iteration.
- [x] Integrated arcade release branch: one-command startup, in-process headless Kinect tracking, tracked completion for every game mode, independent 20/30/60/90 Hz loops, Stewart placement/play lifecycle, and mouse-first cabinet navigation.
- [x] Browser arcade Mode Forge: portable LevelPackage schema/compiler, Hex Fall + Target Hunt runtimes, 12×12 editor, deterministic simulation, JSON import/export, editor route, and tests.
- [x] Maximum-agility pipeline flashed: 60 Hz pipelined roller targets, adaptive endpoint-only tuning moves, crank/heave-first IK, cached AccelStepper profiles, bounded serial parsing, and axis-2-aligned cardinal frame.
- [x] Supervisor-based Stewart live tuning CLI: manual directional ball-roll thresholds, runtime speed/acceleration profiles, and supervised agility reversals without persisted tuning state.
- [x] Persistent no-DTR Stewart supervisor with Unix-socket IPC, motion-client ABORT/HOLD cleanup, readonly leases, and non-restarting user service; experimental tools no longer open Arduino serial by default.
- [x] Isolated experimental full-rotation/free-heave stack: dual-branch host IK, dedicated armed absolute-step firmware (flashed at MCS=4), supervised probe/logging, 10° continuous dry-run coverage, and regression tests.
- [x] Clean app-restart position persistence (EEPROM + reset-cause gate) and supervised Stewart circular range test command.
- [x] One-at-a-time UIM5756PM MCS=8 configurator (A4 RX/white TX, A5 TX/green RX); Stewart runtime prepared for 32000 steps/crank rev and 5 µs pulses.
- [x] Responsive Stewart position control: safe 4.6° radial envelope at heave 20, direct EMA 1.0 response at fixed 0.04°/count, firmware error visibility, corrected as-built kinematics, and regression tests.
- [x] Merged arcade branch into `main` and pushed to `origin` (2026-07-13).
- [x] Arcade cabinet copy pass: player-facing actions only, concise level/placement/play/result screens, 854×480 tuning, and persistent UI rule.
- [x] Arcade V1 web app: 854×480 gauntlet/practice UI, 3 levels, score/timer/retries, JSON leaderboard, synthesized audio, and module-grid adapter.
- [x] LED remount to 9 independent strands + firmware flash + `led_grid_config` remap (2026-07-12).
- [x] Migrated repo to public `TiltyTable/tiltytable` with project-neutral authorship.
- [x] Consolidated calibration mega-suite + configs into `calibration/`.
- [x] Documented trusted 12×12 LED/servo global maps (`module-grid-mapping` rule); cleared stale orientation-mismatch guidance in `tilt_table_cli`.
- [x] Remapped grid origin to `(0,0)` = top-left (row↓ col→).
- [x] Module servos: pulse-then-release only; never leave PWM latched (hardware-safety rule).
- [x] Archived obsolete Arduino sketches under `archive/arduino/`; live modules board = `servo_calib`.
- [x] `game_runner.py` + maps schema; firmware per-channel 3s HOLD timeout on R4.
- [x] LED palette (9 game colors) + per-tile gain cal tool; game_runner uses them.
- [x] Archived old `scratch/tilt-table` sandbox.
- [x] Documented correct board roles (R3 Stewart / R4 modules).
- [x] Stewart pinout PLS 2/7/11, DIR 3/8/12, ENA 4/9/13; Amazon wire colors (Brown=COM, Gray=DIR, Yellow=PLS, Blue=ENA).
- [x] Installed udev aliases `/dev/arduino-stewart` and `/dev/arduino-modules` on Jetson.
- [x] Installed Jetson Python deps into `.venv`.
- [x] Non-motion smoke test: R4 servo+LED firmware responds (`?`, `LX`, `X`).
- [x] Scaffolded `camera/mindvision_capture.py` + README for HT-SUA134GM; Arducam UVC path via `camera/uvc_focus_stream.py`.
- [x] Stewart `calibrate` gate + MGL23-G20-D8 (20:1); runtime MCS=8 → 32000 steps/crank rev.
- [x] Interactive `stewart_cli.py` for Stewart serial sessions.

## In Progress
- [ ] Live projector + Kinect + Stewart smoke test of the integrated arcade runner (human at table).

## Next Steps
- [ ] Install `udev/99-tiltytable-rollerball.rules` so roller ball needs no sudo.
- [ ] Wire ball tracking / table vision to Arducam frames.
- [ ] Revisit KINEMATICS.md / tilt_kinematics.py for BASE=119 neutral-pose implications.

## Known Issues
- Mac cannot `git push` (DoorDash DLP hook); push from Jetson only.
- MindVision SDK prints harmless CoaXPress (`fg_cxp.cti`) warnings on USB cameras.
- UIROBOT website wire-color diagram disagrees with Amazon/Fig0-6; these motors match Amazon (no purple).
