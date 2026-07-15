# tiltytable

Reconfigurable labyrinth maze driven by a Stewart-style tilt platform, a
12×12 module grid of servo-actuated tiles with addressable LEDs, controlled
with an arcade roller ball, and tracked with a camera (MindVision now;
Azure Kinect later).

## Hardware roles

| Board | Alias | Firmware | Role |
| --- | --- | --- | --- |
| Uno R3 | `/dev/arduino-stewart` | `arduino/uim5756pm_stewart/` | 3-DOF tilt platform (UIM5756PM) |
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
# After all three motors read back MCS=8, flash runtime firmware
arduino-cli compile --fqbn arduino:avr:uno arduino/uim5756pm_stewart
arduino-cli upload -p /dev/arduino-stewart --fqbn arduino:avr:uno arduino/uim5756pm_stewart

# Calibrate: interactive per-axis (recommended)
.venv/bin/python3 stewart_calibrate.py

# Legacy: all cranks manually straight up first
.venv/bin/python3 stewart_calibrate.py --legacy --yes

# Roller-ball → Stewart tilt (runs interactive cal by default)
.venv/bin/python3 roller_ball.py
# Position-control tuning (defaults shown):
.venv/bin/python3 roller_ball.py \
  --max-tilt 4.6 --smooth 1.0 --scale 0.04
# Keep the loaded platform actively held after Ctrl-C:
.venv/bin/python3 roller_ball.py --hold-on-exit
# Supervised full-envelope circle (holds and persists level pose afterward):
.venv/bin/python3 stewart_circle_test.py
# or:  sudo .venv/bin/python3 roller_ball.py
#
# WARNING: --hold-on-exit continuously energizes all motors. Opening the Uno
# serial port again resets the board and disables them; mechanically support
# the table before reconnecting, flashing, power loss, or unplugging USB.
#
# Optional once: install udev so HID works without sudo
#   sudo cp udev/99-tiltytable-rollerball.rules /etc/udev/rules.d/
#   sudo udevadm control --reload-rules && sudo udevadm trigger

# Low-level HID / debug (legacy)
# sudo .venv/bin/python3 capture_usb_mouse.py --list

# Standalone trackball motion/button web monitor
.venv/bin/python3 trackball_input_web.py
# Open http://<jetson>:8089/ in a browser.

```

#### Roller position-control tuning

The roller ball controls absolute platform roll/pitch position. Stopping input
holds the last angle. This direct position mapping suits a freely moving
trackball and the platform's small workspace better than rate control.

- Gameplay height is fixed at **20 mm heave**.
- The host clamps total tilt to a **4.6° circle**, not a ±4.6° square. This
  keeps diagonal commands inside the modeled 4.8° all-direction envelope.
- Default `--smooth 1.0` sends the target directly; lower values add damping.
- Default gain is `--scale 0.04` degrees per trackball count.

Supervised test order (mechanically support the 50 lb table against reset or
power loss):

1. Calibrate and verify level at 20 mm.
2. Test each cardinal direction with small ball movements.
3. Test diagonals and confirm the displayed magnitude remains ≤4.6°.
4. Hold at the boundary, then reverse input; it should move inward immediately.
5. Only if direct response is too sharp, retry with `--smooth 0.8`, then `0.6`.

The circle test defaults to the modeled-safe 4.6° radius. Larger experimental
radii require `--allow-experimental-radius`; firmware IK still rejects
unreachable poses. Do not infer loaded reachability from how far an unpowered
table can sag—the crank/arm closure and all three legs constrain active motion.

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
