# Tile maps

JSON maze layouts for [`game_runner.py`](../game_runner.py).

## Schema

Top-level object: keys `A1`…`L12` (columns A–L left→right, rows 1–12 top→bottom).
**A1 = global (0,0)** top-left after the 2026-07-11 grid remap.

Each cell:

```json
{
  "value": 1,
  "color": "#76D69E",
  "dynamic": {
    "intervalSeconds": 7,
    "pattern": [
      { "value": 1, "color": "#76D69E" },
      { "value": 0, "color": "#FFFFFF" },
      { "value": -1, "color": "#F29C64" }
    ]
  }
}
```

| `value` | Meaning | Servo |
| --- | --- | --- |
| `1` | wall / blocker | extended (80% safety margin) |
| `0` | floor / open | neutral |
| `-1` | pit | recessed (80% safety margin) |

`color` is mapped through the game LED palette + per-tile RGB gains
([`calibration/led_palette.json`](../calibration/led_palette.json),
[`calibration/led_color_cal.json`](../calibration/led_color_cal.json)) so
exported pastels (e.g. mint wall / orange trap) become named `wall` /
`trap` / `path` / `unmarked` and look consistent across diffuser tiles.

## LED color calibration

```bash
.venv/bin/python3 calibration/led_color_cal_tool.py --port /dev/arduino-modules
```

If the **top** looks brighter than the **bottom**:

```text
white
darker top
darker top
save
```

Other colors: `red`, `green`, `yellow`, `start`, `end`, `points`, `floor`, `blue`.  
Regions: `top` / `middle` / `bottom` / `left` / `center` / `right` / `all`, or `darker module 0 0` (0,0 = top-left).

Nine palette ids: `path`, `trap`, `wall`, `unmarked`, `start`, `end`, `points`, `floor`, `dynamic`.

## Examples

- `blank-neutral.json` — all floor (`value` 0) + unmarked white (LEDs off); use to reset the table
- `arcade-level-1.json` — neutral tutorial surface + illuminated route
- `arcade-level-2.json` — static raised-wall maze
- `arcade-level-3.json` — 25% recessed borders + 20% dynamic borders
- `tile-map-2026-07-12T04-05-46-868Z.json` — static maze
- `dynamic-tile-map-2026-07-12T04-08-34-750Z.json` — same layout + oscillating tiles

## Run

```bash
# Reset: every servo → neutral, every LED → off
.venv/bin/python3 game_runner.py maps/blank-neutral.json --once

.venv/bin/python3 game_runner.py maps/tile-map-2026-07-12T04-05-46-868Z.json --once
.venv/bin/python3 game_runner.py maps/dynamic-tile-map-2026-07-12T04-08-34-750Z.json
.venv/bin/python3 game_runner.py maps/….json --leds-only
.venv/bin/python3 game_runner.py maps/….json --dry-run --once
```

Requires `arduino/servo_calib` on `/dev/arduino-modules` (per-channel 3s hold timeout).
