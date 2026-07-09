# UIM5756PM 3-Axis Stewart Controller

Arduino sketch for three UIM5756PM pulse-direction motors on a 3-axis
roll/pitch/heave Stewart-style platform. Runs on the **Uno R3**
(`/dev/arduino-stewart` / `/dev/ttyACM0`).

The Uno R4 Minima is a separate board that drives the module-grid PCA9685
servos and WS2812 LEDs (`arduino/servo_calib/`).

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
- `MAX_ROLL_DEG`, `MAX_PITCH_DEG`, and heave limits.
- Speed / acceleration (`MAX_CRANK_SPEED_DEG_S`, `MAX_CRANK_ACCEL_DEG_S2`).

The UIM5756PM/UIM344 wiring diagram uses pulse/direction inputs, so this sketch
uses `PLS`/`DIR` pulses. The purple `TX` and gray `RX` wires are for driver
configuration only and are not used by this Arduino controller.

## Serial Commands

Use `115200` baud with newline enabled.

```text
enable [axis]
disable [axis]
pose <roll_deg> <pitch_deg> <heave_mm>
vel <roll_deg_s> <pitch_deg_s> <heave_mm_s>
angle <a0_deg> <a1_deg> <a2_deg>
steps <s0> <s1> <s2>
jog <axis> <pulses>
zero
status
help
```

Example:

```text
enable
zero
pose 0 0 0
pose 5 0 0
pose 0 -5 0
pose 0 0 5
status
disable
```

`zero` tells the Arduino that the current physical position is the neutral
pose. Use it only after you have manually placed or homed the platform at the
neutral height.

## Upload

For the Uno R3 Stewart board (`/dev/arduino-stewart` or `/dev/ttyACM0`):

```bash
arduino-cli compile --fqbn arduino:avr:uno arduino/uim5756pm_stewart
arduino-cli upload -p /dev/arduino-stewart --fqbn arduino:avr:uno arduino/uim5756pm_stewart
```

## First Power-Up

Test with the motor power disabled first and watch `PLS`/`DIR` logic if you can.
Then test one axis at low speed with the platform unloaded. Verify direction,
travel limits, steps-per-rev, and active-low enable polarity before running tilt
commands.
