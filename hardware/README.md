# PCA9685 + Arduino R3 + SG90 Setup

This folder adds a simple hardware bridge for four SG90 servos:

- `arduino/archive/pca9685_servo_bridge/pca9685_servo_bridge.ino` (archived; was early Uno R3 servo bridge).
  Live module board firmware is `arduino/servo_calib/` on the Uno R4.
- `servo_cli.py` runs on your Mac and talks to the Arduino over USB serial.
- `servo_calibration.json` stores the current calibration defaults for channels `0` through `3`.
- `run_control_center.py` starts the Raspberry Pi hosted web app.
- `control_center/server.py` serves the REST API, serial bridge, and MJPEG webcam stream.
- `control_center/static/` contains the browser UI for live servo control and configuration.

## Control Center web app

The new web app is intended to run directly on the Raspberry Pi with both the Arduino and the USB webcam plugged into the Pi.

Important: the camera view is captured on the Pi itself from the Pi's Linux video devices such as `/dev/video0`. It is **not** using the browser camera from the laptop, phone, or tablet that is viewing the page.

From the `marble-maze` folder:

```sh
python3 -m pip install -r hardware/requirements.txt
python3 hardware/run_control_center.py --host 0.0.0.0 --port 8080
```

Then open:

- `http://raspberrypi.local:8080` from another device on the same network, or
- `http://127.0.0.1:8080` directly on the Pi

What the web app includes:

- serial auto-discovery and manual serial-port selection for the Arduino bridge
- per-servo live angle, pulse, nudge, home, enable, disable, and named-state controls
- editable servo profile calibration for `min_us`, `max_us`, `home_deg`, `invert`, and saved `wall` / `floor` / `hole` pulse values
- global actions for `apply-config`, `save-config`, `home all`, `enable all`, `disable all`, and `cycle all`
- USB webcam device selection from the Pi's `/dev/video*` devices plus width / height / FPS / JPEG-quality configuration
- live MJPEG camera preview and one-click snapshot endpoint at `/api/camera/snapshot.jpg`
- persistent runtime state in `hardware/control_center/runtime_state.json`

### Run on boot with systemd

An example systemd unit is included at:

- `hardware/control_center/marble-maze-control-center.service.example`

On the Pi, copy it into `/etc/systemd/system/`, adjust `WorkingDirectory` if needed, then enable it:

```sh
sudo cp hardware/control_center/marble-maze-control-center.service.example /etc/systemd/system/marble-maze-control-center.service
sudo systemctl daemon-reload
sudo systemctl enable --now marble-maze-control-center.service
sudo systemctl status marble-maze-control-center.service
```

## Wiring

### Arduino Uno R3 to PCA9685

| Arduino Uno R3 | PCA9685 | Notes |
| --- | --- | --- |
| `5V` | `VCC` | Logic power only |
| `GND` | `GND` | Must be shared with servo supply ground |
| `A4` / `SDA` | `SDA` | I2C data |
| `A5` / `SCL` | `SCL` | I2C clock |
| optional `A3` | `OE` | The current sketch drives `A3` low during startup. If unused, tie `OE` to `GND` |

On the Uno R3, the dedicated `SDA` / `SCL` header near `AREF` is electrically the same I2C bus as `A4` / `A5`.

### Servo power

Do **not** run four SG90s from the Arduino `5V` pin.

Use a separate regulated `5V` supply rated for at least `3A`, and `4A` is better if several servos may move together.

| External 5V supply | PCA9685 / system |
| --- | --- |
| `+5V` | `V+` or servo power terminal on the PCA9685 |
| `GND` | `GND` on the PCA9685 |

The Arduino ground, PCA9685 ground, and external 5V supply ground must all be tied together.

Adding a `470 uF` to `1000 uF` electrolytic capacitor across the PCA9685 servo power rails is a good idea to reduce brownouts when the servos start moving.

### Servos

Plug the four SG90s into PCA9685 channels `0`, `1`, `2`, and `3`.

Typical SG90 wire colors:

- `brown` = ground
- `red` = +5V
- `orange` = signal

Most PCA9685 boards put `GND`, `V+`, and `SIG` in a row on each 3-pin header. Double-check the silkscreen on your board before powering up.

## Upload the Arduino sketch

1. Open `arduino/archive/pca9685_servo_bridge/pca9685_servo_bridge.ino` in the Arduino IDE (archived; prefer `arduino/servo_calib/` for the live R4).
2. Select board `Arduino Uno`.
3. Select the Arduino serial port.
4. Upload the sketch.

The sketch uses only the built-in `Wire` library, so there are no extra Arduino dependencies to install.

## Install the Python tools

From the `marble-maze` folder:

```sh
python3 -m pip install -r hardware/requirements.txt
```

Then list ports:

```sh
python3 hardware/servo_cli.py ports
```

If only one Arduino-like serial port is connected, the CLI can usually auto-pick it. Otherwise pass `--port`.

## First run

Apply the default four-servo calibration:

```sh
python3 hardware/servo_cli.py --port /dev/cu.usbmodemXXXX apply-config
```

Open the interactive shell:

```sh
python3 hardware/servo_cli.py --port /dev/cu.usbmodemXXXX interactive
```

Inside the shell:

```text
write
status
enable all
home all
angle 0 90
nudge 0 25
sweep 2 30 150 5 80
cycle-all 2 50 35 150
cal 0 min 520
cal 0 max 2350
cal 0 home 87
invert 3 on
capture 0 floor
state 0 floor
states
save
```

## Named positions

Each servo can now store three named pulse positions:

- `wall`: full extension / wall raised
- `floor`: no extension / floor
- `hole`: full retraction / hole

Recommended teaching flow for one servo:

```text
write
enable 0
pulse 0 1500
nudge 0 20
nudge 0 -10
capture 0 floor

pulse 0 2100
capture 0 wall

pulse 0 900
capture 0 hole

states 0
save
```

Later you can recall those positions directly:

```text
state 0 wall
state 0 floor
state 0 hole
```

## Cycling all servos

To exercise all configured servos together across their current `min_us` to `max_us` range:

```text
cycle-all 2 50 35 150
```

That means:

- `2` full min -> max -> min cycles
- `50` interpolation steps per half-cycle
- `35` ms delay between steps
- `150` ms pause at each end

Smaller step counts and shorter delays make motion faster. Larger step counts and longer delays make motion slower and smoother.

## Command summary

Inside interactive mode:

- `status [all]`
- `states [target]`
- `angle <target> <deg>`
- `pulse <target> <microseconds>`
- `nudge <target> <delta_us>`
- `sweep <target> <start> <end> <step> [delay_ms]`
- `cycle-all [cycles] [steps] [delay_ms] [hold_ms]`
- `state <target> <wall|floor|hole>`
- `capture <target> <wall|floor|hole>`
- `set-state <target> <wall|floor|hole> <pulse_us>`
- `home <target|all>`
- `enable <target|all>`
- `disable <target|all>`
- `cal <target> min|max|home <value>`
- `invert <target> on|off`
- `name <target> <new_name>`
- `write`
- `save`

Targets can be either a channel number like `0` or a configured servo name like `servo0`.

## Calibration notes

The default JSON file assumes:

- servo channels `0..3`
- pulse range `500..2400 us`
- home position `90 deg`
- no inversion

Those are safe starting values for many SG90 servos, but they are not universal. If a servo hits a mechanical stop early, back off immediately and reduce its `min_us` / `max_us` range before continuing.

Named positions are stored locally in `servo_calibration.json`. They are host-side presets used by the CLI, while `min_us`, `max_us`, `home_deg`, and `invert` are the values sent down to the Arduino with `write`.
