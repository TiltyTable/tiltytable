# Experimental full-rotation Stewart executor

This is the live Stewart firmware. It accepts
host-computed absolute motor steps while `stewart_exp_probe.py` performs
dual-branch IK, free-heave optimization, and path continuity.

It is not protocol-compatible with the retired direct-serial tools in
`archive/stewart_legacy/`.
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
- Speed/acceleration are runtime configurable after this firmware is installed:

```bash
.venv/bin/python3 stewart_exp_profile.py --speed 60 --accel 200
.venv/bin/python3 stewart_exp_profile.py  # query active profile
```

Firmware validates speed 1–90°/s and acceleration 1–500°/s². Probe and roller
tools also accept `--crank-speed` and `--crank-accel`.
- `ABORT` and default host cleanup hold the current position.
- `DISABLE` is always explicit.
- Experimental EEPROM uses offset 128 and magic `TTXE`; it never consumes the
  production pose snapshot.
- Power-on/brown-out/watchdog reset always requires calibration.

Opening `/dev/arduino-stewart` can still DTR-reset the Uno and briefly release
the loaded table. Normal tools therefore connect to the persistent
`stewart_supervisor.py` Unix socket and never open Arduino serial themselves.
Supported host tools do not provide a direct-serial fallback.

## Persistent serial supervisor

Start once, with the table mechanically protected for initial validation:

```bash
.venv/bin/python3 stewart_supervisor.py
```

It launches one persistent backend equivalent to:

```bash
arduino-cli monitor -p /dev/arduino-stewart --raw --quiet \
  -c baudrate=115200,dtr=off,rts=off
```

The supervisor owns `/dev/arduino-stewart` and serves clients through:

```text
/run/user/1000/tiltytable-stewart.sock
```

Motion-client disconnect sends `ABORT` (hold); readonly clients cannot issue
motion commands. The supervisor never restarts itself automatically.

Optional user-service installation:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/tiltytable-stewart-supervisor.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tiltytable-stewart-supervisor.service
```

Do not configure `Restart=always`: a daemon restart could reopen serial while
the table is load-bearing. Stop/start manually with the table supported.

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

The check uses a readonly supervisor lease and never opens serial itself.

## Supervised progression

The probe owns one supervisor motion lease for calibration and the complete
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
free-heave roller controller:

```bash
.venv/bin/python3 stewart_exp_roller_ball.py --max-tilt 10
```

Type `START`, calibrate if requested, then roll the ball. Input commands
absolute roll/pitch position while the host continuously chooses heave and
crank branches. Maximum-agility defaults are 60 Hz, 1.5° maximum platform
target change per update, 0.5 mm maximum heave change, and a 2 ms input-vector
window. Targets are pipelined while motors move; Ctrl-C sends `ABORT` and holds.

The host commits REL_X/REL_Y only at Linux `SYN_REPORT` boundaries, with an
8 ms aggregation window so diagonal ball motion remains one vector. IK heave
selection includes a 50 lb static-load torque estimate plus a penalty inside
15° of top/bottom crank dead center. Use `--vector-window-ms` to tune input
aggregation without changing the production roller tool.

The agile IK objective prioritizes shortest continuous crank travel and minimum
heave motion; torque/dead-center/closure metrics remain secondary constraints.
The tuning CLI uses adaptive 0.5–1.5° waypoints and waits only at requested
endpoints rather than after every waypoint.

## Game tuning CLI

Run one persistent tuning session through the supervisor:

```bash
.venv/bin/python3 stewart_exp_tune.py
```

Useful commands:

```text
status
level
nudge roll 0.1
nudge pitch -0.1
trim level
motorcal
profile 60 200
profile select
threshold roll + 0.1
threshold roll - 0.1
threshold pitch + 0.1
threshold pitch - 0.1
mark roll + 0.5
agility roll 6 3
agility pitch 6 3
hold
quit
```

Threshold tests return level and increment one direction until the operator
presses `m` to mark reliable rolling. Results are saved to
`calibration/stewart_game_tuning.json`; the recommended activation threshold
is the largest directional result plus 0.1° (override with
`--threshold-margin`). `mark` records an observed value directly, while
`profile select` stores the active runtime profile as the game recommendation.

If model level is not physically level, keep the motors enabled, use small
`nudge roll/pitch` commands until a physical level is observed, then run
`trim level`. Future `level`, threshold, and agility commands use that stored
pose as zero without releasing motors or recalibrating crank vertical.

For per-motor adjustment with the other two motors actively holding, run
`motorcal` (all axes) or `motorcal 0`/`1`/`2`. Arrow keys adjust 20 pulses fine
or 100 pulses coarse; Enter accepts that motor. The saved `motor_trim_steps`
are applied to every future tuning and experimental roller target.

Agility tests use the active runtime profile, time each ±position reversal,
then record operator rating/notes. Change profiles without reflashing using
`profile speed accel`. Errors and Ctrl-C hold; they never disable.

## Return to production

1. Mechanically support the table.
2. Flash `arduino/uim5756pm_stewart`.
3. Recalibrate before production motion.

Do not hand experimental multi-turn step coordinates to the production solver.
