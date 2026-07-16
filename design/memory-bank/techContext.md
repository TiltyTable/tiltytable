# Technical Context

## Environment
- Primary runtime: NVIDIA Jetson (`zipline@10.0.0.15`), Ubuntu 22.04 / JetPack 6.x, `~/tiltytable`
- Dev edit machine: macOS Cursor workspace (fetch OK; push blocked by DoorDash DLP)
- Design site: browser ES modules under `design/` (Three.js via `esm.sh`, no bundler)
- Python: Jetson `.venv` + root `requirements.txt` / `hardware/requirements.txt`

## Key Paths
- Repo root: `~/tiltytable` (Jetson) / this workspace (Mac)
- Calibration suite: `calibration/`
- Tile maps + game runner: `maps/`, `game_runner.py`
- Stewart firmware: `arduino/uim5756pm_stewart/`
- Module servos+LEDs firmware: `arduino/servo_calib/`
- Archived obsolete sketches: `archive/arduino/`
- Camera (MindVision): `camera/`
- Design / memory bank: `design/`, `design/memory-bank/`
- Cursor rules: `.cursor/rules/`

## Module-grid global orientation (trusted)
- Shared 12×12 `(row, col)` space for LEDs and servos.
- Origin: `(0,0)` = top-left; row↓, col→.
- `calibration/led_grid_config.json` — cell → strip+pixel.
- `calibration/servo_grid_config.json` — cell → PCA9685 address+channel.
- `calibration/servo_config_0x4X.json` — per-servo rec/neu/ext µs.
- Join on `"r,c"` keys; see `.cursor/rules/module-grid-mapping.mdc`.

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
