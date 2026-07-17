# Tile maps

JSON maze layouts for [`game_runner.py`](../game_runner.py).

## Schema

Top-level object: keys `A1`…`L12` (columns A–L left→right, rows 1–12 top→bottom).
**A1 = global (0,0)** top-left after the 2026-07-11 grid remap.

Each cell:

```json
{
  "value": 1,
  "color": "#00E050"
}
```

| `value` | Meaning | Servo |
| --- | --- | --- |
| `1` | wall / blocker | extended (80% safety margin) |
| `0` | floor / open | neutral |
| `-1` | pit / lava | recessed (80% safety margin) |

### Physical semantics (LED color ≠ barrier)

**Only `value` controls whether the ball can roll on a tile.** LED color is
cosmetic unless it matches the servo pose below.

| Visual | `value` | Rollable? |
| --- | ---: | --- |
| Gray floor (`#567DBB`) | `0` | **Yes** — open play space only (Chamber 1 tutorial) |
| Shrek path (`#F49400`) | `0` | **Yes** — intended route |
| Red pit / lava (`#FF0000`) | `-1` | **No** — recessed hazard |
| Green wall (`#4DFF00`) | `1` | **No** — raised blocker |
| OFF / black LED on neutral | `0` | **Yes** — LED off does **not** block movement |

Do **not** paint maze margins gray or off while leaving `value: 0` — players
can shortcut through “inactive” tiles. Non-path cells in maze levels must be
`value: 1` (wall) or `value: -1` (pit/lava).

### Two “lava” concepts

| Style | Where defined | Behaviour |
| --- | --- | --- |
| **Static path lava** (deprecated for Chamber 7) | Map JSON: non-path cells `value: -1` | Fixed recessed pits from level load |
| **Survival dwell lava** | `levels.json` `mode: "survival_lava"` | All tiles start `value: 0`; engine sinks tiles after ball dwell + warn blink |

Survival params (`levels.json`, not map): `survivalSeconds`, `dwellSeconds`,
`warnSeconds`, `pointsPerTile`. Win = survive countdown; lose = ball on sunk tile.

`color` is mapped through the game LED palette + per-tile direct RGB overrides
([`calibration/led_palette.json`](../calibration/led_palette.json),
[`calibration/led_color_cal.json`](../calibration/led_color_cal.json)) so
the canonical colors become named `wall` / `trap` / `yellow` / `off` and
look consistent across diffuser tiles.

Optional fields:

- `blinkUntilPlay: true` — arcade recesses the tile until placement is confirmed (LED blinks).
- `dynamic` — timed behaviour (arcade + `game_runner.py`):
  - **cycle** (default): `intervalSeconds` + `pattern[]` of `{value, color}` steps.
  - **delayed_trap**: `type`, `armDelaySeconds`, `warnDurationSeconds`,
    `initialIntervalSeconds`, `minIntervalSeconds`, `trapColor`, `floorColor` —
    path tile blinks trap color with accelerating cadence, then recesses.

Arcade palette hex (from `led_color_cal.json`):

| Role | Hex |
| --- | --- |
| Open floor (Chamber 1 only) | `#567DBB` |
| Path | `#F49400` |
| Wall | `#4DFF00` |
| Pit | `#FF0000` |
| Bonus | `#001FFF` |
| Start | `#00FFFF` |
| End | `#680056` |

## LED color calibration

```bash
.venv/bin/python3 calibration/led_color_cal_tool.py --port /dev/arduino-modules
```

If the **top** looks brighter than the **bottom**:

```text
yellow
darker top
darker top
save
```

Other colors: `black`, `red`, `green`, `cyan`, `magenta`, `yellow`, `gray`, `blue`.
Regions: `top` / `middle` / `bottom` / `left` / `center` / `right` / `all`, or `darker module 0 0` (0,0 = top-left).

Eight palette ids: `trap`, `wall`, `start`, `end`, `yellow`, `floor`, `points`, `off`.

## Examples

- `blank-neutral.json` — all floor (`value` 0) + black/off LEDs; use to reset the table
- `arcade-lava-survival.json` — touched floor warns, then sinks
- `arcade-hex-a-fall.json` — open floor for random warned collapses
- `arcade-snake.json` — open floor for flashing food plus spawned walls/pits
- `tile-map-2026-07-12T04-05-46-868Z.json` — static wall/path maze (source layout for level 2)
- `dynamic-tile-map-2026-07-12T04-08-34-750Z.json` — legacy pit layout reference

## Run

```bash
# Reset: every servo → neutral, every LED → off
.venv/bin/python3 game_runner.py maps/blank-neutral.json --once

.venv/bin/python3 game_runner.py maps/tile-map-2026-07-12T04-05-46-868Z.json --once
.venv/bin/python3 game_runner.py maps/….json --leds-only
.venv/bin/python3 game_runner.py maps/….json --dry-run --once
```

Requires `arduino/servo_calib` on `/dev/arduino-modules` (per-channel 3s hold timeout).
