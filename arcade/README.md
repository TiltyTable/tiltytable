# TiltyTable Arcade

Jetson-hosted 854×480 Open Sauce cabinet game.

## Games

The cabinet opens directly to four selectable game modes:

- **Lava Survival** — survive 40 seconds while touched tiles warn and sink. The
  score is 100 points per whole second survived plus 100 per unique tile.
- **Snake** — collect flashing food for 100 points; every pickup raises one wall
  and sinks one floor tile. The run ends when the ball falls into a pit.
- **Food Frenzy** — collect flashing blue food against a yellow floor before a
  30-second round timer expires. Each completed level awards 500 points.
- **Maze** — an unlimited-time maze with raised walls, lowered pits, and cycling
  gates. Falling into a pit ends the run; the fastest finish ranks first.

Snake has no timer. Dynamic modes have no magenta finish tile. Hex-A-Fall remains
in the internal catalog for compatibility, but is hidden and cannot be selected.

Hex collapse pacing is configured directly in `arcade/levels.json` with
`modeParams.collapseStages`. Each stage defines `afterSeconds`, `everySeconds`,
and `count`; the shipped 45-second round progresses from one tile every 2.0
seconds, to two every 1.6 seconds, to three every 1.2 seconds.

All modes share `arcade.pit_detection.PitDetector`: neutral-floor observations
apply immediately, while a pit requires two seconds of sustained,
high-confidence tracking before ending the game.

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

During every game, green/right triggers a host-side jump-assist pulse when
Kinect confidently locates the ball on any non-red, non-recessed cell. The cell
lifts 15% toward extended, returns to neutral, and immediately releases; the
2.5-second cooldown prevents repeated servo pulses.

## Per-game leaderboards

Every selectable game has its own top-ten board. After every clear, pit, or
timeout, press green/right to enter three letters or pink/left to skip. Roll the
trackball up/down to choose each letter and press green/right to advance and
save. Point scores are ranked highest first; Maze times are ranked fastest
first. The game selector previews the selected game's leaderboard.

## Maps and tiles

The active catalog is `arcade/levels.json`. Maps:

- `maps/arcade-lava-survival.json`
- `maps/arcade-hex-a-fall.json`
- `maps/arcade-snake.json`
- `maps/arcade-food-frenzy.json`
- `maps/arcade-level-4.json`

Servo `value` controls physics:

- `0`: rollable floor
- `1`: raised wall
- `-1`: recessed pit

## Maze editor

Open `http://127.0.0.1:8080/editor` while the arcade server is running. The
editor can set any clicked cell raised, neutral, or lowered; edit LED colors; and
configure repeating cycles or delayed traps. It supports undo/redo, flood
fill, dynamic previews, JSON import/export, and validated atomic saves to
`maps/arcade-level-4.json`.

Saving does not move live hardware or alter a Maze game already in progress.
The edited map is applied the next time Maze is loaded.

## Hardware behavior

The live server exclusively owns `/dev/arduino-modules`. Module moves retain
the required pulse-then-release behavior. Color-only animation updates never
energize servos.
