# Tilt-table kinematics & tilt envelope

Single source of truth for the platform's tilt limits and how they depend on
geometry. Re-run the model after any geometry change:

```bash
python3 analysis/tilt_kinematics.py
```

The model (`tilt_kinematics.py`) reproduces the inverse kinematics that the
firmware (`arduino/uim5756pm_stewart/uim5756pm_stewart.ino`) hard-codes, so the
two must be kept in sync. If you change a constant in one, change it in both.

## Mechanism

3 legs at 120°, each: `motor —R(driven)→ crank —R→ arm —S(GE8C)→ platform`.
The crank sweeps in the leg's radial–vertical plane.

## Assumptions (the things to verify when they change)

| Assumption | Value | Notes |
| --- | --- | --- |
| Platform rod **radius** | **119 mm** | Firmware `TABLE_ROD_RADIUS_MM`. **= 238 mm diameter** — see discrepancy below. |
| Crank radius | 30 mm | Dominant tilt driver. |
| Arm (coupler) length | 110 mm | Center-to-center. |
| Base motor radius | **119 mm** (2026-07-09) | Motors moved inward. At max heave, crank+arm vertical ⇒ `BASE = R_platform`. Old value was 149 mm (`R_platform + crank`) for arm-vertical at *neutral*. |
| Model top-height datum | 110 mm | Firmware `NEUTRAL_TOP_Z_MM`; gameplay adds fixed heave. |
| Rod-end (GE8C) limit | 14° | Firmware value; GE8C datasheet allows 15°. |
| Gameplay pose | **20 mm heave** | Best modeled all-direction envelope for the as-built `BASE = TABLE = 119 mm` geometry. |

### Arm-vertical design variant
Holding **both** "crank horizontal" and "arm vertical" at neutral forces:
- `BASE_MOTOR_RADIUS − CRANK_RADIUS = PLATFORM_ROD_RADIUS`
- `NEUTRAL_TOP_Z = ARM_LENGTH`

So **you cannot shorten the arm in isolation** — doing so while keeping both
invariants also lowers the platform neutral height by the same amount. To keep
the platform height fixed while shortening the arm, you must give up one
invariant (e.g. let the arm sit slightly off-vertical at neutral).

The as-built mechanism does exactly that: moving the motors inward to
`BASE_MOTOR_RADIUS = PLATFORM_ROD_RADIUS = 119 mm` gives up arm-vertical at
neutral. At the fixed 20 mm gameplay height, the crank and arm are diagonal.
The arm-vertical sweep later in this document is design exploration, not the
current operating pose.

## ⚠️ Open discrepancy to resolve

- Firmware encodes platform rod **radius = 119 mm**.
- Design note from the engineer says **"radius 238 mm."**
- **238 = 2 × 119**, so this is almost certainly a **radius/diameter mix-up**
  (238 mm is the diameter). The firmware geometry is self-consistent at 119 mm
  radius. **Confirm which it is** — it changes the tilt envelope by ~2×.

## Results (computed by the model)

"Guaranteed tilt" = max tilt available in the **worst** direction (the envelope
you can rely on in any direction). "Best" = the most favorable direction.

### Headline (firmware geometry: 119 mm radius, 30 mm crank, 110 mm arm)
- At fixed **20 mm heave**, **guaranteed tilt ≈ 4.8°** in any direction;
  best direction ≈ **5.5°**.
- **Limiting constraint = crank reach** (the crank/arm can't close the loop),
  *not* the rod-end angle. The GE8C's 14–15° is not the bottleneck here.
- Roller control uses a **4.6° circular envelope**, keeping a small margin and
  preventing unreachable square-corner requests such as roll=5°, pitch=5°.
- The old ≈12.6° / 16.7° result described the arm-vertical design variant at
  zero heave (`BASE = TABLE + CRANK = 149 mm`), not the as-built platform.

If 238 mm is truly the **radius** (doubled platform): guaranteed tilt drops to
**≈ 6.3°** — half — because the rim must travel twice as far vertically for the
same tilt.

### Arm-length sweep (invariants held, so neutral height = arm length)

R = 119 mm radius, crank = 30 mm, rod-end limit 14°:

| Arm (mm) | Neutral Z (mm) | Guaranteed tilt | Best tilt | Limited by |
| ---: | ---: | ---: | ---: | --- |
| 70 | 70 | 11.4° | 14.1° | rod-end angle |
| 80 | 80 | 11.9° | 14.9° | rod-end angle |
| 90 | 90 | 12.2° | 15.6° | rod-end angle |
| 100 | 100 | 12.5° | 16.2° | crank reach |
| 110 | 110 | 12.6° | 16.7° | crank reach |
| 120 | 120 | 12.7° | 16.8° | crank reach |
| 130 | 130 | 12.8° | 17.0° | crank reach |

**Takeaway:** arm length barely moves the tilt envelope (~1.4° across
70–130 mm). Shortening the arm *slightly* reduces tilt **and** shifts the
binding limit to the rod-end bearing (a shorter arm swings through a larger
angle for the same platform motion), so below ~100 mm the GE8C angle becomes
the bottleneck.

### What actually drives tilt: crank radius (R = 119, arm = 110)

| Crank (mm) | Guaranteed tilt | Best tilt | Limited by |
| ---: | ---: | ---: | --- |
| 20 | 8.8° | 11.5° | crank reach |
| 25 | 10.7° | 14.2° | crank reach |
| 30 | 12.6° | 16.7° | crank reach |
| 35 | 14.3° | 18.4° | rod-end angle |
| 40 | 15.8° | 19.9° | rod-end angle |

**To get more tilt, increase crank radius (and/or shrink platform radius) — not
arm length.** Past ~35 mm crank the GE8C 14° limit becomes the bottleneck, so
crank and bearing must be sized together.
