# Tilt Table Marble Maze

This workspace contains the public project site, actuator-architecture explorer, mechanical calculators, hardware control tools, and firmware sketches for the tilt-table marble maze project.

## Website

The root `index.html` is a static project / portfolio landing page designed to deploy directly on Vercel with no build step.

Key pages:

- `index.html` - public project landing page
- `architecture-explorer.html` - ES-module actuator architecture explorer
- `tilt-table-simulator.html` - 3-motor tilt-table simulator
- `wall-actuator-calc.html` - 3-position wall actuator calculator
- `fbd-visualization.html` - free-body diagram visualization
- `grid-comparison.html` - legacy redirect to `architecture-explorer.html`

## Architectures

- `Vertical Actuator`: dense interior-tile architecture. Every interior tile can become open floor, an actuated blocker, or a hole. Only the border is permanently static, and the frame stays as minimal as the integrated tile package allows.
- `Horizontal Actuator`: regular lattice architecture that now uses the same public calculator model as `Vertical Actuator` (`tileSize`, border / inner-wall thickness, marble, travel factors, pinion settings, module size). The differences are that fixed floors and fixed walls reserve neighboring underfloor bay space, and the horizontal pinion can fit against the larger outer slider envelope instead of the inner cavity.

## Hardware

- `hardware/` contains the Raspberry Pi / Python control center, servo CLI, calibration data, and Arduino firmware.
- `hardware/arduino/pca9685_servo_bridge/` controls SG90 servos through a PCA9685.
- `hardware/arduino/tilt_table_leds/` contains the UNO R4 WS2811 LED test sketch.
- `hardware/README.md` documents setup and wiring.

## Serving Locally

Serve the workspace over HTTP instead of opening pages directly with `file://`, because the architecture explorer uses ES modules.

```sh
cd design
python3 -m http.server 8765
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

## Deploying to Vercel

Use the Vercel static site flow with this directory as the project root. There is no install or build command; Vercel should serve `index.html` directly.
