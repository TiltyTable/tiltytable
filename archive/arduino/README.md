# Archived Arduino sketches

Obsolete / superseded sketches moved here on 2026-07-11.

**Do not flash these** to the live Jetson boards. Current firmware:

| Board | Alias | Sketch |
| --- | --- | --- |
| Uno R3 | `/dev/arduino-stewart` | `../../arduino/uim5756pm_stewart_exp/` |
| Uno R4 Minima | `/dev/arduino-modules` | `../../arduino/servo_calib/` |

## Contents

| Directory | Was | Notes |
| --- | --- | --- |
| `pca9685_serial_servo/` | `arduino/` | Early servo-only serial bridge (+ Python helpers) |
| `pca9685_sg90_sweep/` | `arduino/` | Sweep bring-up test |
| `ws2811_serial_leds/` | `arduino/` | LED-only serial |
| `pca9685_servo_bridge/` | `hardware/arduino/` | Older hardware-stack servo bridge |
| `tilt_table_leds/` | `hardware/arduino/` | Older LED sketch |
| `tilt_table_leds_diagnostic/` | `hardware/arduino/` | LED diagnostic |

Live module grid (servos **and** LEDs) is unified in `servo_calib.ino`.
