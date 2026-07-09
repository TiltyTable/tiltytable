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

## In Progress
- [ ] Wire ball tracking / table vision to MindVision frames.

## Next Steps
- [ ] Safe-motion Stewart bring-up (enable one axis, verify direction, then pose).
- [ ] Module-grid calibration session with `calibration/tilt_table_cli.py` (human visual confirm).
- [ ] Prevent Mac→Jetson rsync from overwriting Jetson `.git/config` remotes.

## Known Issues
- Mac cannot `git push` (DoorDash DLP hook); push from Jetson only.
- MindVision SDK prints harmless CoaXPress (`fg_cxp.cti`) warnings on USB cameras.
