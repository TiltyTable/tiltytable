# Active Context

## Current Focus
Bring up the live Jetson-hosted tiltytable stack: Stewart tilt (Uno R3),
module-grid servos+LEDs (Uno R4 Minima), and Arducam UVC capture.

## Recent Changes
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
