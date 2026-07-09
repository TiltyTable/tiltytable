# Technical Context

## Environment
- Primary runtime: NVIDIA Jetson (`zipline@10.0.0.15`), Ubuntu 22.04 / JetPack 6.x, `~/tiltytable`
- Dev edit machine: macOS Cursor workspace (fetch OK; push blocked by DoorDash DLP)
- Design site: browser ES modules under `design/` (Three.js via `esm.sh`, no bundler)
- Python: Jetson `.venv` + root `requirements.txt` / `hardware/requirements.txt`

## Key Paths
- Repo root: `~/tiltytable` (Jetson) / this workspace (Mac)
- Calibration suite: `calibration/`
- Stewart firmware: `arduino/uim5756pm_stewart/`
- Module servos+LEDs firmware: `arduino/servo_calib/`
- Camera (MindVision): `camera/`
- Design / memory bank: `design/`, `design/memory-bank/`
- Cursor rules: `.cursor/rules/`

## Serial Aliases
- `/dev/arduino-stewart` — Uno R3 (tilt)
- `/dev/arduino-modules` — Uno R4 Minima (servos + LEDs)

## Local Serve (design site)
```sh
cd design
python3 -m http.server 8765
```

## Constraints
- Push git from the Jetson only (`origin` → `git@github.com:TiltyTable/tiltytable.git`).
- Do not rsync Mac `.git/` onto the Jetson.
- MindVision camera needs ARM64 `libMVSDK.so`, not OpenCV V4L2.
- No motor/servo motion without explicit human approval.
