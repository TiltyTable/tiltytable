# Progress

## Completed
- [x] Migrated repo to public `TiltyTable/tiltytable` with project-neutral authorship.
- [x] Consolidated calibration mega-suite + configs into `calibration/`.
- [x] Archived old `scratch/tilt-table` sandbox.
- [x] Documented correct board roles (R3 Stewart / R4 modules).
- [x] Updated Stewart firmware pinout to PLS 2/5/10, DIR 3/6/11, ENA 4/7/12.
- [x] Installed udev aliases `/dev/arduino-stewart` and `/dev/arduino-modules` on Jetson.
- [x] Installed Jetson Python deps into `.venv`.
- [x] Non-motion smoke test: R4 servo+LED firmware responds (`?`, `LX`, `X`).
- [x] Flashed and smoke-tested R3 Stewart firmware (`help`, `status`; motors not enabled).
- [x] Scaffolded `camera/mindvision_capture.py` + README for HT-SUA134GM.

- [x] Stewart `calibrate`: cranks straight up = max heave; gate motion until calibrated; flashed + smoke-tested.

## In Progress
- [ ] First safe post-calibrate motion test (human at table).

## Next Steps
- [ ] Module-grid calibration session with `calibration/tilt_table_cli.py` (human visual confirm).
- [ ] Wire ball tracking / table vision to camera frames.
- [ ] Prevent Mac→Jetson rsync from overwriting Jetson `.git/config` remotes.

## Known Issues
- Mac cannot `git push` (DoorDash DLP hook); push from Jetson only.
- MindVision SDK prints harmless CoaXPress (`fg_cxp.cti`) warnings on USB cameras.
