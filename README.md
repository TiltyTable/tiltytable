# TiltyTable arcade game

TiltyTable is a physical 12×12 marble-maze arcade game. A USB trackball tilts
a three-motor Stewart platform, an Arduino-controlled module grid raises and
recesses tiles and drives their LEDs, an Azure Kinect tracks the ball and the
moving table, and a Flask arcade server runs the levels, scoring, projector UI,
and module animations.

This README describes the supported game stack. Retired direct-serial Stewart
programs and firmware are kept under `archive/stewart_legacy/` and must not be
used while the persistent Stewart supervisor is running.

## What the complete game needs

### Hardware and firmware

| Device | Stable device path | Firmware used during the game | Board/FQBN |
| --- | --- | --- | --- |
| Stewart controller | `/dev/arduino-stewart` | `arduino/uim5756_stewart_r4/` | Uno R4 WiFi, `arduino:renesas_uno:unor4wifi` |
| Module servo + LED controller | `/dev/arduino-modules` | `arduino/servo_calib/` | Uno R4 Minima, `arduino:renesas_uno:minima` |
| Trackball | Linux `event-mouse` device | No firmware from this repository | USB HID `13ba:0018` |
| Ball/table camera | Azure Kinect device 0 | Azure Kinect Sensor SDK | USB camera + IMU |

`arduino/uim5756pm_config/` is a temporary one-motor setup sketch. It is used
only to configure each UIM5756PM motor to MCS=4; it is not game firmware.

### Programs running for a complete live game

The current system is four cooperating host processes:

| Process | Purpose | Owns |
| --- | --- | --- |
| `stewart_supervisor.py` | Permanent no-DTR serial owner and motion lease server | `/dev/arduino-stewart` |
| `kinect_web_control.py` | Azure Kinect capture, ball detection, continuous table-pose fit, ball-to-cell API | Azure Kinect; HTTP `:8080` |
| One `stewart_platform_control_*.py` | Trackball input, free-heave IK, and Stewart targets | Trackball; supervisor motion lease |
| `run_arcade.sh` | Arcade server, projector UI, level logic, servos, and LEDs | `/dev/arduino-modules`; HTTP `:8081` |

