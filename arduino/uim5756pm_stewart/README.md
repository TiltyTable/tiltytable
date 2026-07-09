# UIM5756PM 3-Axis Stewart Controller

Arduino sketch for three UIM5756PM pulse-direction motors on a 3-axis
roll/pitch/heave Stewart-style platform. Runs on the **Uno R3**
(`/dev/arduino-stewart` / `/dev/ttyACM0`).

The Uno R4 Minima is a separate board that drives the module-grid PCA9685
servos and WS2812 LEDs (`arduino/servo_calib/`).

## Calibration (required)

On boot the controller does **not** know where the cranks are. Motion
commands (`enable`, `pose`, `vel`, `jog`, …) are rejected until you calibrate.

1. Leave motors **disabled** (default after boot / after `disable`).
2. Manually turn all three cranks so they point **straight up**
   (crank pin at maximum height = **max heave**).
3. Send:

```text
calibrate
```

(`zero` is kept as an alias for the same command.)

That pose is recorded as heave ≈ `+25.831 mm` at roll=pitch=0, with crank
angle `90°` relative to the IK model (`NEUTRAL_CRANK_DEG = 180°` is
horizontal).

Host helper:

```bash
python3 stewart_calibrate.py --port /dev/arduino-stewart
# or non-interactive once cranks are already up:
python3 stewart_calibrate.py --port /dev/arduino-stewart --yes
```

## Wiring

Your diagram shows two cables:

- Power cable: red and black, 22 AWG.
- Signal cable: white/green/blue/brown/yellow plus purple/gray config serial.

Wire each motor like this:

| Motor wire | Driver pin | Diagram label | Connect to |
| --- | ---: | --- | --- |
| Red | 1 | `+24-48 VDC` | Motor power supply positive |
| Black | 2 | `0 VDC` | Motor power supply negative |
| White | 3 | `COM` | Arduino `5V` |
| Green | 4 | `DIR` | Arduino DIR pin for that axis |
| Blue | 5 | `PLS` | Arduino PLS pin for that axis |
| Brown | 6 | `ENA` | Arduino ENA pin for that axis |
| Yellow | 7 | `GND` | Arduino `GND` and power supply negative |
| Purple | 8 | `TX` | Leave unconnected, config only |
| Gray | 9 | `RX` | Leave unconnected, config only |

Default Arduino control pins (Uno R3):

| Axis | `PLS` | `DIR` | `ENA` |
| --- | ---: | ---: | ---: |
| 0 | D2 | D3 | D4 |
| 1 | D5 | D6 | D7 |
| 2 | D10 | D11 | D12 |

With white `COM` connected to Arduino `5V`, the Arduino output pins sink current
through the driver's opto-isolated inputs. The sketch is configured for this:
`ENA` is active-low (`ENA_ACTIVE_LOW = true`). If you wire `COM` differently,
update that constant in the sketch.

Keep the grounds common: Arduino `GND`, each yellow `GND` wire, and the motor
power supply negative/black side should be connected together. Do not power the
motors from the Arduino.

## Configure Before Use

Edit these constants near the top of `uim5756pm_stewart.ino`:

- `STEPS_PER_CRANK_REV`: motor pulses per crank revolution.
- Platform geometry (`TABLE_ROD_RADIUS_MM`, `CRANK_RADIUS_MM`, `ARM_LENGTH_MM`, …).
- `CALIBRATE_CRANK_DEG` / `CALIBRATE_HEAVE_MM` (must stay consistent with geometry).
- `MAX_ROLL_DEG`, `MAX_PITCH_DEG`, and heave limits.
- Speed / acceleration (`MAX_CRANK_SPEED_DEG_S`, `MAX_CRANK_ACCEL_DEG_S2`).

See also `analysis/KINEMATICS.md`.

## Serial Commands

Use `115200` baud with newline enabled.

```text
calibrate                 # cranks straight up = max heave (alias: zero)
enable [axis]
disable [axis]
pose <roll_deg> <pitch_deg> <heave_mm>
vel <roll_deg_s> <pitch_deg_s> <heave_mm_s>
angle <a0_deg> <a1_deg> <a2_deg>
steps <s0> <s1> <s2>
jog <axis> <pulses>
status
help
```

Example after calibration:

```text
calibrate
enable
pose 0 0 0
pose 3 0 0
status
disable
```

## Upload

```bash
arduino-cli compile --fqbn arduino:avr:uno arduino/uim5756pm_stewart
arduino-cli upload -p /dev/arduino-stewart --fqbn arduino:avr:uno arduino/uim5756pm_stewart
```

## First Power-Up

1. Flash firmware; confirm `status` shows `calibrated 0`.
2. Manually set cranks straight up; send `calibrate`; confirm `calibrated 1`.
3. With motor power still cautious, `enable` then small `pose` / `jog` tests.
4. Verify direction; flip `DIR_INVERT[i]` if an axis is backwards.
