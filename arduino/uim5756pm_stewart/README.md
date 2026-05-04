# UIM5756PM 3-Axis Stewart Controller

Arduino sketch for three UIM5756PM pulse-direction motors on a 3-axis
roll/pitch/heave Stewart-style platform.

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
| Brown | 6 | `ENA` | Arduino common enable pin `D8` |
| Yellow | 7 | `GND` | Arduino `GND` and power supply negative |
| Purple | 8 | `TX` | Leave unconnected, config only |
| Gray | 9 | `RX` | Leave unconnected, config only |

Default Arduino control pins:

| Axis | Motor blue `PLS` | Motor green `DIR` | Optional limit |
| --- | ---: | ---: | ---: |
| 0 | D2 | D3 | D9 |
| 1 | D4 | D5 | D10 |
| 2 | D6 | D7 | D11 |

All three brown `ENA` wires connect to Arduino `D8` by default.

With white `COM` connected to Arduino `5V`, the Arduino output pins sink current
through the driver's opto-isolated inputs. The sketch is configured for this:
`PLS`, `DIR`, and `ENA` are active-low. If you wire `COM` differently, update
`PLS_ACTIVE_LOW`, `DIR_ACTIVE_LOW`, and `ENA_ACTIVE_LOW` in the sketch.

Keep the grounds common: Arduino `GND`, each yellow `GND` wire, and the motor
power supply negative/black side should be connected together. Do not power the
motors from the Arduino.

## Configure Before Use

Edit these constants near the top of `uim5756pm_stewart.ino`:

- `STEPS_PER_MM`: motor pulses per millimeter of actuator extension.
- `BASE_RADIUS_MM` and `PLATFORM_RADIUS_MM`: pivot triangle radii.
- `BASE_ANGLE_OFFSET_DEG` and `PLATFORM_ANGLE_OFFSET_DEG`: pivot angular
  offsets.
- `HOME_HEIGHT_MM`: neutral platform height.
- `MIN_EXTENSION_MM` and `MAX_EXTENSION_MM`: safe actuator travel from neutral.
- `MAX_ROLL_DEG`, `MAX_PITCH_DEG`, and heave limits.

The UIM5756PM/UIM344 wiring diagram uses pulse/direction inputs, so this sketch
uses `PLS`/`DIR` pulses. The purple `TX` and gray `RX` wires are for driver
configuration only and are not used by this Arduino controller.

## Serial Commands

Use `115200` baud with newline enabled.

```text
enable
pose <roll_deg> <pitch_deg> <heave_mm>
len <axis0_length_mm> <axis1_length_mm> <axis2_length_mm>
steps <axis0_steps> <axis1_steps> <axis2_steps>
status
zero
disable
help
```

Example:

```text
enable
zero
pose 0 0 0
pose 5 0 0
pose 0 -5 0
pose 0 0 10
status
```

`zero` tells the Arduino that the current physical position is the neutral
pose. Use it only after you have manually placed or homed the platform at the
neutral height.

## Upload

For an Uno on `/dev/ttyACM0`:

```bash
arduino-cli compile --fqbn arduino:avr:uno arduino/uim5756pm_stewart
arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:uno arduino/uim5756pm_stewart
```

## First Power-Up

Test with the motor power disabled first and watch `PLS`/`DIR` logic if you can.
Then test one axis at low speed with the platform unloaded. Verify direction,
travel limits, steps-per-mm, and active-low enable polarity before running tilt
commands.
