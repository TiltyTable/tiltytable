# tiltytable

Reconfigurable labyrinth maze driven by a Stewart-style tilt platform, a
12×12 module grid of servo-actuated tiles with addressable LEDs, controlled
with an arcade roller ball, and tracked with a camera (MindVision now;
Azure Kinect later).

## Hardware roles

| Board | Alias | Firmware | Role |
| --- | --- | --- | --- |
| Uno R3 | `/dev/arduino-stewart` | `arduino/uim5756pm_stewart_exp/` | Supervisor-owned 3-DOF tilt platform |
| Uno R4 Minima | `/dev/arduino-modules` | `arduino/servo_calib/` | Module-grid PCA9685 servos + WS2812 LEDs |

## Project site

The GitHub Pages site lives in `docs/`.

Once Pages is enabled from `main` / `/docs`, the expected URL is:

```text
https://tiltytable.github.io/tiltytable/
```

## Commands

### Arcade cabinet (854×480 projector)

```bash
# Test the complete UI without touching hardware
./run_arcade.sh --simulation

# Live module-grid levels (Kinect and Stewart are intentionally V2/V3)
./run_arcade.sh
```

See `arcade/README.md` for keyboard controls, scoring, and level details.

### Stewart tilt (Uno R3)

All three UIM5756PM motors must be configured to **MCS=8** before flashing the
motion firmware. The one-motor configurator uses white TX → A4 and green RX ←
A5; see `arduino/uim5756pm_config/README.md`.

```bash
# Compile and upload only with the table mechanically supported
arduino-cli compile --fqbn arduino:avr:uno arduino/uim5756pm_stewart_exp
arduino-cli upload -p /dev/arduino-stewart --fqbn arduino:avr:uno \
  arduino/uim5756pm_stewart_exp

# Keep this process (normally its user service) running permanently
.venv/bin/python3 stewart_supervisor.py

# Trackball X/Y directly controls pitch/roll position
.venv/bin/python3 stewart_platform_control_position.py

# Trackball swipes add pitch/roll angular velocity with exponential decay
.venv/bin/python3 stewart_platform_control_velocity.py
#
# Optional once: install udev so HID works without sudo
#   sudo cp udev/99-tiltytable-rollerball.rules /etc/udev/rules.d/
#   sudo udevadm control --reload-rules && sudo udevadm trigger

# Standalone trackball motion/button web monitor
.venv/bin/python3 trackball_input_web.py
# Open http://<jetson>:8089/ in a browser.

```

All supported clients use the supervisor socket and read its current absolute
motor coordinates during connection startup. The host IK may choose heave
freely, while an exact level request returns to the configured startup heave.
The retired direct-serial implementation is retained under
`archive/stewart_legacy/` for historical reference only.

### Module grid servos + LEDs (Uno R4 Minima)

```bash
# Flash combined servo+LED firmware
arduino-cli compile --fqbn arduino:renesas_uno:minima arduino/servo_calib
arduino-cli upload -p /dev/arduino-modules --fqbn arduino:renesas_uno:minima arduino/servo_calib

# Unified runtime CLI (uses calibration/ configs)
python3 calibration/tilt_table_cli.py --port /dev/arduino-modules

# LED color calibration (8 named colors + per-tile direct RGB overrides)
.venv/bin/python3 calibration/led_color_cal_tool.py --port /dev/arduino-modules

# Button UI: select a 4x4 module, cycle the existing palette colours,
# then set direct per-module R/G/B values with sliders and save.
.venv/bin/python3 calibration/led_color_cal_gui.py --port /dev/arduino-modules

# Apply a tile-map JSON to the table (walls/floors/pits)
.venv/bin/python3 game_runner.py maps/tile-map-2026-07-12T04-05-46-868Z.json --once
```

### udev aliases

```bash
sudo cp udev/99-tiltytable-arduinos.rules /etc/udev/rules.d/
sudo cp udev/99-tiltytable-mindvision.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
ls -l /dev/arduino-*
```

### Camera (MindVision HT-SUA134GM)

Needs MindVision's ARM64 `linuxSDK` (`libMVSDK.so`) — not V4L2. See `camera/README.md`.

```bash
python3 camera/mindvision_capture.py --probe
```
