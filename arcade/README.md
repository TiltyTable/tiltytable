# TiltyTable Arcade

Jetson-hosted 854×480 Open Sauce cabinet game.

## Games

The cabinet opens directly to three game modes:

- **Lava Survival** — touched tiles score, warn, and sink. Survive 40 seconds.
- **Hex-A-Fall** — unique touched tiles score while random tiles warn and sink.
  Survive 45 seconds.
- **Snake** — collect flashing food for one point; every pickup raises one wall
  and sinks one floor tile. The run ends when the ball falls into a pit.

Lava and Hex keep their mode-specific score when the timer expires. Snake has
no timer. Dynamic modes have no magenta finish tile.

Hex collapse pacing is configured directly in `arcade/levels.json` with
`modeParams.collapseStages`. Each stage defines `afterSeconds`, `everySeconds`,
and `count`; the shipped 45-second round progresses from one tile every 2.0
seconds, to two every 1.6 seconds, to three every 1.2 seconds.

All modes share `arcade.pit_detection.PitDetector`: neutral-floor observations
apply immediately, while pits require sustained high-confidence tracking.

The generic `reach_end` engine capability remains available for a future
procedural level generator, but no reach-the-finish maps ship in this build.

## Run

```bash
./run_arcade.sh
```

Live server without opening the kiosk browser:

```bash
./run_arcade.sh --no-kiosk
```

Open `http://127.0.0.1:8080`.

The live process owns Kinect tracking, roller-ball/Stewart control, the module
grid, game engine, and UI. Do not run standalone Kinect, Stewart, or module-grid
tools at the same time.

## Latency architecture

- Kinect ball frames wake the game ticker immediately; `tracking.game_tick_ms`
  is a 10 ms fallback.
- Ball detection publishes before asynchronous table-pose fitting.
- The cabinet polls full game state every 50 ms and lock-free `/api/ball`
  telemetry every 16 ms.
- LED-only touch, warning, and food updates bypass servo board selection and
  settle delays.
- Warning blinks are deduplicated so unchanged frames emit no hardware write.
- The overlay reports latest, average, and p95 capture-to-game latency.

## Controls

- Roll up/down: choose a game.
- Green/right button or Enter: confirm.
- Pink/left button or Escape: back/end game.
- Roller ball during placement/play: tilt the Stewart platform.
- Arrow keys: move the simulated ball.
- `R`: restart.
- `M`: mute.

## Maps and tiles

The active catalog is `arcade/levels.json`. Maps:

- `maps/arcade-lava-survival.json`
- `maps/arcade-hex-a-fall.json`
- `maps/arcade-snake.json`

Servo `value` controls physics:

- `0`: rollable floor
- `1`: raised wall
- `-1`: recessed pit

The old browser editor and `LevelPackage` pipeline live under
`archive/arcade/`; they are not served or tested.

## Hardware behavior

The live server exclusively owns `/dev/arduino-modules`. Module moves retain
the required pulse-then-release behavior. Color-only animation updates never
energize servos.
