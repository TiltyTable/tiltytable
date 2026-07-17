# Active Context

## Current Focus
Build a **fun physical interactive arcade game for Open Sauce** on the live
Jetson-hosted TiltyTable stack. The isolated
`release/open-sauce-integrated` worktree now has one-command arcade startup,
in-process headless Kinect tracking, a 90 Hz Stewart/roller-ball loop, and a
mouse-first 854×480 cabinet flow. Live projector/Kinect/Stewart validation
remains before release handoff.

## Recent Changes
- 2026-07-16 | Integrated arcade release branch: game owns headless Kinect observations directly (no HTTP hop), auto-completes tracked end cells, runs game/vision/Stewart feedback independently, scopes 90 Hz roller tilt to placement/play through the persistent supervisor, and provides full left/right-click cabinet navigation with on-screen initials.
- 2026-07-16 | Added browser-only arcade Mode Forge at `/editor`: versioned LevelPackage import/export/validation, 12×12 paint/inspect workflow, deterministic ball/time simulation, and Hex Fall + Target Hunt/Snake mode runtimes. Browser never writes Jetson files; explicit CLI validates/installs packages.
- 2026-07-14 | Maximum-agility experimental pipeline flashed and supervisor restarted: roller targets pipelined at 60 Hz / 1.5° with 2 ms SYN vector window; tuning/probe use adaptive larger waypoints and endpoint-only waits; IK prioritizes crank/heave continuity before torque; firmware caches motion profiles and interleaves stepping with bounded serial reads. Experimental coordinate frame rotates leg azimuths to `(axis0=120°, axis1=240°, axis2=0°)` so axis 2 is cardinal. 27 tests pass; runtime profile query verified.
- 2026-07-14 | Experimental MCS=4 firmware now supports runtime `PROFILE speed accel` (1–90°/s, 1–500°/s²), flashed and verified through persistent supervisor. Added `stewart_exp_tune.py` for manual four-direction ball-roll threshold trials, runtime profile changes, and supervised agility reversals.
- 2026-07-14 | Added and live-validated persistent `stewart_supervisor.py`: one `arduino-cli monitor` backend with `dtr=off,rts=off`, Unix socket `/run/user/1000/tiltytable-stewart.sock`, readonly/motion leases, and ABORT/HOLD on motion-client loss. Experimental probe/roller default to supervisor IPC; repeated firmware checks now use the socket without reopening Arduino serial. Direct serial requires explicit `--direct-serial`; user service has `Restart=no`.
- 2026-07-14 | Experimental stack moved to MCS=4 (all three motors user-verified) and experimental runtime flashed: 16000 steps/crank rev, 40°/s, 120°/s². Free-heave IK scores 50 lb static crank torque and penalizes vertical dead centers; experimental roller input aggregates REL_X/REL_Y by SYN_REPORT with an 8 ms vector window. Post-flash identity verified; calibration required. Production MCS=8 firmware must not be used with current motor settings.
- 2026-07-14 | Added isolated full-rotation/free-heave experiment stack (not flashed): `uim5756pm_stewart_exp` executor, `stewart_exp_probe.py`, and host-side dual-branch/unwrapped IK. A dry-run 10° circle plans 480 continuous waypoints with heave −5.0..29.75 mm, max 7.62° crank change/waypoint, and no production firmware/tool edits.
- 2026-07-13 | Added EEPROM position persistence for clean `roller_ball.py --hold-on-exit` restarts: firmware restores only after an external/DTR reset, while power-on/brown-out/watchdog requires calibration. Any separate motor-supply power cycle still requires explicit `forget`/recalibration. Added `stewart_circle_test.py` for supervised 4.6° full-envelope circles; larger radii remain experimental because firmware IK predicts 6.6° is unreachable at heave 20.
- 2026-07-13 | Configured and verified all three UIM5756PM motors at MCS=8 using the dedicated one-at-a-time A4/A5 configurator, then flashed `uim5756pm_stewart`: 32000 steps/crank rev, 5 µs pulse, and calibration jogs scaled by 4. Post-flash status confirmed calibrated=0 / enabled=0.
- 2026-07-13 | Roller-ball position control tuned for direct response: fixed 20 mm gameplay height, 4.6° circular all-direction envelope (prevents unreachable diagonal corners), EMA 1.0, fixed 0.04°/count gain, hold-last-position idle behavior, and visible firmware pose rejections. As-built kinematics model now uses `BASE=TABLE=119`; modeled envelope at heave 20 is 4.8° guaranteed / 5.5° best.
- 2026-07-13 | Stewart calibration is now per-axis curses TUI (`cal_begin` → jog one enabled axis → `cal_axis` → `cal_finish`). `roller_ball.py --hold-on-exit` stops at the current pose and intentionally leaves all motors energized; serial reopen/reset, power loss, or USB disconnect can still release the 50 lb table.
- 2026-07-13 | Stewart gearboxes upgraded to **MGL23-G20-D8 (20:1)**; initial MCS=32 required 128000 steps/crank rev. Current runtime targets MCS=8 / 32000 after all three motors are reconfigured. Re-`calibrate` after flash.
- 2026-07-13 | Merged `cursor/arcade-game-ui` into `main` (3 commits: arcade V1 UI, survival/levels 4–7, grid cal + archived firmware). Deleted stale local `github-pages-proposal` (already merged via PR #1). Pushed `main` to `origin`.
- 2026-07-12 | Floor is Lava (`survival_lava`): per-tile touch timers (yellow on touch → 2s dwell → red blink → sink at value -1); warn/sink continue after ball leaves; Kinect cell stabilized 0.4s before arming touch; dropout does not reset timers.
- 2026-07-12 | Arcade gauntlet trimmed to 2 explainer chambers: level 1 tilt tutorial (gray floor, blue `blinkUntilPlay` tile), level 2 wall maze + pits/bonuses/gates/delayed traps (`delayed_trap` dynamic schema). Practice keeps chambers 3–7.
- 2026-07-12 | Added `arcade/` V1: Flask + screen-filling 854×480 pixel-arcade kiosk UI, offline Press Start 2P font, 3-level gauntlet/practice flows, explicit restart/abandon/fault states, timers/scoring, atomic JSON leaderboard export, synthesized browser audio, launch preflight, and async/dry-run module-grid adapter. V1 uses keyboard `C` completion; Kinect is V2 and Stewart/roller-ball is V3.
- 2026-07-12 | LED remount: 9 independent strands (one per module). `servo_calib` NUM_STRIPS=9; fresh LED calibration established physical layout. Pins: 0x43→D3, 0x45→D8, 0x48→D5, 0x42→A1, 0x44→D9, 0x40→A3, 0x47→A2, 0x46→D7, 0x41→D2. Keep A4/A5 free for I2C.
- 2026-07-11 | LED color calibration: 9-color palette + per-tile RGB gains (`led_color_cal_tool.py`); `game_runner` resolves map hex via palette aliases/gains.
- 2026-07-11 | Added `game_runner.py` (tile-map JSON → LEDs/servos; dynamic pattern loop). `servo_calib` now per-channel 3s HOLD auto-limp (flashed to R4); host silence WATCHDOG remains 5s.
- 2026-07-11 | Archived obsolete Arduino sketches to `archive/arduino/` (serial_servo, sg90_sweep, serial_leds, servo_bridge, tilt_table_leds*). Live modules firmware confirmed = `servo_calib.ino` via serial `?` fingerprint on `/dev/arduino-modules`.
- 2026-07-11 | Hard rule: never leave module servos energized (stall burnout). `tilt_table_cli` / `servo_tool` / `run_green_cell_sequence` pulse-then-`O`; hardware-safety rule updated.
- 2026-07-11 | Remapped module-grid origin so `(0,0)` = top-left (was top-right): `new_row,new_col = old_col, 11-old_row` on both grid JSONs. Backups `*.bak-2026-07-11-origin-tl`. Updated led/servo cal block seeds + `module-grid-mapping` rule.
- 2026-07-11 | Confirmed module-grid global maps are trusted: `led_grid_config.json` + `servo_grid_config.json` share `(row,col)`. Removed outdated “servo orientation mismatch” notes; `tilt_table_cli` demo uses the servo grid again. Cursor rule: `.cursor/rules/module-grid-mapping.mdc`.
- 2026-07-08 | Migrated project to public `TiltyTable/tiltytable`; commits authored as `TiltyTable`.
- 2026-07-08 | Consolidated calibration suite, hardware stack, and design docs into the git repo; archived `scratch/tilt-table`.
- 2026-07-08 | Corrected board roles: R4 Minima = module servos+LEDs; R3 = Stewart 3DOF.
- 2026-07-08 | Renamed udev aliases to `/dev/arduino-stewart` and `/dev/arduino-modules`.
- 2026-07-08 | Installed Jetson venv deps (`pyserial`, `flask`, `opencv-headless`, …).
- 2026-07-08 | Stewart calibrate: cranks straight up = max heave; motion gated until `calibrate`.
- 2026-07-09 | Cursor SSH on Jetson is the primary edit/runtime path.
- 2026-07-09 | Active camera is Arducam/UVC (`/dev/video0`), not MindVision.
- 2026-07-09 | UIM5756PM harness uses Amazon/Fig0-6 colors (Brown=COM, Gray=DIR, Yellow=PLS, Blue=ENA, White=UART TX, Green=UART RX). Arduino pins PLS 2/7/11, DIR 3/8/12, ENA 4/9/13. Current MCS=8 runtime uses 5 µs pulses and `STEPS_PER_CRANK_REV=32000`. Direct-test bring-up tools removed; use `stewart_cli.py`.
- 2026-07-09 | All three axes: positive jog = inward (DIR_INVERT all false). Geometry updated: BASE_MOTOR_RADIUS_MM=119, CALIBRATE_HEAVE_MM=30. Quieter motion profile 25°/s / 40°/s². Roller ball HID present as 13ba:0018 "Barcode Reader Mouse" (`/dev/input/event7`); `capture_usb_mouse.py` default heave=20.
- 2026-07-09 | Uno ACM open always resets the board on Jetson; no hardware autoreset disable. Host tools recalibrate after open (`--calibrate-on-start` default for roller ball).
- 2026-07-09 | Added `roller_ball.py`: one-command workflow (confirm max heave → open/reset → calibrate → enable → absolute roll/pitch from trackball at heave 30).

## Open Questions
- When Kinect arrives, keep dual camera paths or standardize on one.
- Wire ball tracking to Arducam frames later.
