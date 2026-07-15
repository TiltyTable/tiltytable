# Experimental full-rotation Stewart executor

This firmware is isolated from the production Stewart stack. It accepts
host-computed absolute motor steps while `stewart_exp_probe.py` performs
dual-branch IK, free-heave optimization, and path continuity.

It is not protocol-compatible with `roller_ball.py` or the production CLI.
The unique identity response is:

```text
OK EXP UIM5756PM_STEWART_EXP 1
```

## Safety model

- Boots disabled and disarmed.
- Motion requires `ARM CONFIRM`.
- Every `TARGET` is limited to 12° crank travel from the previous target.
- Experimental performance profile is intentionally aggressive: 40°/s crank
  speed and 120°/s² crank acceleration (MCS=4, 16000 steps/crank revolution).
- `ABORT` and default host cleanup hold the current position.
- `DISABLE` is always explicit.
- Experimental EEPROM uses offset 128 and magic `TTXE`; it never consumes the
  production pose snapshot.
- Power-on/brown-out/watchdog reset always requires calibration.

Opening `/dev/arduino-stewart` can still DTR-reset the Uno and briefly release
the loaded table. Mechanically support/catch the table before opening serial,
flashing, power cycling, or running experiments.

## Non-motion validation

```bash
.venv/bin/python3 -m unittest tests.test_stewart_exp_kinematics -v

.venv/bin/python3 stewart_exp_probe.py \
  --circle 10 --dry-run \
  --log /tmp/stewart-exp-circle-10.json

.venv/bin/python3 stewart_exp_probe.py \
  --envelope-map /tmp/stewart-exp-envelope.json

arduino-cli compile --fqbn arduino:avr:uno \
  arduino/uim5756pm_stewart_exp
```

## Flash and identify

Only with the table mechanically protected:

```bash
arduino-cli upload -p /dev/arduino-stewart \
  --fqbn arduino:avr:uno arduino/uim5756pm_stewart_exp

.venv/bin/python3 stewart_exp_probe.py --check-firmware
```

The check opens serial but never arms or commands motion.

## Supervised progression

The probe owns one serial connection for calibration and the complete
trajectory. Live motion requires typing `MOVE`.

```bash
# Small cardinal sequence
.venv/bin/python3 stewart_exp_probe.py --cardinals 6

# Increase only after inspecting all joints/spacers
.venv/bin/python3 stewart_exp_probe.py --cardinals 8
.venv/bin/python3 stewart_exp_probe.py --cardinals 10

# Then a slow 10° circle with free heave
.venv/bin/python3 stewart_exp_probe.py \
  --circle 10 --period 45 \
  --log /tmp/stewart-exp-live-10.json
```

The default cleanup holds the final pose. Add `--disable-on-exit` only when the
table is physically supported.

## Experimental roller ball

After cardinal tests have validated the desired radius, use the dedicated
free-heave roller controller (production `roller_ball.py` remains unchanged):

```bash
.venv/bin/python3 stewart_exp_roller_ball.py --max-tilt 10
```

Type `START`, calibrate if requested, then roll the ball. Input commands
absolute roll/pitch position while the host continuously chooses heave and
crank branches. Defaults are 30 Hz, 0.5° maximum platform target change per
update, and 0.5 mm maximum heave change per update. Ctrl-C sends `ABORT` and
holds; it does not disable.

## Return to production

1. Mechanically support the table.
2. Flash `arduino/uim5756pm_stewart`.
3. Recalibrate before production motion.

Do not hand experimental multi-turn step coordinates to the production solver.
