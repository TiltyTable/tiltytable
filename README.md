# tiltytable

Reconfigurable labyrinth maze driven by a Stewart-style tilt platform, controlled
with an arcade roller ball, and tracked with an Azure Kinect.

## Project site

The GitHub Pages proposal site lives in `docs/`.

Once this repo is under the `TiltyTable` organization, enable Pages from:

- Source: `Deploy from a branch`
- Branch: `main`
- Folder: `/docs`

Expected project-site URL:

```text
https://tiltytable.github.io/tiltytable/
```

## Commands

### Mouse control

```bash
sudo python3 capture_usb_mouse.py --port /dev/ttyACM0 --enable --zero-on-start --disable-on-exit --center --pitch-sign=1 --roll-sign=1
```

### Flash Arduino (UNO R4 Minima)

```bash
sudo ~/bin/arduino-cli compile --fqbn arduino:renesas_uno:minima --upload -p /dev/ttyACM0 arduino/uim5756pm_stewart
```
