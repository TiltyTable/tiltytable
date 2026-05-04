# tiltytable

## Commands

### Mouse control

```bash
sudo python3 capture_usb_mouse.py --port /dev/ttyACM0 --enable --zero-on-start --disable-on-exit --center --pitch-sign=1 --roll-sign=1
```

### Flash Arduino (UNO R4 Minima)

```bash
sudo ~/bin/arduino-cli compile --fqbn arduino:renesas_uno:minima --upload -p /dev/ttyACM0 arduino/uim5756pm_stewart
```