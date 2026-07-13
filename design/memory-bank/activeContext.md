# Active Context

## Current Focus
Build a **fun physical interactive arcade game for Open Sauce** on the live
Jetson-hosted TiltyTable stack. Arcade V1 is in place (scored gauntlet,
practice, module-grid levels, 854×480 cabinet UI). Ongoing bring-up: Stewart
tilt (Uno R3), module-grid servos+LEDs (Uno R4 Minima, 9 independent LED
strands), roller ball, and Arducam UVC capture. Kinect ball tracking is V2;
full Stewart/roller-ball in the game loop is V3.

## Recent Changes
- 2026-07-12 | Reworked all arcade screens as a player-facing cabinet UI: minimal action copy, TILTYTABLE title, concise level mechanics and placement/play guidance, no marketing or hardware terminology, and explicit 854×480 tuning. Added persistent arcade copy rule.
- 2026-07-12 | Added `arcade/` V1: Flask + screen-filling 854×480 pixel-arcade kiosk UI, offline Press Start 2P font, 3-level gauntlet/practice flows, explicit restart/abandon/fault states, timers/scoring, atomic JSON leaderboard export, synthesized browser audio, launch preflight, and async/dry-run module-grid adapter. V1 uses keyboard `C` completion; Kinect is V2 and Stewart/roller-ball is V3.
- 2026-07-12 | LED remount: 9 independent strands (one per module). `servo_calib` NUM_STRIPS=9; fresh LED calibration established physical layout. Pins: 0x43→D3, 0x45→D8, 0x48→D5, 0x42→A1, 0x44→D9, 0x40→A3, 0x47→A2, 0x46→D7, 0x41→D2. Keep A4/A5 free for I2C.
- 2026-07-11 | LED color calibration: 9-color palette + per-tile RGB gains (`led_color_cal_tool.py`); `game_runner` resolves map hex via palette aliases/gains.
- 2026-07-11 | Added `game_runner.py` (tile-map JSON → LEDs/servos; dynamic pattern loop). `servo_calib` now per-channel 3s HOLD auto-limp (flashed to R4); host silence WATCHDOG remains 5s.
- 2026-07-11 | Archived obsolete Arduino sketches to `arduino/archive/` (serial_servo, sg90_sweep, serial_leds, servo_bridge, tilt_table_leds*). Live modules firmware confirmed = `servo_calib.ino` via serial `?` fingerprint on `/dev/arduino-modules`.
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
- 2026-07-09 | UIM5756PM harness uses Amazon/Fig0-6 colors (Brown=COM, Gray=DIR, Yellow=PLS, Blue=ENA). Arduino pins PLS 2/7/11, DIR 3/8/12, ENA 4/9/13. AccelStepper min pulse 20 µs; STEPS_PER_CRANK_REV=6400 (MCS=32). Direct-test bring-up tools removed; use `stewart_cli.py`.
- 2026-07-09 | All three axes: positive jog = inward (DIR_INVERT all false). Geometry updated: BASE_MOTOR_RADIUS_MM=119, CALIBRATE_HEAVE_MM=30. STEPS_PER_CRANK_REV=32000. Quieter motion profile 25°/s / 40°/s². Roller ball HID present as 13ba:0018 "Barcode Reader Mouse" (`/dev/input/event7`); `capture_usb_mouse.py` default heave=20.
- 2026-07-09 | Uno ACM open always resets the board on Jetson; no hardware autoreset disable. Host tools recalibrate after open (`--calibrate-on-start` default for roller ball).
- 2026-07-09 | Added `roller_ball.py`: one-command workflow (confirm max heave → open/reset → calibrate → enable → absolute roll/pitch from trackball at heave 30).

## Open Questions
- When Kinect arrives, keep dual camera paths or standardize on one.
- Wire ball tracking to Arducam frames later.