The launcher does not currently start Kinect or Stewart control itself. Start
those services first using the commands in [Run the complete game](#run-the-complete-game).

## Essential source and configuration

| Game element | Runtime source | Required configuration |
| --- | --- | --- |
| Trackball + Stewart position control | `stewart_platform_control_position.py`, `stewart_platform_control_common.py` | CLI gains/signs; optional `--step-offsets` |
| Trackball + Stewart velocity control | `stewart_platform_control_velocity.py`, `stewart_platform_control_common.py` | CLI gain, decay, signs, and limits |
| Stewart IK | `analysis/stewart_exp_kinematics.py` | Geometry constants in that module |
| Stewart serial ownership | `stewart_supervisor.py`, `stewart_supervisor_client.py`, `stewart_exp_probe.py` | Supervisor Unix socket; Arduino EEPROM position/calibration |
| Module servos and LEDs | `arcade/hardware.py`, `game_runner.py` | Grid, servo, palette, and LED calibration JSON files below |
| Azure Kinect ball tracking | `kinect_web_control.py`, `ball_tracker.py`, `camera_geometry.py`, `live_capture_viewer.py` | `config.json` ball settings |
| Continuous table tracking | `table_pose.py` | `config.json` marker dimensions/threshold; five measured marker locations in `TableGeometry._rebuild()` |
| Arcade game | `arcade/server.py`, `arcade/engine.py`, `arcade/survival_lava.py` | `arcade/levels.json`, `maps/arcade-level-*.json` |
| Cabinet UI | `arcade/static/` | 854×480 browser kiosk launched by `run_arcade.sh` |

The live module grid requires all of these calibration files:

- `calibration/led_grid_config.json`: all 144 cells mapped to nine physical LED strands.
- `calibration/servo_grid_config.json`: all 144 cells mapped to PCA9685 address/channel pairs.
- `calibration/servo_config_0x40.json` through `servo_config_0x48.json`: recessed, neutral, and extended pulse widths for every mapped servo.
- `calibration/led_palette.json`: game color definitions.
- `calibration/led_color_cal.json`: per-cell/module LED color corrections.

Arcade preflight rejects live mode unless both grids cover 144 cells, all nine
LED strands exist, and every mapped servo has all three calibrated positions.

`kinect_web_control.py` also imports optional depth-servo UI support from
`depth_servo_control.py`. That four-servo depth loop is not started by the game
and must not be pointed at `/dev/arduino-modules` while the arcade owns it.
No active code imports anything under `archive/arduino/`.

### Present in the repository but not required at runtime

- `stewart_exp_probe.py`, `stewart_exp_tune.py`, and the files under
  `calibration/` are bring-up, calibration, and diagnostic tools. Module tools
  require the arcade to be stopped; Stewart tools require the trackball motion
  client to be stopped but normally keep using the supervisor.
- `stewart_exp_roller_ball.py` is an additional supervisor-based position
  controller that consumes `stewart_game_tuning.json`. It is optional when one
  of the two `stewart_platform_control_*.py` programs is used.
- `game_runner.py` and `calibration/tilt_table_cli.py` are standalone
  module-grid tools. The arcade imports reusable pieces from `game_runner.py`,
  but neither CLI should run beside the live arcade server.
- `trackball_input_web.py` is a standalone input monitor, not a game service.
- `archive/camera/` contains the MindVision/UVC path and is not used by the Azure
  Kinect game stack.
- `archive/hardware/`, `depth_servo_control.py`, and `extrinsics.json` are older or
  optional experiments. Everything under `archive/arduino/` is outside the
  active game and calibration path.
- `docs/` and `design/` are project-site/design material, not runtime code.

## Host setup

Run commands from the repository root unless a command explicitly changes
directory.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The Azure Kinect path additionally requires the system Azure Kinect Sensor SDK
(`libk4a` and development files) and its Python wrapper:

```bash
.venv/bin/pip install pyk4a
```

The camera service must be able to import `pyk4a` and open device 0. It uses
the Kinect's factory depth/IMU calibration directly; `extrinsics.json` is not
part of the active ball/table tracking path.

Install Arduino cores and the two non-core sketch libraries:

```bash
arduino-cli core install arduino:renesas_uno
arduino-cli lib install MobaTools@3.1.0
arduino-cli lib install "Adafruit NeoPixel"
```

Install stable serial aliases and non-root trackball access:

```bash
sudo cp udev/99-tiltytable-arduinos.rules /etc/udev/rules.d/
sudo cp udev/99-tiltytable-rollerball.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG plugdev "$USER"
```

Log out and back in after changing group membership. Verify:

```bash
ls -l /dev/arduino-stewart /dev/arduino-modules
ls -l /dev/input/by-id/*event-mouse
```

## Flash the Arduinos

### Uno R4 Minima: module servos and LEDs

This is the only live module-grid sketch. It controls PCA9685 boards
`0x40`–`0x48` and all nine WS2812B strands over one 115200-baud connection.

```bash
arduino-cli compile --fqbn arduino:renesas_uno:minima arduino/servo_calib
arduino-cli upload -p /dev/arduino-modules \
  --fqbn arduino:renesas_uno:minima arduino/servo_calib
```

Do not flash any sketch under `archive/arduino/` for the live game.

### Uno R4 WiFi: configure the three Stewart motors once

The live Stewart firmware assumes every UIM5756PM is configured to **MCS=4**,
which corresponds to 16,000 host steps per crank revolution with the 20:1
gearbox. Mixed MCS settings invalidate all IK step targets.

Mechanically support the loaded platform, stop the supervisor, and connect
exactly one motor UART at a time as documented in
`arduino/uim5756pm_config/README.md`.

```bash
systemctl --user stop tiltytable-stewart-supervisor.service 2>/dev/null || true
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi arduino/uim5756pm_config
arduino-cli upload -p /dev/arduino-stewart \
  --fqbn arduino:renesas_uno:unor4wifi arduino/uim5756pm_config
arduino-cli monitor -p /dev/arduino-stewart -c baudrate=115200
```

For each of the three motors, issue `get`, then `set 4 CONFIRM`. Power-cycle the
motor supply and verify `get` reports MCS=4 for each motor before continuing.

### Uno R4 WiFi: flash the live Stewart executor

Keep the platform mechanically supported whenever uploading or reopening its
serial device.

```bash
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi arduino/uim5756_stewart_r4
arduino-cli upload -p /dev/arduino-stewart \
  --fqbn arduino:renesas_uno:unor4wifi arduino/uim5756_stewart_r4
```

After flashing, start the supervisor before using any Stewart Python tool.
Supported clients use its Unix socket and do not open the Arduino directly.

## One-time calibration and bring-up

Never run the arcade server, `game_runner.py`, or two module calibration tools
at the same time: each expects exclusive ownership of `/dev/arduino-modules`.

### 1. Module LED mapping

First confirm all nine strands and determine their real pixel counts:

```bash
.venv/bin/python3 calibration/led_strip_test.py \
  --port /dev/arduino-modules --together
```

Then tag each physical LED pixel to its 12×12 cell. This writes
`calibration/led_grid_config.json`:

```bash
.venv/bin/python3 calibration/led_cal_tool.py \
  --port /dev/arduino-modules
```

### 2. Per-servo motion envelopes

Calibrate recessed, neutral, and extended pulse widths for all nine PCA9685
boards. Run from `calibration/` because this tool's board-config defaults are
relative to its working directory:

```bash
cd calibration
../.venv/bin/python3 servo_tool.py \
  --port /dev/arduino-modules calibrate
cd ..
```

This updates `servo_config_0x40.json` through `servo_config_0x48.json`. Module
servos are pulse-then-release; do not leave them energized against a stop.

### 3. Servo-to-cell mapping

Visually associate every PCA9685 channel with the LED cell that moves. This
writes `calibration/servo_grid_config.json`:

```bash
cd calibration
../.venv/bin/python3 servo_grid_cal_tool.py \
  --port /dev/arduino-modules
cd ..
```

### 4. LED color matching

Tune the game palette across modules with either the terminal tool or Tk GUI.
Both update `calibration/led_color_cal.json`:

```bash
.venv/bin/python3 calibration/led_color_cal_tool.py \
  --port /dev/arduino-modules

# Alternative GUI; requires python3-tk
.venv/bin/python3 calibration/led_color_cal_gui.py \
  --port /dev/arduino-modules
```

### 5. Validate the complete module grid

```bash
.venv/bin/python3 -m arcade.preflight \
  --hardware --module-port /dev/arduino-modules --port 8081
```

An optional physical smoke test is available, but it moves every calibrated
channel and should be run only with an operator watching the table:

```bash
.venv/bin/python3 calibration/quick_test.py /dev/arduino-modules
```

### 6. Stewart crank and physical-level calibration

Install and start the persistent supervisor:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/tiltytable-stewart-supervisor.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tiltytable-stewart-supervisor.service
systemctl --user status tiltytable-stewart-supervisor.service
```

The supplied service contains absolute `/home/zipline/tiltytable` and
`/home/zipline/bin/arduino-cli` paths. Edit the copied service if the checkout
or `arduino-cli` lives elsewhere. Keep `Restart=no`: an automatic restart can
reopen serial while the platform is loaded.

Confirm the firmware through a read-only supervisor lease:

```bash
.venv/bin/python3 stewart_exp_probe.py --check-firmware
```

Run the tuning session with the platform mechanically protected:

```bash
.venv/bin/python3 stewart_exp_tune.py
```

If Arduino `STATUS` is not calibrated, the tool starts interactive per-crank
vertical calibration. Afterward use `nudge`, `motorcal`, and `trim level` as
needed to establish physical level, then `profile select` to save the game
motion profile. Results live in `calibration/stewart_game_tuning.json`.

That tuning file is consumed by `stewart_exp_roller_ball.py`. The two newer
`stewart_platform_control_position.py` and
`stewart_platform_control_velocity.py` programs deliberately use their CLI
settings instead: they default to zero motor offsets and accept explicit
`--step-offsets S0 S1 S2`, `--startup-heave`, and motion-profile arguments.
If tuning establishes required differential `motor_trim_steps`, copy those
three values into the chosen game controller's `--step-offsets`; the new
controllers do not silently load the tuning JSON.

Every supported motion client gets the current absolute motor positions from
the Arduino/supervisor during startup before constructing its initial IK pose.

### 7. Azure Kinect ball and table tracking

Mount five retroreflective markers in the geometry documented at the top of
`table_pose.py`. Verify the measured marker locations in
`TableGeometry._rebuild()` and the table size used by `world_to_cell()` match
the physical build. Adjustable marker dimensions, thresholds, ball radius,
and camera settings live in `config.json`.

Start the camera service on port 8080:

```bash
.venv/bin/python3 kinect_web_control.py --http-port 8080
```

Open `http://127.0.0.1:8080/` and tune the marker and ball IR thresholds. The
service is ready for the game when these endpoints report table tracking and a
detected ball with a grid cell:

```bash
curl -s http://127.0.0.1:8080/api/pose/state
curl -s http://127.0.0.1:8080/api/state
```

`config.json` currently enables ball tracking. If a different config is used,
pass `--ball-tracking` or set `ball.ball_tracking` to `true`.

## Run the complete game

Use four terminals, or convert terminals 2–4 into supervised services after
hardware validation.

### Terminal 1: permanent Stewart supervisor

Normally this is already running as the user service:

```bash
systemctl --user start tiltytable-stewart-supervisor.service
```

For foreground diagnostics instead:

```bash
.venv/bin/python3 stewart_supervisor.py
```

Never run both instances at once.

### Terminal 2: Azure Kinect tracker

```bash
.venv/bin/python3 kinect_web_control.py --http-port 8080
```

### Terminal 3: trackball and Stewart control

Run exactly one controller. Direct position control is the recommended game
mode: trackball X changes pitch and Y changes roll.

```bash
.venv/bin/python3 stewart_platform_control_position.py \
  --max-tilt 10 --degrees-per-count 0.04
```

The alternative interprets swipes as angular-velocity impulses with decay:

```bash
.venv/bin/python3 stewart_platform_control_velocity.py \
  --max-tilt 10 --velocity-per-count 0.6 --velocity-decay-s 0.35
```

Both programs default to the supervisor socket, auto-detect the trackball,
initialize from current Arduino motor positions, and allow IK to choose heave.
Use `--roll-sign -1` or `--pitch-sign -1` only if the corresponding physical
axis is reversed.

### Terminal 4: arcade server and projector kiosk

The Kinect service defaults to port 8080, so run the arcade on 8081 and pass
the Kinect base URL:

```bash
TILTYTABLE_KINECT_URL=http://127.0.0.1:8080 \
TILTYTABLE_ARCADE_PORT=8081 \
./run_arcade.sh
```

Use `--no-kiosk` to run only the server and open
`http://127.0.0.1:8081/` manually.

Current integration behavior:

- The arcade process exclusively drives module servos and LEDs.
- The separate Stewart controller physically tilts the table but is not yet an
  in-process arcade adapter; the arcade API currently reports tilt integration
  as disabled even while the controller is running.
- Kinect ball cells drive the automatic Floor-is-Lava survival mechanics.
- Chambers 1–6 do not yet automatically detect the finish cell; the operator
  presses `C` when the marble reaches magenta.

## Simulation and diagnostics

Run the cabinet UI and game logic without hardware:

```bash
./run_arcade.sh --simulation
```

Validate maps and Python behavior:

```bash
.venv/bin/python3 -m arcade.preflight --port 8081
.venv/bin/python3 -m unittest discover -s tests -v
```

Apply one map without the arcade server (exclusive module access required):

```bash
.venv/bin/python3 game_runner.py maps/arcade-level-1.json --once
```

## Runtime ownership and shutdown

- Only `stewart_supervisor.py` opens `/dev/arduino-stewart`.
- Only one motion client may hold the supervisor motion lease.
- Only the arcade server or one calibration/diagnostic tool may open
  `/dev/arduino-modules`.
- Stop the arcade before module calibration.
- Stop the Stewart motion client before tuning; keep the supervisor running.
- Stop the supervisor and mechanically support the table before flashing the
  Uno R4 WiFi, changing motor wiring, or cycling motor power.
- Stewart `HOLD` keeps the loaded platform energized. USB loss, controller
  reset, or power loss can release it.
- Module firmware automatically releases stalled servo channels; host code
  also pulses and releases each move.

Additional detail is available in `arcade/README.md`,
`arduino/uim5756_stewart_r4/README.md`, `maps/README.md`, and
`STEWART_PLATFORM_CONTROL_CHANGES.md`.
