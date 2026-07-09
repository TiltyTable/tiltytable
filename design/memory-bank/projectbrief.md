# Project Brief: Marble Maze Workspace

## Mission
Build and maintain a browser-based workspace for exploring marble-maze actuator architectures, grid rules, and visualization tradeoffs.

## Scope
- Static browser app served from local files over HTTP
- Separate architecture views for Vertical Actuator and Horizontal Actuator
- 2D editing, stats, and 3D explanatory rendering
- Persistent project context via a six-file memory bank

## Key Requirements
- Vertical Actuator uses a dense interior-tile model where only the outer border is permanently static.
- Vertical Actuator interior tiles must support open floor, actuated blocker, and hole states.
- Horizontal Actuator must preserve the simulator's fixed-floor and fixed-wall placement rules.
- Settings and visualizations for the two architectures must remain fully independent.
- `marble-maze/` is its own project workspace with memory bank and Cursor rules.

## Team
- TiltyTable - project maintainers
- Cursor agent sessions - implementation and planning support

## Success Criteria
- The app launches from `index.html` and clearly separates the two actuator architectures.
- Each architecture has its own state, controls, metrics, and 3D renderer.
- The memory bank is complete and future sessions can resume work without rediscovering project context.
