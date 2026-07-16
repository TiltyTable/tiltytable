# TiltyTable Arcade

Jetson-hosted cabinet UI for the 854×480 projector.

## V1 flow

- Scored gauntlet: initials → Chambers 1–2 (gauntlet) → leaderboard
- Practice: title screen → all 7 chambers; no score saved
- Module-grid maps, start-tile placement, timers, scoring, retries
- Keyboard-confirmed finish (`C`) until Azure Kinect tracking is integrated
- Synthesized arcade music/effects through the projector audio output

Kinect tracking is V2. Stewart and roller-ball tilt are V3.

## Run

## Parallel dev ports

When Kinect tooling already owns the default HTTP port, run arcade on another:

| Service | Port |
| --- | --- |
| Kinect / other stack | `8080` |
| Arcade | `TILTYTABLE_ARCADE_PORT=8081 ./run_arcade.sh` (default `8080`) |

The kiosk Chromium `--app=` URL uses the same `HTTP_PORT` as the server.

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

Before starting, the launcher verifies the HTTP port, all level maps referenced
in `arcade/levels.json`, and—in live mode—the module serial alias plus complete
144-cell LED/servo calibration.

## Browser level and mode editor

With the arcade server running, open:

```text
http://127.0.0.1:8080/editor
```

The browser-only Mode Forge edits a portable `LevelPackage` containing level
metadata, lore copy, mode parameters, deterministic RNG seed, and all 144 tile
cells. It never writes Jetson files directly.

Supported modes:

- `reach_end`
- `survival_lava`
- `hex_fall` (Hex-A-Gone-style touched/collapsing floor)
- `target_hunt` (Snake-style timed targets that add permanent pits/walls)

The editor has two views:

1. **Build** — choose Lava, Hex-A-Fall, or Snake; paint floor/walls/pits; set
   the start tile; tune the few mode-specific rules.
2. **Play test** — press Start and use the arrow keys to move the simulated
   ball through the actual timer, target, wall, pit, warning, and collapse
   rules.

Advanced tile dynamics and level/lore metadata remain available in collapsed
sections, while the default workflow stays focused on game design.

Validate or install a downloaded package explicitly:

```bash
python3 -m arcade.level_packages validate ~/Downloads/my-level.level.json
python3 -m arcade.level_packages install ~/Downloads/my-level.level.json
```

Export an existing runtime level into the editor format:

```bash
python3 -m arcade.level_packages export level-7 /tmp/level-7.level.json
```

The schema is `arcade/level-package.schema.json`. Installation compiles the
package back into an `arcade/levels.json` entry plus `maps/<level-id>.json`.

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

Configuration lives in `arcade/levels.json`; each level points at a
`maps/arcade-level-N.json` tile map compatible with `game_runner.py`.

**Gauntlet (2 chambers, fast booth throughput)**

| Chamber | Map | Teaches |
| --- | --- | --- |
| 1 — First Tilt | `maps/arcade-level-1.json` | Table tilt only: gray floor, cyan→magenta, one blue tile recessed until play |
| 2 — Dungeon Digest | `maps/arcade-level-2.json` | Wall maze (from `tile-map-2026-07-12…`), pits, blue bonuses, cycling green gates, delayed red traps; non-path cells are physical walls |

**Practice-only (chambers 3–7)**

- Pit drill, dynamic gates, blue bonuses, finale combo, Floor is Lava.

Gauntlet runs `gauntletLevelIds` in `levels.json` (currently `level-1`, `level-2`).
Practice unlocks all seven chambers.

### Tile physics (level design)

Only servo `value` affects traversability — LED color does not.

| Visual | `value` | Rollable? |
| --- | ---: | --- |
| Gray floor | `0` | Yes — open play space (Chamber 1 only) |
| Shrek path | `0` | Yes — intended route |
| Red lava / pit | `-1` | No |
| Green wall | `1` | No |
| OFF LED on neutral | `0` | Yes — unlit ≠ barrier |

Gray or off tiles with `value: 0` outside Chamber 1 create false shortcuts.
Maze margins must be walls (`1`) or pits (`-1`).

### Map extras

- `blinkUntilPlay: true` — recessed until the player confirms placement; LED blinks until play.
- `dynamic.type: "delayed_trap"` — path tile blinks red with accelerating cadence, then recesses.
- `dynamic.pattern` + `intervalSeconds` — cycling walls/gates (see chamber 4).

## Safety

The live server exclusively owns `/dev/arduino-modules`. Do not run
`game_runner.py`, `tilt_table_cli.py`, or calibration tools at the same time.
Module moves retain the firmware/host pulse-then-release behavior.
