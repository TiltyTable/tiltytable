# Product Context: TiltyTable

## TiltyTable — Open Sauce Arcade Game

TiltyTable is a **fun physical interactive arcade game** being built for
**Open Sauce**. Players navigate scored gauntlet and practice levels on a
real 12×12 servo-actuated tile grid with addressable LEDs, Stewart-platform
tilt, arcade roller-ball input, and (planned) camera tracking.

The `arcade/` stack is the game layer: level engine, hardware adapter
(module-grid LEDs/servos), Flask kiosk, scoring, and audio. Simulation mode
exercises the full UI without energizing hardware.

**Cabinet UI** is an important design constraint within that game — not the
whole project identity. The 854×480 projector kiosk must stay player-facing:
minimal text, immediate legibility, action-oriented copy. See
`.cursor/rules/arcade-player-interface.mdc`.

## Marble Maze Design Workspace

## What Is It?
A local browser workspace for comparing two actuator architectures for a modular marble maze:
- Vertical Actuator: integrated actuation inside or under each interior tile
- Horizontal Actuator: adjacent actuator bays with simulator-driven fixed floor and fixed wall regularity

The workspace is used to reason about grid topology, XY packing, wall travel, hole placement, and 3D packaging tradeoffs.

## Key Interfaces
- Browser UI with canvas-based 2D editors, control panels, and stats
- Three.js-based 3D explanatory scenes
- Markdown memory bank and Cursor rules for long-lived project context
- Reference markdown docs (`PRD.md`, legacy option docs) for mechanical background

## Operating Modes
- Architecture review: compare the two actuator approaches over multiple sessions
- Grid editing: click cells to cycle legal states for the active architecture
- Packaging review: inspect the 3D scene and section view for actuator placement tradeoffs

## Project-Specific Integration Concerns
- Serve over HTTP instead of `file://` because the app uses ES modules.
- The old `grid-comparison.html` file now acts as a legacy redirect to the new workspace entrypoint.
- Reference architecture docs now live in `vertical-actuator-architecture.md` and `horizontal-actuator-architecture.md`.
- Horizontal Actuator grid logic is driven by the simulator rules rather than legacy document naming.
