# Horizontal Actuator Architecture

**Parent document:** `PRD.md`  
**Date:** 2026-04-12  
**Status:** Working reference

---

## 1. Overview

Horizontal Actuator uses the same tile-envelope calculator model as Vertical Actuator, but places the actuator beside the linear motion element rather than inside it. The frame is still pushed toward the thinnest practical envelope, and the XY footprint stays tighter than the fully integrated approach, but not every tile can be actuated.

This architecture follows the simulator's regular placement rules:

- fixed wall cells stay fixed
- fixed floor cells stay fixed
- only the legal configurable slots can switch state

That regularity is the main tradeoff for the smaller XY package.

---

## 2. Grid Model

### 2.1 Source of Truth

The simulator grid rules are canonical for this architecture. The browser workspace should preserve the simulator's regular placement of:

- fixed walls / posts
- fixed floors / passages
- configurable slots between those fixed elements

### 2.2 Configurable States

The legal configurable slots can currently become:

- **Open floor**
- **Static wall**
- **Hole**
- **Actuated blocker**

Not every interior tile is eligible. Coverage is constrained by the regular grid logic.

When a slot becomes an actuated gate, that gate also reserves one adjacent bay beneath the fixed lattice. The bay is auto-derived from the lattice and can be flipped to the opposite neighboring side when that alternate bay is still free. The fixed floor or fixed wall above that reservation still remains part of the visible maze surface.

### 2.3 Module Packing

Like Vertical Actuator, the workspace assumes a 3 x 3 module array inside a fixed 36 inch outer frame. The public controls are the same tile-envelope inputs used by Vertical: tile size, border thickness, inner wall thickness, marble size, travel factors, servo range, stroke factor, clearance, pinion diameter, and module size. Horizontal-only geometry such as opening width, separator width, and bay depth is derived internally.

---

## 3. Mechanical Interpretation

### 3.1 Actuator Placement

Each actuator sits adjacent to the motion slot it drives. Mechanically, that means:

- the moving tile or blocker remains compact
- the actuator bay and linkage occupy neighboring underfloor space beneath a fixed lattice cell
- the frame layout has to preserve a regular pattern of fixed structure around the driven slots

### 3.2 Resulting Tradeoff

Compared with the fully integrated approach:

- XY pitch can stay smaller
- the frame can stay minimal
- the pitch-diameter budget can grow because the pinion is fitted against the outer slider envelope instead of the inner cavity inside one tile
- actuator coverage becomes constrained by the regular pattern

This is the architecture to use when density matters more than arbitrary per-tile actuation.

---

## 4. Visualization Requirements

The workspace should communicate the following clearly:

- fixed floors and fixed walls are part of the architecture, not optional state
- actuated blockers reserve real neighboring bay cells
- the adjacent actuator bay is what enables a tighter XY package and larger horizontal pinion
- the top surface above a reserved bay still reads as fixed floor or fixed wall
- holes occupy legal configurable slots, not arbitrary interior positions

The 3D scene should make the reserved bay, linkage path, and flip-able neighboring placement obvious, especially in cutaway regions.

---

## 5. Design Notes

- Avoid legacy frame adjectives; the frame should still be minimized as far as the packaging permits.
- When comparing against Vertical Actuator, focus on **regularity versus coverage** and **smaller XY versus fewer actuatable positions**.
- If future work adds animation, the most useful motion to show is the relationship between the adjacent actuator bay, linkage, and the actuated gate above it.
