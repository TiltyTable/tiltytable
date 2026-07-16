# Uno R4 WiFi Stewart executor

This is the live Stewart-platform firmware for the connected Arduino Uno R4
WiFi. It keeps the existing supervisor protocol and pin mapping, so the Python
IK and control programs require no serial-protocol changes.

## Board and wiring

Select board FQBN `arduino:renesas_uno:unor4wifi`. The R4 uses the same shield
pin positions as the former Uno R3:

| Axis | PLS | DIR | ENA |
| --- | ---: | ---: | ---: |
| 0 | D2 | D3 | D4 |
| 1 | D7 | D8 | D9 |
| 2 | D11 | D12 | D13 |

ENA is active-low. Each driver remains configured for MCS=4, giving 16,000
pulses per 360-degree crank revolution. The motor power supply and signal
ground must remain wired as before; the Arduino does not power the motors.

## Compile and flash

Mechanically support the table and stop the supervisor before flashing:

```bash
systemctl --user stop tiltytable-stewart-supervisor.service 2>/dev/null || true
arduino-cli lib install MobaTools@3.1.0
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi \
  arduino/uim5756_stewart_r4
arduino-cli upload -p /dev/arduino-stewart \
  --fqbn arduino:renesas_uno:unor4wifi arduino/uim5756_stewart_r4
```

The stable device rule is matched to this board's detected USB identity:
VID:PID `2341:1002`, serial `3CDC75443C14`. Reinstall
`udev/99-tiltytable-arduinos.rules` after replacing the R3.

Start `stewart_supervisor.py` after flashing, then use
`stewart_exp_probe.py --check-firmware`. The compatibility response remains
`OK EXP UIM5756PM_STEWART_EXP 1`, intentionally, so all supervisor clients
continue to work.

The live firmware and supervisor default to 230400 baud. Pass `--baud 115200`
only when using an older firmware build that still expects the former rate.

The supervisor defaults to `--dtr on` because the Freenove V5 WiFi USB bridge
does not forward serial traffic with DTR deasserted. Pass `--dtr off`
explicitly for boards where opening with DTR can reset the controller.

## Reset and position safety

The R3-only `MCUSR`, `.noinit`, early-init hook, and AVR watchdog code were
replaced with Renesas RA4M1 reset-status registers. EEPROM motor coordinates
are restored only after a clean external RESET-pin event. Power-on, voltage,
watchdog, software, and processor/bus fault resets require crank calibration;
open-loop coordinates cannot be trusted after those events.

Firmware still boots disabled and disarmed. Motion requires `ARM CONFIRM`, and
each `TARGET` remains limited to 12 degrees of crank travel from the previous
target.

## Performance guidance

The R4 executor uses MobaTools 3.1.0 or newer. MobaTools schedules all three
STEP/DIR axes from an RA4M1 GPT hardware timer, so pulse timing no longer
depends on how frequently the Arduino `loop()` runs. Its synchronized-group
API is not used because it refuses new targets during an active move, while
the game intentionally pipelines updated absolute targets at 60 Hz. Instead,
the firmware gives its three timer-driven axes proportional speed and ramp
profiles so their continuously changing targets remain coordinated.

The existing limit is deliberately unchanged: 90 crank-degrees/s equals
4,000 step pulses/s per fastest axis at MCS=4. The normal 40-degree/s profile
is about 1,778 pulses/s. MobaTools permits substantially higher timer-driven
rates on the R4, but the UIM5756PM motor/load remains open-loop. The R4 backend
uses a 25 microsecond STEP pulse, safely longer than the former 5 microsecond
minimum.

The timer backend's configured minimum step period is 50 microseconds, or a
nominal 20,000 pulses/s ceiling. With MCS=4 and the 20:1 gearbox:

| Crank speed | Pulse rate per axis | Motor speed before gearbox |
| ---: | ---: | ---: |
| 90°/s | 4,000/s | 300 RPM |
| 180°/s | 8,000/s | 600 RPM |
| 270°/s | 12,000/s | 900 RPM |
| 360°/s | 16,000/s | 1,200 RPM |
| 450°/s | 20,000/s | 1,500 RPM (library ceiling, no margin) |

Thus 360°/s is schedulable but not yet a validated loaded-platform speed. It
uses 80% of the nominal timer rate and leaves less margin when all three axes
step together. Raise the firmware and Python 90°/s validators together only
after testing 180, then 270, then 360°/s under load. Also test acceleration:
with a 12° target jump, a 500°/s² symmetric accelerate/decelerate move reaches
only about 77°/s before it must brake, so a higher speed limit alone may have no
effect on short moves.

Recommended progression:

1. Validate the unchanged profile under the real table load and check physical
   zero-return repeatability.
2. If more speed is needed, raise the firmware and Python profile limits
   together in small increments while checking for missed steps and reduced
   torque. The R4 removes CPU headroom as the likely first limit, but does not
   improve motor torque, gearbox behavior, or driver input limits.
3. If substantially higher rates are needed, characterize MobaTools timer
   jitter and motor following with an oscilloscope or logic analyzer before
   increasing the firmware and Python limits.
4. A fixed serial receive buffer can reduce parser overhead. The live link now
   runs at 230400 baud to reduce synchronous target-command latency.
5. Reducing MCS lowers pulse demand but also changes resolution and every
   steps-per-revolution conversion; do not change it without reconfiguring all
   three motors and recalibrating.

The former R3 sketch remains in `arduino/uim5756pm_stewart_exp/` only for a
hardware rollback. Do not flash that directory to the R4.
