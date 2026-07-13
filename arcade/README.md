# TiltyTable Arcade

Jetson-hosted cabinet UI for the 854×480 projector.

## V1 flow

- Scored gauntlet: initials → Levels 1–3 → leaderboard
- Practice: title screen → level select; no score saved
- Module-grid maps, start-tile placement, timers, scoring, retries
- Keyboard-confirmed finish (`C`) until Azure Kinect tracking is integrated
- Synthesized arcade music/effects through the projector audio output

Kinect tracking is V2. Stewart and roller-ball tilt are V3.

## Run

Simulation (no hardware):

```bash
./run_arcade.sh --simulation
```

Live module grid:

```bash
./run_arcade.sh
```

Server only:

```bash
./run_arcade.sh --simulation --no-kiosk
```

Open `http://127.0.0.1:8080`. The launcher uses Chromium/Firefox kiosk mode
when available. On the Jetson, Chromium is launched with `SNAP_REEXEC=0` to
avoid the stock Tegra kernel's snap-confine capability incompatibility.
Scores are stored locally in ignored `var/arcade/scores.json`.

Before starting, the launcher verifies the HTTP port, all three level files,
and—in live mode—the module serial alias plus complete 144-cell LED/servo
calibration.

## Keyboard

| Key | Action |
| --- | --- |
| Arrows | Choose menu item / level |
| A–Z | Enter three initials |
| Enter / Space | Confirm / continue |
| `C` | Host marks the active level complete |
| `R` | Restart active level (−100 points) |
| Escape | End the run (completed levels are saved) |
| `M` | Mute/unmute arcade audio |

## Levels

Configuration lives in `arcade/levels.json`; physical tile states remain
ordinary `maps/*.json` files compatible with `game_runner.py`.

- Level 1: neutral surface with illuminated route
- Level 2: static raised walls
- Level 3: recessed-pit course

## Safety

The live server exclusively owns `/dev/arduino-modules`. Do not run
`game_runner.py`, `tilt_table_cli.py`, or calibration tools at the same time.
Module moves retain the firmware/host pulse-then-release behavior.
