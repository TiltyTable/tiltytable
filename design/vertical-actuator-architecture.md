# Vertical Actuator Architecture

**Parent document:** `PRD.md`  
**Date:** 2026-04-12  
**Status:** Working reference

---

## 1. Overview

Vertical Actuator packages the actuator inside or directly underneath each motion tile. The frame is kept as thin as practical, but every interior tile grows in XY to make room for the integrated actuator envelope.

This architecture is the high-flexibility option:

- Every interior tile is addressable
- Only the border stays permanently static
- Interior tiles can switch between open floor, actuated blocker, and hole

The software model for this architecture is intentionally dense rather than checkerboard-based.

---

## 2. Grid Model

### 2.1 Border Rule

The outer border is permanently fixed structure that contains the marble and stiffens the frame.

### 2.2 Interior Rule

Every interior tile is editable and may be one of:

- **Open floor**
- **Actuated blocker**
- **Hole**

Unlike the regular horizontal architecture, there are no permanently fixed interior floor/passage or wall/post cells. Freedom is maximized at the cost of a larger minimum tile envelope.

### 2.3 Module Packing

The workspace currently models the maze as a 3 x 3 array of modules inside a fixed 36 inch outer frame. Module size, tile envelope, actuator width, and actuator depth are tunable from the app.

---

## 3. Mechanical Interpretation

### 3.1 Actuator Placement

Each tile contains:

- a top motion surface
- an integrated actuator package below that surface
- a linkage or lifting element aligned with that tile

This keeps the surrounding frame minimal, because the mechanism is carried by the tile package instead of being offset into the neighboring structure.

### 3.2 Resulting Tradeoff

The actuator envelope consumes XY area inside each tile, so:

- minimum tile pitch increases
- cell count per module decreases
- actuation freedom increases dramatically

This is the architecture to use when arbitrary interior actuation matters more than raw grid density.

---

## 4. Visualization Requirements

The workspace should communicate the following clearly:

- every interior tile is independently editable
- the actuator package lives within or directly beneath the moving tile
- blocker and hole states are both legal at any interior position
- larger XY packaging is the price paid for universal actuation

The 3D scene should prioritize internal packaging clarity over photorealism.

---

## 5. Design Notes

- Keep the frame language neutral: the frame is not “thick” or “thin”, it is simply minimized as far as the packaging allows.
- When comparing against Horizontal Actuator, focus on **coverage versus XY density**, not on frame adjectives.
- If future work adds animation, the most useful motion to show is blocker travel and the space claim of the integrated actuator package.
