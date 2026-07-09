# Marble Maze: Electromechanical Tilt Maze Platform

## Product Requirements Document

**Version:** 0.1 (Draft)
**Date:** 2026-04-08

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture](#2-system-architecture)
3. [Mechanical Subsystem](#3-mechanical-subsystem)
4. [Electronics Subsystem](#4-electronics-subsystem)
5. [Sensing Subsystem](#5-sensing-subsystem)
6. [Computer Vision Subsystem](#6-computer-vision-subsystem)
7. [Firmware Architecture](#7-firmware-architecture)
8. [Software Architecture](#8-software-architecture)
9. [Game Design](#9-game-design)
10. [Player Input Methods](#10-player-input-methods)
11. [Output and Feedback](#11-output-and-feedback)
12. [Manufacturing Plan](#12-manufacturing-plan)
13. [Bill of Materials and Cost Estimate](#13-bill-of-materials-and-cost-estimate)
14. [Open Questions and Trade-off Matrix](#14-open-questions-and-trade-off-matrix)
15. [Risk Register](#15-risk-register)

---

## 1. Executive Summary

### 1.1 Product Vision

A large-format, physically interactive marble tilt maze that fuses classic wooden labyrinth gameplay with modern electromechanical traps, computer vision, and networked multiplayer. The player tilts a stepper-motor-driven platform to guide a steel marble through a lasercut maze while the maze itself fights back -- electromagnets pull the marble toward pits, walls rise and fall to block paths, and an overhead camera tracks every move.

The system supports multiple play modes: solo play with AI-controlled traps, head-to-head where one player tilts and the other triggers traps, and a reversed mode where the computer solves the maze and a human player acts as the trap master.

### 1.2 Project Goals

| ID | Goal | Priority |
|----|------|----------|
| G1 | Reliable 2-axis tilt control of a maze platform via stepper motors | Must have |
| G2 | Lasercut interchangeable maze plates with fixed and movable walls | Must have |
| G3 | SG90-driven pop-up/drop-down walls as traps | Must have |
| G4 | Electromagnets beneath the maze floor to push/pull the marble | Must have |
| G5 | Hall-effect sensors for marble detection at key waypoints | Must have |
| G6 | Timer, scoring, and LED feedback | Must have |
| G7 | At least one player input method (joystick or phone) | Must have |
| G8 | Computer vision marble tracking via overhead camera + OpenCV | Should have |
| G9 | CV-triggered trap activation based on marble position | Should have |
| G10 | AI maze solver (path planning + motor control) | Nice to have |
| G11 | Networked multiplayer (trap master vs maze runner) | Nice to have |
| G12 | Phone-based tilt control via accelerometer | Nice to have |
| G13 | Modular maze plates that can be swapped for different layouts | Nice to have |

### 1.3 Constraints

| Constraint | Value | Rationale |
|------------|-------|-----------|
| Maximum overall footprint | 48" x 48" (1219 mm x 1219 mm) | Laser cutter bed / table space |
| Wall actuation servo | SG90 micro servo | Specified; cheap, available, small |
| Marble material | Chrome steel ball bearing (ferromagnetic) | Required for electromagnets and hall sensors |
| Tilt range | +/-10 deg (operational), mechanism capable of +/-35 deg | Marble control feel; physics of rolling |
| Budget target | < $500 USD (electronics + hardware, excluding tools) | Hobbyist project |

---

## 2. System Architecture

### 2.1 High-Level Block Diagram

```
+=====================================================================+
|                         PLAYER INPUTS                               |
|  [Phone App]   [Analog Joystick]   [USB Gamepad]   [Keyboard]      |
+========+================+=================+=============+===========+
         |                |                 |             |
         v                v                 v             v
+=====================================================================+
|                    HOST COMPUTER (Raspberry Pi 4/5)                  |
|                                                                     |
|  +---------------+  +----------------+  +-------------------------+ |
|  | Web / BLE     |  | Game Engine    |  | OpenCV Vision Module    | |
|  | Server        |->| (Python)       |<-| - Marble tracking       | |
|  | (Flask /      |  | - Mode FSM     |  | - Trap zone detection   | |
|  |  FastAPI)     |  | - Scoring      |  | - Path planning (AI)    | |
|  +---------------+  | - Trap AI      |  +----------+--------------+ |
|                      | - Timer        |             |               |
|                      +-------+--------+             |               |
|                              |                 USB Camera           |
|                        UART / Serial                                |
+==============================+======================================+
                               |
+==============================+======================================+
|                   REAL-TIME CONTROLLER (ESP32-S3)                   |
|                                                                     |
|  +------------+ +------------+ +----------+ +--------+ +--------+  |
|  | Stepper    | | Servo PWM  | | Electro- | | Hall   | | IMU    |  |
|  | Control    | | (PCA9685)  | | magnet   | | Sensor | | (opt.) |  |
|  | (TMC2209)  | |            | | Drivers  | | Reader | |        |  |
|  +-----+------+ +-----+------+ +----+-----+ +---+----+ +---+----+  |
|        |              |              |           |           |       |
+========+==============+==============+===========+===========+=======+
         |              |              |           |           |
    +----+----+   +-----+-----+  +----+----+ +---+---+  +----+----+
    | NEMA 17 |   | SG90 x N  |  | Electro-| | Hall  |  | BNO055  |
    | x 2     |   | Servos    |  | magnets | | Effect|  | 9-DOF   |
    +---------+   +-----------+  | x N     | | x N   |  +---------+
                                 +---------+ +-------+
         |              |              |           |
+========+==============+==============+===========+==============+
|                    PHYSICAL MAZE PLATFORM                        |
|  Lasercut maze plate on SPM/gimbal tilt mechanism                |
|  Steel marble, LED strips, wiring harness                        |
+==================================================================+
```

### 2.2 Communication Flow

| Link | Protocol | Baud / Speed | Payload |
|------|----------|-------------|---------|
| Pi <-> ESP32 | UART (serial) | 921600 baud | Binary packet protocol (section 7) |
| Pi <-> Phone | WebSocket over WiFi | N/A | JSON commands + telemetry |
| Pi <-> Camera | USB 2.0 | 30 fps @ 1280x720 | MJPEG or raw YUV |
| ESP32 <-> TMC2209 | UART (per driver) | 115200 baud | TMC register read/write |
| ESP32 <-> PCA9685 | I2C | 400 kHz | Servo position commands |
| ESP32 <-> Sensors | GPIO / ADC / I2C | Polling @ 100 Hz | Digital/analog reads |

### 2.3 Power Distribution

```
[12V 10A DC Supply] ──┬── 12V rail ──> Stepper drivers (2x TMC2209)
                       |              ──> Electromagnets (via MOSFET board)
                       |
                       ├── [Buck 12V->5V 5A] ──> 5V rail
                       |      ├──> Raspberry Pi (USB-C, 5V 3A)
                       |      ├──> ESP32 (via regulator or USB)
                       |      ├──> PCA9685 servo driver V+ (SG90 bank)
                       |      └──> WS2812B LED strips
                       |
                       └── [Buck 12V->3.3V] ──> 3.3V rail (sensors, logic)
```

Total power budget: ~85W peak, ~40W typical.

---

## 3. Mechanical Subsystem

### 3.1 Platform Tilt Mechanism

Two candidate architectures exist. The design phase will select one based on prototyping.

#### Option A: Spherical Parallel Manipulator (SPM)

An existing parametric OpenSCAD design (`spm_90deg_final.scad`) implements a 2-DOF SPM with:

| Parameter | Current Value | Scaled Value (proposed) |
|-----------|--------------|------------------------|
| Inner sphere radius (R) | 50 mm | 120 mm |
| Outer sphere radius (R_out) | 65 mm | 155 mm |
| Crank arc angle | 90 deg | 90 deg |
| Coupler arc angle | 90 deg | 90 deg |
| Max tilt | +/-35 deg | +/-15 deg (software-limited to +/-10 deg) |
| Motor | NEMA 17 | NEMA 17 (or NEMA 23 if torque insufficient) |
| Output | 8 mm rod, split-clamp hub | 12 mm rod or plate flange |
| Joint hardware | M3 clevis/tang, 3D printed | M5 clevis/tang, 3D printed or machined |

Advantages:
- Compact: both motors mount to a fixed base ring, no moving motor mass.
- No gimbal lock within operational range.
- Mechanically stiff in both axes simultaneously.
- Existing parametric CAD can be scaled by changing R, R_out.

Disadvantages:
- More complex kinematics (inverse kinematics required in firmware).
- Curved arc links are harder to manufacture at scale (3D print or CNC).
- Joint clearances accumulate -- backlash may need anti-backlash spring preload.

#### Option B: Series Rotary Axes (Gimbal)

A two-axis gimbal with concentric frames:

```
            [Outer Frame - fixed to base]
                     |
              Bearing + Belt/Gear
                     |
            [Inner Frame - rotates about X]
                     |
              Bearing + Belt/Gear
                     |
            [Maze Plate - rotates about Y relative to inner frame]
```

| Parameter | Value |
|-----------|-------|
| Outer axis | Pitch (X rotation), NEMA 17 belt-driven 3:1 |
| Inner axis | Roll (Y rotation), NEMA 17 belt-driven 3:1 |
| Bearings | 608ZZ (8mm ID) or 6001ZZ (12mm ID) |
| Range | +/-20 deg mechanical, +/-10 deg operational |
| Frame material | Lasercut 6mm plywood or 6mm acrylic |

Advantages:
- Simple kinematics: each motor directly controls one axis.
- Easy to build at any scale with lasercut frames and bearings.
- Motors can be larger without impacting mechanism complexity.
- Belt reduction provides torque multiplication and backlash-free drive.

Disadvantages:
- Inner-axis motor rides on outer frame (moving mass).
- At extreme angles, cross-coupling requires software compensation.
- Bulkier footprint for the same tilt range.

#### Torque Analysis (applies to both)

Worst-case load: a 24" x 24" (610 mm x 610 mm) maze plate weighing ~3 kg, center of mass offset by sin(10 deg) * 150 mm = 26 mm from the tilt axis.

- Gravity torque: 3 kg * 9.81 m/s^2 * 0.026 m = 0.76 N-m
- Dynamic torque (target 90 deg/s^2 accel): I * alpha, where I ~ 0.04 kg-m^2 -> 0.04 * 1.57 = 0.063 N-m
- Total: ~0.83 N-m

NEMA 17 (42 mm, 1.5A) typical holding torque: 0.4-0.5 N-m. With a 3:1 belt reduction (gimbal) or the SPM's inherent mechanical advantage, this is feasible. A NEMA 23 fallback provides 1.0-1.9 N-m if needed.

### 3.2 Maze Structure

#### Layered Construction

The maze is built as a stack of lasercut layers fastened together with M3 standoffs and screws.

```
Layer 4 (top):    Guide rails / marble containment rim     3 mm plywood
Layer 3:          Wall layer (fixed walls, slots for moving walls)  6 mm plywood
Layer 2:          Floor layer (marble rolls here, holes for pits)   3 mm acrylic (translucent for underglow)
Layer 1 (bottom): Services layer (servo mounts, magnet pockets, wiring channels)  6 mm plywood
```

| Parameter | Value |
|-----------|-------|
| Maze playing area | 24" x 24" (610 x 610 mm) nominal |
| Maze outer frame | 26" x 26" (660 x 660 mm) including rim |
| Wall height | 12 mm above floor (sufficient for 16 mm marble with 4 mm clearance over top is NOT desired -- walls should fully contain the marble. 12 mm walls plus 3 mm floor = 15 mm channel depth, marble 16 mm diameter means 1 mm protrusion above wall. Increase to 15 mm wall height for full containment.) |
| Wall thickness | 3 mm (lasercut kerf ~0.2 mm) |
| Path width | 22 mm minimum (16 mm marble + 3 mm clearance each side) |
| Pit hole diameter | 20 mm (marble falls through) |
| Material | 3 mm and 6 mm Baltic birch plywood; 3 mm clear or translucent acrylic for floor |
| Marble | 16 mm (5/8") chrome steel ball bearing, ~16.3 g |

#### Interchangeable Maze Plates

The maze stack (layers 1-4) attaches to the tilt mechanism output via a quick-release system:

- Four quarter-turn fasteners (Dzus-style) at the corners of the services layer.
- A 4-pin Molex Mini-Fit Jr connector for power/signal passthrough.
- Keyed alignment pins (2x 6 mm dowels) for repeatable positioning.

This allows different maze layouts to be swapped in seconds. Each maze plate carries its own servos, magnets, and hall sensors pre-wired to the connector.

### 3.3 Trap Mechanisms

#### 3.3.1 Moving Walls (SG90 Servo Actuated)

Each moving wall segment is a 3 mm plywood blade that slides vertically through a slot in the wall layer. An SG90 servo mounted in the services layer drives the blade via a crank-slider linkage.

```
              Wall blade (3 mm plywood)
                |
                |  slot in wall layer
  ==============|================  <- wall layer (6 mm)
                |
                |  slot in floor
  =============-+================  <- floor layer (3 mm)
                |
        [crank arm link]
                |
          [SG90 horn]
                |
           [SG90 servo body, mounted to services layer]
```

| Parameter | Value |
|-----------|-------|
| Servo | SG90 micro servo (22.2 x 11.8 x 31 mm) |
| Torque | 1.8 kg-cm @ 4.8V |
| Travel | 15 mm vertical (wall fully up = path blocked, fully down = path open) |
| Linkage | Simple crank-slider: 10 mm crank arm on servo horn, 20 mm connecting rod to wall blade pin |
| Wall blade size | 25 mm wide x 20 mm tall x 3 mm thick |
| Actuation time | ~0.15 s (SG90 speed: 0.12 s/60 deg @ 4.8V; ~45 deg throw) |
| Count per maze | 10-20 (budget for 16) |

#### 3.3.2 Electromagnets

Small DC electromagnets mounted in pockets in the services layer, directly beneath the floor. They attract the ferromagnetic marble toward trap zones (pits, dead ends) or resist the marble's motion through a passage.

| Parameter | Value |
|-----------|-------|
| Type | 12V DC holding electromagnet, 20 mm diameter x 15 mm tall |
| Holding force | ~2.5 N at contact, ~0.3 N at 5 mm air gap (through 3 mm acrylic floor) |
| Control | Logic-level N-channel MOSFET per magnet, PWM capable (1 kHz) |
| Flyback diode | 1N4007 across each coil |
| Current draw | ~250 mA each at 12V |
| Count per maze | 8-12 (budget for 10) |
| Placement | Under floor at pit edges, dead-end walls, narrow passages, and intersection decision points |

PWM duty cycle control allows variable pull strength: a gentle tug that the player can resist, or a hard snap that requires quick reaction.

#### 3.3.3 Pit Traps (Passive)

Holes in the floor layer (20 mm dia) through which the marble falls. A ramp or funnel beneath routes the marble to a return chute that delivers it back to the start zone. The return chute is gravity-fed and fixed to the base frame (does not tilt with the platform).

A hall sensor at the bottom of each pit detects the marble drop, triggering a "life lost" event.

### 3.4 Frame and Enclosure

The overall frame is a table-like structure that supports the tilt mechanism, houses the electronics, and provides a stable base.

```
+--------------------------------------------------+
|             Camera mast (adjustable height)       |
|                      |                            |
|   +------------------+------------------+         |
|   |          Maze platform              |         |
|   |     (tilts on SPM or gimbal)        |         |
|   +------------------+------------------+         |
|                      |                            |
|          Tilt mechanism (SPM / gimbal)            |
|                      |                            |
|   +------------------------------------------+   |
|   |            Base platform                  |   |
|   |  [Pi] [ESP32] [PSU] [Driver boards]       |   |
|   +------------------------------------------+   |
|         |              |              |           |
|       [Leg]          [Leg]          [Leg]         |
+--------------------------------------------------+
```

| Parameter | Value |
|-----------|-------|
| Overall footprint | 36" x 36" nominal, 48" x 48" max with camera mast |
| Base platform | 30" x 30" lasercut 6 mm plywood |
| Height (table surface to maze) | 8-12" (200-300 mm) |
| Camera mast height | 30-40" above maze (for FOV coverage) |
| Material | 6 mm Baltic birch plywood (lasercut), 2020 aluminum extrusion for legs and camera mast |
| Joinery | T-slot nuts for extrusion; tab-and-slot with wood glue for plywood panels |

### 3.5 Cable Management

The maze platform tilts +/-10 degrees. All wiring between the fixed base and the tilting maze must accommodate this motion.

- **Service loop**: A coiled cable bundle (similar to a robot arm cable carrier) runs from the base up through the center of the tilt mechanism to the maze's Molex connector.
- **Wire count**: approximately 40 conductors (16 servo signal + power/ground, 10 magnet drive, 10 hall sensor, 4 LED data, 2 IMU I2C).
- **Ribbon cable**: A 40-pin IDC ribbon cable with enough slack (150 mm service loop) handles the +/-10 deg motion without fatigue. Strain relief at both ends.

---

## 4. Electronics Subsystem

### 4.1 Controller Selection

#### Host Computer: Raspberry Pi 4 Model B (4 GB)

| Role | Detail |
|------|--------|
| OpenCV processing | 720p @ 30 fps marble tracking |
| Game engine | Python process managing modes, scoring, trap AI |
| Web server | Flask/FastAPI serving phone control UI over WiFi |
| Camera interface | USB 2.0 UVC camera |
| Serial link to ESP32 | UART via GPIO or USB-serial |

A Pi 5 can be substituted for better CV throughput if needed.

#### Real-Time Controller: ESP32-S3-DevKitC-1

| Role | Detail |
|------|--------|
| Stepper control | Step/dir generation at up to 100 kHz per axis |
| Servo PWM | I2C commands to PCA9685 |
| Electromagnet PWM | GPIO + MOSFET, hardware timer PWM |
| Sensor polling | Hall sensors (digital GPIO), IMU (I2C) |
| Communication | UART to Pi at 921600 baud |

The ESP32-S3 is chosen for its dual-core 240 MHz processor, ample GPIO (>30 pins), built-in WiFi/BLE (fallback comms), hardware timers for step generation, and broad library support (Arduino / ESP-IDF).

### 4.2 Motor Drivers

#### Stepper Drivers: TMC2209 (x2)

| Parameter | Value |
|-----------|-------|
| Interface | STEP/DIR + UART for configuration |
| Current rating | 2.0A RMS (peak 2.8A) |
| Microstepping | Up to 256, default 16 |
| StallGuard | Sensorless homing / stall detection |
| SpreadCycle / StealthChop | Silent operation in StealthChop mode |
| Supply | 4.75-29V (12V nominal) |

UART configuration allows runtime tuning of current, microstepping, and stall threshold without hardware changes. StallGuard provides sensorless homing: on power-up, both axes drive slowly until stall is detected at mechanical endstops, establishing a known zero position.

#### Servo Driver: PCA9685 (x1)

| Parameter | Value |
|-----------|-------|
| Channels | 16 PWM outputs |
| Interface | I2C (address 0x40, chainable) |
| Resolution | 12-bit (4096 steps) |
| Frequency | 50 Hz (standard servo) |
| Supply (V+) | 5V from buck converter (separate from logic) |

16 channels cover up to 16 SG90 servos. A second PCA9685 (address 0x41) can be added if more channels are needed.

### 4.3 Electromagnet Driver

A custom or off-the-shelf MOSFET driver board controls the electromagnets.

| Parameter | Value |
|-----------|-------|
| MOSFET | IRLZ44N (logic-level, 47A, 55V) or equivalent |
| Gate drive | Direct from ESP32 GPIO (3.3V logic-level gate) |
| Flyback protection | 1N4007 reverse-biased across each coil |
| PWM frequency | 1 kHz (sufficient for force modulation, low audible whine) |
| Channels | 10 (one per electromagnet) |
| Board | Either a custom PCB with 10x MOSFET + diode, or 2x off-the-shelf 8-channel MOSFET breakout boards |

### 4.4 Sensor Interfaces

| Sensor | Interface | ESP32 Pin(s) | Notes |
|--------|-----------|-------------|-------|
| Hall effect (SS49E analog) x10 | ADC | GPIO 1-10 (ADC1) | Analog output, threshold in software |
| Hall effect (A3144 digital) alt. | Digital GPIO | GPIO 1-10 | Open-collector, needs 10k pull-up |
| IMU (BNO055 or ICM-20948) | I2C | SDA=GPIO 21, SCL=GPIO 22 | Optional; provides absolute orientation |
| Endstop switches x2 | Digital GPIO | GPIO 11-12 | For stepper homing fallback (if not using StallGuard) |

For hall-effect marble detection, each sensor has a small bias magnet (3 mm dia x 2 mm NdFeB disc) mounted behind it. When the steel marble passes within ~10 mm, it concentrates the field through the hall element, producing a detectable voltage swing. The SS49E (analog) variant is preferred because it allows threshold tuning in software and can estimate proximity, not just presence.

### 4.5 PCB / Wiring Strategy

Phase 1 (prototyping):
- ESP32-S3 dev board on a breadboard or perfboard.
- PCA9685 breakout board.
- TMC2209 SilentStepStick modules on a carrier board.
- MOSFET driver on perfboard or breakout.
- Point-to-point wiring with JST-XH connectors for all sensor/actuator connections.

Phase 2 (integration):
- Custom carrier PCB ("Maze Controller Board") that hosts:
  - ESP32-S3 module (WROOM) soldered directly.
  - 2x TMC2209 in STEP/DIR + UART configuration.
  - PCA9685 with 16x 3-pin servo headers.
  - 10x MOSFET + flyback for electromagnets, with 2-pin screw terminals.
  - 10x JST-XH 3-pin headers for hall sensors (VCC, GND, SIG).
  - I2C header for IMU.
  - UART header for Pi connection.
  - Power input (barrel jack 12V), onboard 5V and 3.3V regulators.
- 2-layer PCB, 100 x 150 mm, designed in KiCad.

### 4.6 Power Supply

| Rail | Voltage | Source | Max Current | Loads |
|------|---------|--------|-------------|-------|
| Main | 12V | AC-DC PSU (Mean Well LRS-150-12) | 10A | Steppers, electromagnets, input to buck |
| Servo/Logic | 5V | Buck converter (LM2596 or Pololu D24V50F5) | 5A | SG90 servos, Pi, ESP32, LEDs |
| Sensor | 3.3V | LDO on ESP32 dev board | 0.5A | Hall sensors, IMU |

Peak load estimate:
- 2x NEMA 17 @ 1.5A = 3A @ 12V = 36W
- 10x electromagnets @ 250 mA = 2.5A @ 12V = 30W (all on simultaneously, worst case)
- 16x SG90 stall current ~0.7A each, but simultaneous actuation limited to 4 = 2.8A @ 5V = 14W
- Pi 4 = 3A @ 5V = 15W
- ESP32 + sensors = 0.5A @ 5V = 2.5W
- LEDs (60 LED strip) = 1A @ 5V = 5W
- **Total peak: ~103W, typical: ~45W**

The Mean Well LRS-150-12 (150W) provides sufficient headroom.

---

## 5. Sensing Subsystem

### 5.1 Hall-Effect Sensors

#### Purpose
Detect the marble's presence at discrete waypoints: start zone, finish zone, checkpoints, pit bottoms, and trap trigger zones.

#### Sensor Selection: SS49E (Linear Hall Effect)

| Parameter | Value |
|-----------|-------|
| Output | Analog, 0.2-4.8V (quiescent ~2.5V) |
| Sensitivity | 1.4 mV/G typical |
| Supply | 4.5-6V |
| Package | TO-92 |
| Detection range | ~5-15 mm with 3 mm NdFeB bias magnet |

#### Mounting

Each sensor is mounted on the underside of the floor layer, directly beneath a waypoint. A 3 mm NdFeB disc magnet is glued to the sensor face (or adjacent). When the steel marble rolls over the sensor, the ferromagnetic ball concentrates the field lines, causing a measurable voltage change (~200-500 mV swing depending on air gap).

Software reads the ESP32's 12-bit ADC (0-4095 counts), applies a per-sensor calibrated threshold, and debounces (20 ms window) to generate a binary "marble present" event.

#### Sensor Map (per maze plate)

| Location | Count | Purpose |
|----------|-------|---------|
| Start zone | 1 | Detect game start / marble placement |
| Finish zone | 1 | Detect game completion |
| Checkpoints | 4-6 | Progress tracking, scoring milestones |
| Pit bottoms | 4-6 | Detect marble fall (life lost) |
| Trap trigger zones | 4-6 | CV fallback; activate trap when marble is nearby |
| **Total** | **14-20** | Budget for 16 on ADC1 channels + MUX |

If more than 10 analog channels are needed, a CD74HC4067 16-channel analog multiplexer expands one ADC pin to 16 inputs.

### 5.2 Inertial Measurement Unit (Optional)

#### Purpose
Measure actual platform pitch and roll for closed-loop tilt control. This compensates for stepper missed steps, linkage compliance, and external disturbances.

#### Sensor Selection: BNO055 (9-DOF Absolute Orientation)

| Parameter | Value |
|-----------|-------|
| Axes | 3-axis accelerometer, gyroscope, magnetometer |
| Fusion output | Quaternion or Euler angles at 100 Hz |
| Interface | I2C |
| Accuracy | +/-1 deg (static), +/-2 deg (dynamic) |
| Mounting | Bolted to the maze services layer (moves with platform) |

The BNO055's onboard sensor fusion runs at 100 Hz and outputs absolute orientation. The firmware compares the IMU-reported pitch/roll to the commanded pitch/roll and applies a PI correction term to the stepper target positions.

If the IMU is omitted, the system operates open-loop: stepper position (in microsteps) is the sole tilt reference. Open-loop is acceptable if the mechanism has low backlash and no missed steps (StealthChop + moderate speeds).

### 5.3 Endstops (Homing)

Two approaches, not mutually exclusive:

1. **StallGuard sensorless homing**: TMC2209 detects motor stall when the mechanism hits a hard stop. No additional hardware.
2. **Mechanical microswitches**: Two normally-open lever switches mounted at the gimbal/SPM hard stops. Simple, reliable fallback.

Both can be implemented. StallGuard is used as the primary method; microswitches are wired as a safety backup.

---

## 6. Computer Vision Subsystem

### 6.1 Purpose

Real-time marble tracking enables:
- Position-triggered trap activation (the maze "knows" where the marble is without hall sensors at every point).
- AI maze solving (path planning requires knowing the marble's current position and velocity).
- Live display overlay (marble position, predicted path, trap zones visualized on a screen).
- Post-game replay and analytics.

### 6.2 Camera

| Parameter | Value |
|-----------|-------|
| Type | USB 2.0 UVC webcam (e.g., Logitech C920 or Arducam OV9281 global shutter) |
| Resolution | 1280 x 720 (720p) |
| Frame rate | 30 fps minimum, 60 fps preferred |
| Lens | Fixed focus, wide-angle (90-120 deg FOV) |
| Mounting | Camera mast, 30-40" above maze center, aimed straight down |
| Shutter | Global shutter preferred (eliminates rolling shutter distortion during platform tilt) |

#### Mounting Options

| Option | Pros | Cons |
|--------|------|------|
| **Fixed mast (on base frame)** | Stationary reference frame; no moving cables | Maze tilts relative to camera; perspective distortion changes with tilt |
| **Maze-mounted** | Camera moves with maze; image is always top-down | Adds weight/inertia to tilting platform; cable fatigue |

**Recommendation:** Fixed mast. The +/-10 deg tilt causes only minor perspective change, correctable with a homography transform updated from the platform's known tilt angle.

### 6.3 OpenCV Pipeline

```
Frame Capture (720p @ 30fps)
       |
       v
Undistort (camera calibration matrix, pre-computed)
       |
       v
Perspective Warp (homography from 4 maze corner ArUco markers, updated per-frame or per-tilt-change)
       |
       v
ROI Crop (maze playing area only)
       |
       v
Background Subtraction (MOG2 or static background diff)
       |
       v
Morphological Cleanup (erode + dilate to remove noise)
       |
       v
Contour Detection -> Circle Fitting (Hough circles or minEnclosingCircle on largest contour)
       |
       v
Marble Position (x, y) in maze-coordinate mm
       |
       v
Kalman Filter (smooth position, estimate velocity)
       |
       v
[Output: marble_pos, marble_vel] --> Game Engine
```

#### Calibration

1. **Camera intrinsics**: Captured once using a checkerboard pattern. Stored as a YAML file.
2. **Camera-to-maze homography**: Four ArUco markers (4x4_50 dictionary, IDs 0-3) are printed on the maze corners. OpenCV detects them each frame and computes a perspective transform that maps the image to a top-down maze-coordinate view. This auto-corrects for tilt.
3. **Maze map registration**: The maze DXF/SVG is loaded as a reference. Trap zones, pit positions, and wall positions are defined in maze coordinates and do not need re-calibration when the camera moves slightly.

#### Performance Targets

| Metric | Target |
|--------|--------|
| Latency (frame capture to position output) | < 30 ms |
| Position accuracy | +/- 3 mm |
| Tracking robustness | No lost tracking for > 95% of frames under normal lighting |
| False positive rate | < 1% (phantom marble detections) |

### 6.4 Trap Zone Triggering

The game engine maintains a list of trap zones, each defined as a circle (center_x, center_y, radius) in maze coordinates. Each frame, the marble position is checked against all active trap zones:

```python
for trap in active_traps:
    dist = sqrt((marble_x - trap.x)**2 + (marble_y - trap.y)**2)
    if dist < trap.radius and not trap.cooldown:
        trap.activate()
        trap.cooldown = TRAP_COOLDOWN_MS
```

Trap zones can have configurable trigger behaviors:
- **Instant**: fires the moment the marble enters the zone.
- **Dwell**: fires only if the marble stays in the zone for N milliseconds.
- **Proximity**: activates proportionally (e.g., electromagnet PWM scales with distance).
- **Predictive**: uses Kalman-filtered velocity to fire slightly before the marble arrives (compensating for actuation latency).

### 6.5 AI Maze Solver

For the mode where the computer controls the tilt and solves the maze:

1. **Maze graph extraction**: From the maze DXF/SVG, extract a graph of nodes (intersections, dead ends) and edges (paths). Edges are weighted by length and danger (proximity to pits, traps).
2. **Path planning**: A* on the maze graph, with danger-weighted cost. The path is a sequence of waypoints in maze coordinates.
3. **Trajectory planning**: Convert waypoints to a smooth trajectory with velocity limits (marble must not overshoot at turns).
4. **Tilt controller**: A PID loop that adjusts platform pitch/roll to steer the marble along the trajectory. The CV system provides the marble position feedback.
5. **Replanning**: If the marble deviates (e.g., due to an opponent's trap), the path is replanned from the current position.

This is a "nice to have" (G10) and is developed after the core system is functional.

---

## 7. Firmware Architecture

### 7.1 Overview

The ESP32-S3 firmware runs on ESP-IDF (or Arduino framework with FreeRTOS). Two cores are used:

| Core | Task | Priority |
|------|------|----------|
| Core 0 | Communication (UART parsing, command dispatch) | Medium |
| Core 0 | Sensor polling (hall sensors, IMU) | Medium |
| Core 1 | Stepper motion control (step pulse generation) | Highest (ISR-driven) |
| Core 1 | Servo/magnet actuation (I2C + PWM updates) | High |

### 7.2 Serial Protocol

Binary packet protocol over UART between Pi and ESP32.

#### Packet Structure

```
[START: 0xAA] [LEN: 1 byte] [CMD: 1 byte] [PAYLOAD: 0-252 bytes] [CRC8: 1 byte]
```

| Field | Size | Description |
|-------|------|-------------|
| START | 1 | Magic byte 0xAA |
| LEN | 1 | Payload length (0-252) |
| CMD | 1 | Command ID (see table) |
| PAYLOAD | 0-252 | Command-specific data |
| CRC8 | 1 | CRC-8/MAXIM over LEN+CMD+PAYLOAD |

#### Command Table

| CMD | Name | Direction | Payload | Description |
|-----|------|-----------|---------|-------------|
| 0x01 | SET_TILT | Pi -> ESP | pitch_deg(f16), roll_deg(f16) | Set target tilt angles |
| 0x02 | SET_SERVO | Pi -> ESP | servo_id(u8), position_us(u16) | Set single servo position |
| 0x03 | SET_SERVO_BATCH | Pi -> ESP | count(u8), [id(u8), pos(u16)] x N | Set multiple servos |
| 0x04 | SET_MAGNET | Pi -> ESP | magnet_id(u8), pwm_duty(u8) | Set electromagnet PWM (0-255) |
| 0x05 | SET_MAGNET_BATCH | Pi -> ESP | count(u8), [id(u8), duty(u8)] x N | Set multiple magnets |
| 0x06 | SET_LED | Pi -> ESP | led_mode(u8), color(RGB) | Set LED strip mode/color |
| 0x07 | HOME | Pi -> ESP | (none) | Home both stepper axes |
| 0x08 | E_STOP | Pi -> ESP | (none) | Emergency stop all actuators |
| 0x10 | STATUS | ESP -> Pi | See below | Periodic status telemetry |
| 0x11 | HALL_EVENT | ESP -> Pi | sensor_id(u8), value(u16) | Hall sensor threshold crossing |
| 0x12 | HOME_DONE | ESP -> Pi | axis(u8), result(u8) | Homing complete notification |
| 0x13 | IMU_DATA | ESP -> Pi | pitch(f16), roll(f16), yaw(f16) | IMU orientation (if enabled) |

#### STATUS Packet (0x10), sent every 50 ms

| Offset | Size | Field |
|--------|------|-------|
| 0 | 2 | Stepper A position (int16, 0.01 deg units) |
| 2 | 2 | Stepper B position (int16, 0.01 deg units) |
| 4 | 2 | IMU pitch (int16, 0.01 deg) or 0x7FFF if disabled |
| 6 | 2 | IMU roll (int16, 0.01 deg) or 0x7FFF if disabled |
| 8 | 2 | Hall sensor bitmask (16 bits, 1=marble present) |
| 10 | 1 | Error flags (bit 0: stepper A stall, bit 1: stepper B stall, bit 2: overtemp) |

### 7.3 Stepper Motion Control

The firmware implements a trapezoidal velocity profile for each stepper axis:

| Parameter | Value |
|-----------|-------|
| Max velocity | 500 deg/s (maze tilt) |
| Max acceleration | 2000 deg/s^2 |
| Microstepping | 16 (3200 steps/rev for 1.8 deg motor) |
| Step pulse timing | Hardware timer ISR, 1 us pulse width |

Position commands from the Pi arrive as target angles. The firmware plans a trapezoidal move from the current position and generates step pulses via a hardware timer ISR on core 1. For the SPM, the firmware also computes the inverse kinematics (motor angles from desired pitch/roll) using the equations from the OpenSCAD model, ported to C.

#### Closed-Loop Option

If the IMU is enabled, a PI controller runs at 100 Hz:

```
error_pitch = target_pitch - imu_pitch
error_roll  = target_roll  - imu_roll
correction_pitch = Kp * error_pitch + Ki * integral_pitch
correction_roll  = Kp * error_roll  + Ki * integral_roll
stepper_target_A = nominal_A + correction_pitch  (or IK function)
stepper_target_B = nominal_B + correction_roll   (or IK function)
```

### 7.4 Servo Control

On receiving SET_SERVO or SET_SERVO_BATCH, the firmware writes the requested pulse width (500-2400 us) to the PCA9685 via I2C. A rate limiter (optional) can smooth transitions to avoid jarring wall movements, or the servo can slam for dramatic trap activation.

### 7.5 Electromagnet Control

Each magnet is driven by a hardware PWM channel (LEDC peripheral on ESP32). On receiving SET_MAGNET, the firmware sets the duty cycle (0-255 mapped to 0-100%). A safety watchdog disables all magnets if no command is received for 2 seconds (prevents overheating if the Pi crashes).

### 7.6 Safety

| Hazard | Mitigation |
|--------|------------|
| Stepper runaway | Software endstops at +/-15 deg; StallGuard hard stop detection |
| Electromagnet overheat | 2-second watchdog timeout; max duty cycle limit (80%); thermal pad on magnets |
| Servo stall | PCA9685 OE (output enable) pin pulled low by ESP32 on E_STOP |
| Communication loss | If no Pi heartbeat for 1 second, ESP32 centers platform and disables all traps |
| Power surge | 10A fuse on 12V input; polyfuses on 5V servo rail |

---

## 8. Software Architecture

### 8.1 Overview

The Raspberry Pi runs a Python application with the following modules:

```
marble_maze/
  __init__.py
  main.py                 # Entry point, initializes all modules
  config.py               # YAML-based configuration loader
  serial_bridge.py        # UART communication with ESP32
  game_engine.py          # Game state machine, scoring, timer
  vision/
    __init__.py
    camera.py             # Camera capture, calibration
    tracker.py            # Marble detection and Kalman filter
    maze_map.py           # Maze geometry and trap zone definitions
  input/
    __init__.py
    joystick.py           # Physical joystick via ADC or USB HID
    web_input.py          # Phone/browser WebSocket input
    gamepad.py            # USB gamepad input (pygame)
  output/
    __init__.py
    led_controller.py     # LED mode commands to ESP32
    display.py            # Score/timer display (web UI or HDMI)
    sound.py              # Sound effects (pygame.mixer)
  ai/
    __init__.py
    trap_ai.py            # AI trap triggering logic
    solver.py             # AI maze solver (pathfinding + tilt controller)
  web/
    __init__.py
    server.py             # FastAPI web server
    static/               # Phone control UI (HTML/JS)
    templates/
```

### 8.2 Game Engine State Machine

```
                   +-----------+
          power-on |           |
       +---------->|   IDLE    |<---------------------+
       |           |           |                      |
       |           +-----+-----+                      |
       |                 | start_game()                |
       |                 v                             |
       |           +-----------+                       |
       |           |  HOMING   |  (center platform,    |
       |           |           |   home steppers)       |
       |           +-----+-----+                       |
       |                 | home_complete                |
       |                 v                             |
       |           +-----------+                       |
       |           |  READY    |  (waiting for marble  |
       |           |           |   on start sensor)     |
       |           +-----+-----+                       |
       |                 | marble_detected(start)       |
       |                 v                             |
       |           +-----------+                       |
       |   +------>|  PLAYING  |  (timer running,      |
       |   |       |           |   traps active)        |
       |   |       +--+--+--+--+                       |
       |   |          |  |  |                          |
       |   |  marble  |  |  | marble                   |
       |   |  in_pit  |  |  | at_finish                |
       |   |          v  |  v                          |
       |   |  +-------+  |  +---------+                |
       |   |  | LIFE  |  |  | VICTORY |                |
       |   |  | LOST  |  |  |         |                |
       |   |  +---+---+  |  +----+----+                |
       |   |      |       |       |                    |
       |   | lives > 0   |  show_score()               |
       |   +------+       |       |                    |
       |                  |       +--------------------+
       |          timeout |
       |                  v
       |           +-----------+
       |           | GAME OVER |
       |           |           |
       |           +-----+-----+
       |                 |
       +--- reset() -----+
```

### 8.3 Trap AI Module

When the computer controls traps (modes: CV Solo, Player vs AI), the trap AI decides when and which traps to fire.

#### Difficulty Levels

| Level | Behavior |
|-------|----------|
| Easy | Traps fire only when marble dwells in a zone for > 2 seconds; magnets at 30% PWM |
| Medium | Traps fire on zone entry with 500 ms delay; magnets at 60% PWM |
| Hard | Predictive firing (using marble velocity); magnets at 90% PWM; walls close escape routes |
| Nightmare | All traps coordinate to herd the marble into a pit; walls form a moving blockade |

#### Trap Selection Heuristic (Hard / Nightmare)

```python
def select_trap(marble_pos, marble_vel, traps, maze_graph):
    # 1. Predict marble position 500ms in the future
    predicted = marble_pos + marble_vel * 0.5

    # 2. Find the nearest pit to the predicted position
    nearest_pit = min(pits, key=lambda p: distance(predicted, p))

    # 3. Activate magnets that pull marble toward the pit
    for magnet in magnets:
        if pulls_toward(magnet.pos, marble_pos, nearest_pit):
            magnet.set_duty(scale_by_distance(marble_pos, magnet.pos))

    # 4. Close walls that block the marble's escape from the pit
    escape_paths = maze_graph.paths_away_from(nearest_pit, marble_pos)
    for wall in walls:
        if wall.blocks(escape_paths):
            wall.close()
```

### 8.4 Web Server and Phone Control

A FastAPI server runs on the Pi, serving a mobile-friendly single-page app.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Phone control UI (virtual joystick + trap buttons) |
| `/ws/control` | WebSocket | Bidirectional: player sends tilt/trap commands, server sends telemetry |
| `/ws/spectate` | WebSocket | Read-only telemetry stream (score, marble position, trap states) |
| `/api/game` | POST | Start/stop/reset game, set mode and difficulty |
| `/api/config` | GET/PUT | Read/write system configuration |
| `/api/maze` | GET | Get current maze layout as SVG |

The phone UI uses the device's accelerometer (DeviceOrientationEvent API) to map phone tilt to maze tilt commands. A virtual joystick (nipple.js) serves as a fallback for devices without accelerometer access.

---

## 9. Game Design

### 9.1 Game Modes

#### Mode 1: Classic Solo

- Player controls tilt (joystick or phone).
- Traps activate on a fixed timer (random trap every N seconds) or are CV-triggered.
- Difficulty setting controls trap frequency and intensity.
- Objective: reach finish before time runs out.
- Lives: 3 (lost by falling in a pit).

#### Mode 2: Player vs AI (Trap Master)

- Player controls tilt.
- AI controls all traps using CV to track the marble.
- AI difficulty ranges from "telegraphed, slow" to "predictive, coordinated."
- Objective: reach finish despite AI opposition.
- Scoring: time-based with bonus for remaining lives.

#### Mode 3: AI vs Player (Maze Runner)

- AI controls tilt (using CV + path planning to solve the maze).
- Player controls traps manually (via phone app or dedicated trap panel).
- Player earns points for each life the AI loses; AI earns points for reaching the finish.
- Trap cooldowns prevent spam (each trap has a 3-second cooldown after firing).

#### Mode 4: Two-Player Versus

- Player 1 controls tilt (joystick).
- Player 2 controls traps (phone app or second joystick mapped to trap panel).
- Alternating rounds; players swap roles.
- Scoring: combined across both rounds.

#### Mode 5: Speed Run

- No traps active.
- Pure tilt skill challenge.
- Leaderboard (stored locally, displayed on web UI).

#### Mode 6: Puzzle Mode

- Certain paths are blocked by default (walls up).
- Player must activate walls in the correct sequence to create a viable path.
- Some walls are linked (raising one lowers another).
- Marble physics still apply -- player tilts to navigate the opened path.
- Configurable puzzle definitions per maze plate (stored as JSON).

### 9.2 Scoring System

| Event | Points |
|-------|--------|
| Reach checkpoint | +100 per checkpoint (first visit only) |
| Reach finish | +500 |
| Time bonus | +(max_time - elapsed_time) * 2 |
| Life lost (pit) | -200 |
| Trap dodged (entered zone and exited without falling) | +50 |
| Speed bonus (< 50% of par time) | +300 |

High scores stored in a local SQLite database. Web UI shows leaderboard.

### 9.3 Difficulty Tuning Knobs

| Parameter | Easy | Medium | Hard | Nightmare |
|-----------|------|--------|------|-----------|
| Trap activation delay | 2000 ms | 500 ms | 100 ms | Predictive |
| Magnet PWM max | 30% | 60% | 90% | 100% |
| Trap cooldown | 5 s | 3 s | 2 s | 1 s |
| Wall close speed | Slow (servo rate-limited) | Normal | Fast (slam) | Fast + coordinated |
| AI solver skill (Mode 3) | Greedy, no prediction | A*, slow replanning | A*, fast replanning | A* + momentum model |
| Time limit | 5 min | 3 min | 2 min | 90 s |
| Lives | 5 | 3 | 2 | 1 |

### 9.4 Maze Design Guidelines

When designing a new lasercut maze plate:

1. **Minimum 2 viable paths** from start to finish (so traps can block one without making the maze unsolvable).
2. **Trap placement rules**: no trap within 30 mm of the start zone; at least one safe checkpoint between any two traps; no more than 3 traps guarding any single path.
3. **Path width**: 22 mm minimum, 30 mm at intersections.
4. **Dead ends**: maximum depth 3 path-widths (prevents frustrating long dead ends that waste time without fun).
5. **Pit placement**: always adjacent to at least one magnet (so the magnet can pull the marble in). Pits near the finish increase tension.
6. **Difficulty gradient**: the first third of the maze (near start) should have fewer and easier traps; the final third should be dense with traps.

---

## 10. Player Input Methods

### 10.1 Physical Analog Joystick (Primary)

A 2-axis analog thumbstick mounted on a control panel connected to the base.

| Parameter | Value |
|-----------|-------|
| Type | PS2-style dual-axis analog joystick module |
| Interface | 2x analog (X, Y) + 1x digital (button) |
| Connection | Wired to ESP32 ADC (or a dedicated Arduino Nano for ADC + USB HID) |
| Mapping | Joystick deflection -> tilt angle (linear, with adjustable sensitivity and dead zone) |
| Sensitivity range | 0.5x to 2.0x (configurable in settings) |
| Dead zone | 5% default (center dead band to prevent drift) |
| Panel | 3D-printed enclosure, 80 x 80 x 40 mm, with anti-slip rubber feet |

### 10.2 Phone App (Accelerometer Tilt)

The phone's accelerometer maps the device's tilt directly to the maze platform tilt.

| Parameter | Value |
|-----------|-------|
| API | DeviceOrientationEvent (beta = pitch, gamma = roll) |
| Connection | WebSocket over local WiFi |
| Latency | ~50-100 ms (WiFi + processing) |
| Calibration | "Hold phone level and tap to calibrate" sets zero point |
| Sensitivity | Adjustable 0.5x to 3.0x multiplier |
| Fallback | Virtual joystick (nipple.js) if accelerometer unavailable |

### 10.3 USB Gamepad

Standard USB gamepad (Xbox controller, PS4 controller, or generic HID) connected to the Pi.

| Parameter | Value |
|-----------|-------|
| Library | pygame.joystick |
| Mapping | Left stick X/Y -> tilt; right stick or D-pad -> trap selection; triggers -> trap activation |
| Dead zone | 10% |
| Rumble feedback | Supported (if controller has it) -- buzz on trap activation, life lost |

### 10.4 Keyboard (Development / Accessibility)

| Key | Action |
|-----|--------|
| W/A/S/D or Arrow keys | Tilt pitch/roll |
| 1-9 | Activate trap by ID |
| Space | Activate all traps in current zone |
| R | Reset game |
| P | Pause |
| Esc | Return to menu |

### 10.5 Input Abstraction

All input methods feed into a unified `InputState` object:

```python
@dataclass
class InputState:
    tilt_x: float       # -1.0 to 1.0 (mapped to -max_tilt to +max_tilt)
    tilt_y: float       # -1.0 to 1.0
    trap_activate: list  # list of trap IDs to fire this frame
    button_start: bool
    button_pause: bool
    button_reset: bool
    source: str         # "joystick", "phone", "gamepad", "keyboard"
```

The game engine consumes `InputState` regardless of source. Multiple sources can be active simultaneously (e.g., one player on joystick, another on phone for traps).

---

## 11. Output and Feedback

### 11.1 LED Strips

Addressable WS2812B LED strips mounted around the maze perimeter and/or beneath the translucent acrylic floor for underglow effects.

| Parameter | Value |
|-----------|-------|
| LED type | WS2812B (5V, addressable RGB) |
| Count | ~120 LEDs (60/m strip, 2 m perimeter) |
| Data pin | ESP32 GPIO via level shifter (3.3V -> 5V) |
| Power | 5V rail, ~3.6A max (all white full brightness), typically < 1A |

#### LED Modes

| Mode | Trigger | Effect |
|------|---------|--------|
| Idle pulse | IDLE state | Slow blue-green breathing |
| Ready | READY state | Solid green |
| Playing | PLAYING state | Ambient white underglow |
| Trap fired | Any trap activation | Red flash at trap location |
| Magnet active | Electromagnet on | Purple glow under active magnet |
| Life lost | Marble in pit | Red chase animation (1 s) |
| Checkpoint | Marble reaches checkpoint | Gold sparkle burst (0.5 s) |
| Victory | Marble reaches finish | Rainbow chase (3 s) |
| Game over | Timer expires / no lives | Red fade to black |

### 11.2 Discrete LEDs at Trap Positions

In addition to the strip, individual 5 mm LEDs (or SMD LEDs on the maze PCB) at each trap indicate state:

| State | LED Color |
|-------|-----------|
| Trap armed (ready) | Dim amber |
| Trap firing | Bright red |
| Trap cooldown | Blinking amber |
| Trap disabled | Off |

These are driven by the PCA9685's unused channels or shift registers.

### 11.3 Display

| Option | Implementation |
|--------|---------------|
| **Web UI (primary)** | FastAPI serves a dashboard on the Pi; accessible from any browser on the LAN. Shows timer, score, lives, marble position overlay, trap status. |
| **HDMI monitor** | Pi drives an HDMI display with a Pygame or Electron-based dashboard. For spectators or demo setups. |
| **OLED (on device)** | 1.3" SH1106 128x64 I2C OLED mounted on the control panel. Shows timer and score. Minimal but always available. |

### 11.4 Sound

| Library | pygame.mixer |
|---------|-------------|
| Output | 3.5 mm audio jack on Pi -> small powered speaker |
| Sounds | Trap fire (click/buzz), life lost (descending tone), checkpoint (chime), victory (fanfare), game over (sad trombone) |
| Volume | Software-adjustable; mute button on web UI |

Sound files are short WAV clips stored in a `sounds/` directory. They can be generated procedurally (synthesized tones) or sourced from free SFX libraries.

---

## 12. Manufacturing Plan

### 12.1 Fabrication Methods by Part

| Part | Method | Material | Qty |
|------|--------|----------|-----|
| Maze floor layer | Laser cut | 3 mm clear acrylic | 1 per maze |
| Maze wall layer | Laser cut | 6 mm Baltic birch plywood | 1 per maze |
| Maze rim / guide rails | Laser cut | 3 mm plywood | 1 per maze |
| Maze services layer | Laser cut | 6 mm plywood | 1 per maze |
| Moving wall blades | Laser cut | 3 mm plywood | 10-20 per maze |
| Base platform | Laser cut | 6 mm plywood | 1 |
| SPM linkage arms (if Option A) | 3D print (FDM, PLA/PETG) | N/A | 4 arms + 1 hub |
| Gimbal frames (if Option B) | Laser cut | 6 mm plywood or 6 mm acrylic | 2 frames |
| Motor mounts | 3D print or laser cut | PLA/PETG or 6 mm ply | 2 |
| Servo mounts | 3D print | PLA | 16 |
| Crank-slider linkages | 3D print | PLA | 16 |
| Sensor housings | 3D print | PLA | 16 |
| Joystick enclosure | 3D print | PLA | 1 |
| Camera mast brackets | 3D print | PETG | 2 |
| Controller board enclosure | 3D print | PLA | 1 |
| Frame legs and camera mast | Buy (2020 aluminum extrusion) | 6063 aluminum | ~3 m total |

### 12.2 Laser Cutting File Preparation

All lasercut parts are designed in a 2D CAD tool (Inkscape, Fusion 360, or LibreCAD) and exported as DXF or SVG.

- **Kerf compensation**: 0.1 mm per side for plywood, 0.05 mm for acrylic (applied in CAD before cutting).
- **Tab-and-slot joints**: 6 mm ply uses 6 mm wide tabs; 3 mm ply uses 3 mm tabs. Tab length 10-15 mm, spacing 30-50 mm.
- **Line colors**: Red = cut through; Blue = engrave/score (for labeling, alignment marks); Green = raster engrave (decorative).
- **Material test**: Always run a small test piece first to calibrate power/speed for the specific laser cutter and material batch.

### 12.3 3D Printing Guidelines

| Parameter | Value |
|-----------|-------|
| Printer | FDM (Ender 3 / Prusa MK3S / Bambu P1S or equivalent) |
| Material | PLA for low-stress parts; PETG for camera mast brackets and output hub |
| Layer height | 0.2 mm (0.12 mm for fine joint features) |
| Infill | 30% gyroid for structural parts; 15% for housings |
| Supports | Minimal; parts are designed for supportless printing where possible |
| Tolerances | Holes +0.2 mm oversize; press-fit shafts -0.1 mm |

### 12.4 Assembly Sequence

1. **Frame**: Cut and assemble base platform. Attach 2020 extrusion legs and camera mast.
2. **Tilt mechanism**: Print/cut and assemble SPM or gimbal. Mount NEMA 17 motors. Verify range of motion.
3. **Electronics**: Populate controller board (or breadboard prototype). Flash ESP32 firmware. Connect to Pi. Verify serial comms.
4. **Maze plate**: Laser cut all four layers. Assemble with standoffs. Install servos, magnets, hall sensors. Wire to Molex connector.
5. **Integration**: Mount maze plate on tilt mechanism. Connect Molex. Route cable harness.
6. **Camera**: Mount camera on mast. Run calibration. Verify marble tracking.
7. **Input**: Wire joystick. Set up phone app. Test all input methods.
8. **Game software**: Run game engine. Test all modes. Tune difficulty parameters.
9. **Polish**: Install LEDs. Add sound. Final calibration and endurance testing.

---

## 13. Bill of Materials and Cost Estimate

### 13.1 Electronics

| Item | Qty | Unit Price (USD) | Total |
|------|-----|-----------------|-------|
| Raspberry Pi 4 Model B (4 GB) | 1 | $55 | $55 |
| ESP32-S3-DevKitC-1 | 1 | $10 | $10 |
| TMC2209 SilentStepStick | 2 | $6 | $12 |
| NEMA 17 stepper motor (42 mm, 1.5A, 0.45 Nm) | 2 | $12 | $24 |
| PCA9685 16-ch servo driver breakout | 1 | $4 | $4 |
| SG90 micro servo | 16 | $2 | $32 |
| 12V DC electromagnet (20 mm dia, 2.5N) | 10 | $3 | $30 |
| IRLZ44N N-channel MOSFET (or equivalent logic-level) | 10 | $0.50 | $5 |
| 1N4007 diode | 10 | $0.05 | $1 |
| SS49E linear hall effect sensor | 16 | $0.60 | $10 |
| 3 mm x 2 mm NdFeB disc magnets (bias) | 16 | $0.15 | $3 |
| BNO055 9-DOF IMU breakout | 1 | $12 | $12 |
| USB webcam (720p, global shutter preferred) | 1 | $25 | $25 |
| WS2812B LED strip (60 LED/m, 2 m) | 1 | $8 | $8 |
| Mean Well LRS-150-12 (12V 12.5A PSU) | 1 | $18 | $18 |
| Pololu D24V50F5 (5V 5A step-down) | 1 | $12 | $12 |
| CD74HC4067 16-ch analog MUX breakout | 1 | $3 | $3 |
| Analog joystick module (dual-axis + button) | 1 | $3 | $3 |
| SH1106 1.3" OLED display (I2C) | 1 | $5 | $5 |
| Misc (perfboard, headers, JST connectors, wire, barrel jacks, fuses) | -- | -- | $25 |
| **Electronics subtotal** | | | **$297** |

### 13.2 Mechanical / Hardware

| Item | Qty | Unit Price (USD) | Total |
|------|-----|-----------------|-------|
| 6 mm Baltic birch plywood (24" x 24" sheets) | 4 | $8 | $32 |
| 3 mm clear acrylic (24" x 24" sheet) | 1 | $10 | $10 |
| 3 mm Baltic birch plywood (24" x 24" sheets) | 2 | $6 | $12 |
| 2020 aluminum extrusion (1 m lengths) | 3 | $5 | $15 |
| 2020 T-slot corner brackets + T-nuts | 20 | $0.50 | $10 |
| 608ZZ bearings (for gimbal option) | 4 | $1.50 | $6 |
| GT2 belt (2 m) + 2x 20T pulleys + 2x 60T pulleys (for gimbal option) | 1 set | $12 | $12 |
| M3 screws, nuts, standoffs assortment | 1 kit | $10 | $10 |
| M5 screws, nuts (for scaled SPM joints) | 1 kit | $8 | $8 |
| 8 mm steel rod (300 mm, for SPM output) | 1 | $3 | $3 |
| Dowel pins (6 mm x 20 mm, alignment) | 4 | $0.50 | $2 |
| Quarter-turn fasteners (Dzus style) | 4 | $2 | $8 |
| Molex Mini-Fit Jr connectors (4-pin) | 2 pairs | $2 | $4 |
| 3D printing filament (PLA, 1 kg) | 1 | $18 | $18 |
| 3D printing filament (PETG, 0.5 kg) | 1 | $15 | $15 |
| Chrome steel ball bearing 16 mm (marble) | 5 | $0.50 | $3 |
| 40-pin IDC ribbon cable + connectors (1 m) | 1 | $4 | $4 |
| **Mechanical subtotal** | | | **$172** |

### 13.3 Total

| Category | Cost |
|----------|------|
| Electronics | $297 |
| Mechanical / Hardware | $172 |
| **Grand total** | **$469** |

Contingency (10%): $47 -> **$516 total budget** (slightly over $500 target; shaveable by omitting IMU, using cheaper camera, or sourcing from AliExpress).

---

## 14. Open Questions and Trade-off Matrix

### 14.1 Trade-off Matrix

| Decision | Option A | Option B | Recommendation | Rationale |
|----------|----------|----------|----------------|-----------|
| Tilt mechanism | SPM (spherical parallel manipulator) | Series gimbal (two rotary axes) | **Prototype both; default to gimbal** | Gimbal is far simpler to build and debug at scale. SPM is more elegant but higher risk. The existing SPM OpenSCAD design can be scaled, but joint clearances at larger radii are unproven. Build a gimbal first for functional testing; port to SPM later if desired. |
| Stepper control | Open-loop (step counting only) | Closed-loop (IMU feedback) | **Start open-loop; add IMU later** | Open-loop with TMC2209 (StealthChop, low-speed torque) is sufficient for +/-10 deg tilt. IMU adds complexity. Wire the I2C header but don't populate until needed. |
| Camera mounting | Fixed mast (on base frame) | Mounted on maze platform | **Fixed mast** | Simpler wiring, no added inertia. Perspective correction is minor at +/-10 deg and solved with ArUco homography. |
| Host computer | Raspberry Pi 4 + ESP32 (dual processor) | Single Raspberry Pi with real-time HAT | **Pi + ESP32** | Clean separation of concerns. ESP32 handles hard-real-time motor control; Pi handles CV and game logic. A single-board approach risks jitter in step pulse generation when the CPU is loaded with OpenCV. |
| Maze plate | Fixed (glued/screwed) | Modular (quick-release) | **Modular** | Interchangeable mazes massively increase replayability. The Dzus fastener + Molex connector approach adds ~$12 in cost and trivial complexity. |
| Marble tracking | Hall sensors only | CV only | Hall + CV (hybrid) | Hall sensors give guaranteed detection at critical points (start, finish, pits) even if CV fails. CV gives continuous position. They complement each other. |
| Communication | UART binary protocol | USB serial JSON | **UART binary** | Lower latency, lower overhead, deterministic parsing. JSON is wasteful for 50 ms telemetry packets. |

### 14.2 Open Questions

| ID | Question | Impact | Notes |
|----|----------|--------|-------|
| Q1 | What is the maximum acceptable latency from tilt input to physical platform motion? | Controls motor driver configuration and comms architecture | Target: < 50 ms total. Measure during prototyping. |
| Q2 | Is the SG90 torque sufficient to reliably raise/lower a 3 mm plywood wall blade with the crank-slider linkage? | May need MG90S (metal gear) upgrade | Test with prototype linkage. SG90 stalls at ~1.8 kg-cm; a light wall blade should be fine, but friction in the slot could be an issue. |
| Q3 | How bright do the LEDs need to be for the underglow to be visible through the acrylic floor in a lit room? | May need more LEDs or a diffuser layer | Test with a sample piece of acrylic + LED strip. White translucent acrylic may work better than clear. |
| Q4 | Should the marble return chute be automated (motorized lift) or manual (player retrieves the marble)? | Complexity vs. QoL | A gravity chute to a collection tray is the simplest. The player places the marble back on start. A motorized return is a stretch goal. |
| Q5 | What is the minimum camera resolution for reliable marble tracking at 610 mm maze width? | Determines camera choice | At 720p across a 610 mm maze, each pixel ~= 0.85 mm. A 16 mm marble is ~19 pixels across. This should be sufficient. Test with prototype. |
| Q6 | Should the system support multiple maze sizes (e.g., 12"x12" for a simpler game)? | Affects quick-release mount design and software calibration | Design the mount for 24"x24" but don't preclude smaller plates. The ArUco calibration auto-adapts to maze size. |
| Q7 | Should the game engine run asynchronously or in a fixed-timestep loop? | Affects code architecture and determinism | A fixed 20 ms (50 Hz) game tick with async I/O for web and serial is the cleanest architecture. |

---

## 15. Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| R1 | SPM joints have too much backlash at scaled-up size, causing imprecise tilt | Medium | High | Prototype joints early. Add anti-backlash springs. Fallback to gimbal design. |
| R2 | NEMA 17 torque insufficient for maze weight at target acceleration | Low | High | Torque analysis shows 0.83 Nm needed; NEMA 17 + 3:1 reduction provides ~1.35 Nm. If insufficient, swap to NEMA 23 (same driver, larger motor). |
| R3 | SG90 servo lifespan under repeated slam actuation is short | Medium | Medium | SG90 is cheap and replaceable. Budget spares. Consider MG90S (metal gear) for heavily-used wall positions. Design servo mounts for tool-free swap. |
| R4 | OpenCV marble tracking fails under certain lighting conditions | Medium | Medium | Use ArUco markers for robust calibration. Use adaptive thresholding. Test under fluorescent, incandescent, and natural light. Provide a hood/shroud option for consistent lighting. Add LED ring on camera mast for controlled illumination. |
| R5 | Electromagnet heat buildup during extended play | Medium | Low | Limit max continuous duty cycle to 80%. Add thermal pads. 2-second watchdog timeout ensures magnets don't stay on if software crashes. |
| R6 | WiFi latency makes phone tilt control feel sluggish | Medium | Medium | Phone control is inherently ~50-100 ms latency. Clearly label it as the "casual" input method. Physical joystick is the primary input for responsive play. |
| R7 | Ribbon cable fatigue from repeated tilt motion | Low | Medium | +/-10 deg is minimal flex. Use stranded (not solid core) ribbon cable. Add strain relief. Budget a replacement cable. |
| R8 | Camera mast vibration causes motion blur | Low | Low | Use a global shutter camera. Stiffen mast with triangulating braces or thicker extrusion. |
| R9 | Maze plate warping (plywood, humidity) | Medium | Medium | Seal plywood with polyurethane. Use Baltic birch (more stable than regular plywood). Store maze plates flat. |
| R10 | Scope creep delays the core experience | High | High | Strictly prioritize: first get tilt + fixed maze + manual play working. Then add traps. Then CV. Then AI. Each phase is independently playable. |

---

## Appendix A: Phase Plan

| Phase | Deliverable | Dependencies | Duration Estimate |
|-------|-------------|-------------|-------------------|
| 0. Prototyping | Tilt mechanism (gimbal or SPM) + 2 steppers working. Manual tilt via joystick. | Motor drivers, ESP32 firmware basics | 2-3 weeks |
| 1. Basic Maze | Lasercut maze plate with fixed walls. Marble rolls, pits work. Hall sensor at start/finish. Timer + score on OLED. | Phase 0 complete | 2 weeks |
| 2. Traps | SG90 moving walls and electromagnets integrated. Traps triggered by hall sensors or timer. | Phase 1 complete | 2-3 weeks |
| 3. Computer Vision | Camera mounted, OpenCV pipeline running, marble tracked. Traps triggered by CV position. | Phase 2 complete, Pi set up | 2 weeks |
| 4. Game Modes | All 6 game modes implemented in game engine. Phone/web control working. | Phase 3 complete | 2 weeks |
| 5. AI & Polish | AI maze solver. Trap AI. LED effects. Sound. Leaderboard. Second maze plate. | Phase 4 complete | 3-4 weeks |

**Total estimated timeline: 13-16 weeks** (part-time, ~10-15 hrs/week)

---

## Appendix B: Reference Documents

- `spm_90deg_final.scad` -- Existing OpenSCAD design for the 2-DOF spherical parallel manipulator (inner R=50 mm, outer R=65 mm, NEMA 17 motors at 90 deg, clevis/tang joints, M3 hardware, split-clamp 8 mm output hub).
- TMC2209 datasheet -- Trinamic (now ADI), for UART register map and StallGuard tuning.
- PCA9685 datasheet -- NXP, for I2C register map and PWM configuration.
- SG90 datasheet -- Tower Pro, for mechanical dimensions and pulse width range.
- BNO055 datasheet -- Bosch Sensortec, for fusion mode configuration and I2C protocol.
- OpenCV ArUco module documentation -- for marker generation and pose estimation.

---

## Appendix C: Glossary

| Term | Definition |
|------|-----------|
| SPM | Spherical Parallel Manipulator -- a 2-DOF mechanism where two kinematic chains connect a fixed base to a moving platform via spherical arc links, providing pitch and roll motion. |
| Gimbal | A series-rotary-axes mechanism with two concentric frames, each rotating about one axis. The inner frame carries the payload. |
| StallGuard | A Trinamic TMC2209 feature that detects motor stall by monitoring back-EMF, enabling sensorless homing. |
| StealthChop | A TMC2209 operating mode that uses voltage-mode chopping for silent operation at low speeds. |
| ArUco | A fiducial marker system in OpenCV used for camera pose estimation and image rectification. |
| Kalman filter | A recursive estimator that fuses noisy measurements (marble position from CV) with a motion model to produce smooth position and velocity estimates. |
| PCA9685 | A 16-channel, 12-bit PWM driver IC with I2C interface, commonly used to control servos. |
| Dzus fastener | A quarter-turn quick-release fastener used in aviation and racing; here used for tool-free maze plate swaps. |
| MOG2 | Mixture of Gaussians background subtraction algorithm in OpenCV, used to isolate moving objects (the marble) from a static background (the maze). |
