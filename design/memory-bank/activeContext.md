# Active Context

## Current Focus
Bring up the live Jetson-hosted tiltytable stack: Stewart tilt (Uno R3),
module-grid servos+LEDs (Uno R4 Minima), and MindVision camera capture.

## Recent Changes
- 2026-07-08 | Migrated project to public `TiltyTable/tiltytable`; commits authored as `TiltyTable`.
- 2026-07-08 | Consolidated calibration suite, hardware stack, and design docs into the git repo; archived `scratch/tilt-table`.
- 2026-07-08 | Corrected board roles: R4 Minima = module servos+LEDs; R3 = Stewart 3DOF.
- 2026-07-08 | Updated Stewart pinout to PLS 2/5/10, DIR 3/6/11, ENA 4/7/12; flashed R3; both boards pass non-motion serial smoke tests.
- 2026-07-08 | Renamed udev aliases to `/dev/arduino-stewart` and `/dev/arduino-modules`.
- 2026-07-08 | Installed Jetson venv deps (`pyserial`, `flask`, `opencv-headless`, …).
- 2026-07-08 | Installed MindVision linuxSDK ARM64 `libMVSDK.so`; first frame grab works (1280×1024 mono via `camera/mindvision_capture.py`).

## Open Questions
- When Kinect arrives, keep dual camera paths or standardize on one.
- Agree safe-motion test plan before enabling Stewart motors or jogging module servos.
- Wire ball tracking to MindVision frames.
