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

### Stewart tilt (Uno R3)

```bash
# Flash
arduino-cli compile --fqbn arduino:avr:uno arduino/uim5756pm_stewart
arduino-cli upload -p /dev/arduino-stewart --fqbn arduino:avr:uno arduino/uim5756pm_stewart

# Roller-ball control (non-motion dry-run first if unsure)
sudo python3 capture_usb_mouse.py --port /dev/arduino-stewart --enable --zero-on-start --disable-on-exit --center --pitch-sign=1 --roll-sign=1
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
