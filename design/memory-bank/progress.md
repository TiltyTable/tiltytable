# Progress

## Completed
- [x] Migrated repo to public `TiltyTable/tiltytable` with project-neutral authorship.
- [x] Consolidated calibration mega-suite + configs into `calibration/`.
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
- [ ] Module-grid calibration session with `calibration/tilt_table_cli.py` (human visual confirm).
- [ ] Wire ball tracking / table vision to Arducam frames.
- [ ] Revisit KINEMATICS.md / tilt_kinematics.py for BASE=119 neutral-pose implications.

## Known Issues
- Mac cannot `git push` (DoorDash DLP hook); push from Jetson only.
- MindVision SDK prints harmless CoaXPress (`fg_cxp.cti`) warnings on USB cameras.
- UIROBOT website wire-color diagram disagrees with Amazon/Fig0-6; these motors match Amazon (no purple).
