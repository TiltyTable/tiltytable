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

```bash
# Flash
arduino-cli compile --fqbn arduino:avr:uno arduino/uim5756pm_stewart
arduino-cli upload -p /dev/arduino-stewart --fqbn arduino:avr:uno arduino/uim5756pm_stewart

# Calibrate: manually point ALL cranks STRAIGHT UP (max heave), then:
python3 stewart_calibrate.py --port /dev/arduino-stewart

# Roller-ball → Stewart tilt (primary workflow)
# 1. Manually set ALL cranks STRAIGHT UP (max heave)
# 2. Run (use sudo only if HID permission denied):
.venv/bin/python3 roller_ball.py
# or:  sudo .venv/bin/python3 roller_ball.py
#
# Optional once: install udev so HID works without sudo
#   sudo cp udev/99-tiltytable-rollerball.rules /etc/udev/rules.d/
#   sudo udevadm control --reload-rules && sudo udevadm trigger

# Low-level HID / debug (legacy)
# sudo .venv/bin/python3 capture_usb_mouse.py --list

```

### Module grid servos + LEDs (Uno R4 Minima)

```bash
# Flash combined servo+LED firmware
arduino-cli compile --fqbn arduino:renesas_uno:minima arduino/servo_calib
arduino-cli upload -p /dev/arduino-modules --fqbn arduino:renesas_uno:minima arduino/servo_calib

# Unified runtime CLI (uses calibration/ configs)
python3 calibration/tilt_table_cli.py --port /dev/arduino-modules
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
