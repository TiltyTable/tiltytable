# Marble Maze — OpenSauce 2026 Build Video Plan

A graphics-forward maker video plan, reverse-engineered from the actual project
files (`PRD.md`, the `memory-bank/`, the actuator architecture docs, the hardware
firmware/control-center, `hardware_config.yaml`, and the CAD renders in
`assets/site/`). Built to cover the unfilmed first half of the build with CAD
animation, motion graphics, and Ken Burns stills carried by voiceover.

---

## Part 1 — What the exhibit actually is

### The one-liner
A large-format, physically interactive **marble tilt maze that fights back**. A
visitor tilts a ~36-inch motorized table to roll a steel marble through a
labyrinth, while the maze actively works against them: floor tiles rise and fall
to open and block paths, hidden electromagnets tug the marble toward pits, and an
overhead camera tracks every move so the maze can react in real time.

### Two mechanisms doing two different jobs
The project has **two independent motion systems**, and the video needs to keep
them visually distinct:

**1. The tilt table (gross motion — steers the marble).**
Per `hardware_config.yaml`, the current design is a **3-motor tilt table**, not a
gimbal. Three **UIROBOT NEMA 23 integrated closed-loop steppers**, each behind a
**StepperOnline 5:1 planetary gearbox** (~5.67 N·m rated output), sit on a fixed
base. Each motor turns a short **0.92" crank** connected to the underside of the
table by a **rod with spherical Heim joints on both ends**. Three cranks lifting
and lowering three points under the table produce smooth 2-axis tilt. Geometry is
limited to a **±70° crank swing** (to stay clear of the over-center singularity),
which yields about **0.86" of vertical stroke** and **±10° of table tilt** — the
operational sweet spot for marble control. Closed-loop encoders mean the table
knows its true angle, not just commanded steps.

**2. The actuated maze surface (fine motion — changes the maze).**
This is what the CAD renders in `assets/site/` show. The maze floor is a grid of
**tiles that travel vertically**, each driven by a **rack-and-pinion "elevator"
module**: a servo/motor (blue body in the renders) turns a **pinion gear** that
meshes with a **vertical rack**, raising or lowering a tile through a
capsule-shaped pocket in a triangulated truss cage. Each interior tile can be one
of three states — **open floor, raised blocker (wall), or hole (pit)** — so the
maze layout itself is reconfigurable on the fly. The working bench hardware today
drives these with **SG90 servos through a PCA9685 16-channel driver and an Arduino
Uno R3 bridge** (`hardware/`), with each servo storing named `wall` / `floor` /
`hole` positions in `servo_calibration.json`.

The project's two architecture docs capture the central design fork for that
surface:
- **Vertical Actuator** — the actuator lives *inside/under each tile*. Maximum
  freedom (every interior tile addressable) at the cost of a larger tile pitch.
- **Horizontal Actuator** — the actuator sits in an *adjacent under-floor bay*
  beside the tile. Tighter XY packing and a bigger pinion budget, but only a
  regular pattern of slots can be actuated.
A browser-based **Three.js architecture explorer** (`index.html`, `app/`) was
built to compare these two approaches — packing density, stroke, pinion sizing —
and a procedural maze generator produces solvable layouts with safe routes,
avoidable trap holes, reward tiles, and multi-phase dynamic traps.

### The supporting systems (from the PRD)
- **Traps:** rising/falling walls (the elevator tiles), DC **electromagnets** under
  the floor that pull the ferromagnetic marble toward pits, and passive **pit
  traps** with a gravity return chute.
- **Sensing:** **hall-effect sensors** at waypoints (start, finish, checkpoints,
  pit bottoms) detect the marble; an optional **IMU** closes the loop on true tilt.
- **Computer vision:** an overhead **USB camera + OpenCV** tracks the marble at
  720p/30fps, corrected by ArUco corner markers, feeding a Kalman filter for
  position + velocity.
- **Brains:** a **Raspberry Pi** runs the game engine, CV, and a FastAPI web
  server; an **ESP32-S3** is the planned real-time controller for steppers/servos/
  magnets. Today a **Raspberry Pi "control center" web app** already does live
  servo control, calibration, and an MJPEG webcam stream.

### How visitors interact
- **Input:** physical **analog joystick** (primary), **phone tilt** via the
  device accelerometer over WiFi, or a **USB gamepad**. Tilt deflection maps to
  table angle with adjustable sensitivity and dead zone.
- **Game modes:** Classic Solo (reach the finish before time runs out, 3 lives);
  **Player vs AI Trap Master** (you tilt, the computer fires traps using CV);
  **AI vs Player** (the computer solves the maze, you man the traps); Two-Player
  Versus (one tilts, one traps); Speed Run; and **Puzzle Mode** (raise/lower walls
  in sequence to open a path).
- **Feedback:** timer, scoring, **WS2812B LED** underglow through the translucent
  floor, sound, and a live score/marble-position display.
- **The OpenSauce hook:** it's a crowd machine — walk up, grab the stick, and
  fight a maze that is actively trying to beat you while a camera narrates your
  every mistake in lights.

---

## Part 2 — CAD views & animations worth exporting

You already have three Fusion-style renders of the rack-and-pinion elevator module
(`assets/site/elevator-mechanism-{iso,side,front}.png`) — those are gold and should
be reused directly as Ken Burns stills. The list below is what's worth *exporting
or rendering* from CAD/Three.js to carry the unfilmed build. For each: the view,
what it explains, and where it pays off in the story.

### Tier 1 — must-have (these carry the explainer spine)

1. **Full-table hero turntable (360° orbit).**
   The whole 36" tilt table, marble on the maze surface. Establishes scale and
   "what is this thing" before any teardown. *Explains:* the product at a glance.

2. **Tilt-mechanism cutaway + motion loop.**
   Hide the tabletop; show the three NEMA 23 + gearbox + crank + Heim-rod legs.
   Animate one full tilt cycle (level → +10° → −10°) so the audience sees three
   cranks converting rotation into table tilt. *Explains:* how motorized tilt
   actually works — the single most important "aha" of the gross-motion system.

3. **Single-leg kinematics close-up.**
   One crank + rod, annotated: 0.92" crank radius, ±70° swing, 0.86" stroke,
   Heim joints at both ends. Animate the crank sweeping through its limit and
   freeze at the over-center danger zone. *Explains:* why the geometry is limited,
   and why closed-loop steppers matter.

4. **Elevator actuator turntable (the CAD you already rendered, now moving).**
   Orbit the rack-and-pinion module, then animate a tile cycling
   **floor → wall → hole**. *Explains:* the maze surface is reconfigurable; this is
   the project's signature mechanism.

5. **Elevator exploded view.**
   Blow apart the truss cage, tile, rack, pinion, and servo along an axis, then
   reassemble. *Explains:* part count and how the module goes together — perfect
   for the "here's what I had to print/cut ×N" beat.

6. **Maze cutaway / layer stack.**
   Section through the maze showing the layered build (guide rim → wall layer →
   translucent floor → services layer with servos, magnets, hall sensors, wiring).
   *Explains:* that the maze is a dense stack, not just a board — sells the
   engineering depth.

### Tier 2 — high value (concept + "the maze fights back")

7. **Vertical vs Horizontal actuator comparison (side-by-side, from the explorer).**
   Export matched shots from the Three.js architecture explorer showing the two
   packing strategies, with the adjacent bay/linkage highlighted on the horizontal
   one. *Explains:* the real design decision you wrestled with — great for the
   "engineering tradeoff" beat and shows the software tool you built.

8. **Electromagnet trap diagram (motion graphic over a cutaway).**
   Marble rolling over a floor tile; magnet beneath energizes; field-pull arrow
   yanks marble toward a pit. *Explains:* the invisible trap — impossible to film
   clearly, ideal as graphics.

9. **System block diagram, animated build-on.**
   Animate the PRD's architecture diagram: Player input → Pi (game engine + CV) →
   ESP32 → motors/servos/magnets/sensors → table. Lines light up as data flows.
   *Explains:* how everything talks; orients the viewer before electronics b-roll.

10. **CV tracking overlay (screen-capture style mockup).**
    Top-down maze with a tracked marble, ArUco corners, trap zones as circles, a
    predicted-path vector. *Explains:* the camera "sees" the marble and the maze
    reacts — the magic that makes it interactive.

### Tier 3 — nice-to-have / transitions

11. **Procedural maze-generation animation.** Use the live generator to show a
    layout assembling — safe route, branch paths, trap holes, reward tiles
    appearing. *Explains:* every play is different. Strong B-roll/transition.
12. **Wireframe → shaded reveal of the full assembly.** A satisfying "design
    coming to life" transition for the act break into real build footage.
13. **Power/wiring schematic callout.** 12V rail → buck → 5V/3.3V; light, only if
    you go deep on electronics.

> Export notes: render Tier-1 turntables at 1080p/60 (or 4K if you want push-ins),
> alpha/transparent or dark-grey background (matches your existing renders), and
> keep a consistent camera focal length so cuts between CAD shots feel coherent.
> The existing PNGs are dark-grey-bg, so match that.

---

## Part 3 — Scene-by-scene video plan

Target: ~10–12 min maker video. Tag legend —
**[CAD]** CAD/Three.js animation · **[STILL]** still + Ken Burns/push-in ·
**[SHOOT]** real-time footage you still need to capture · **[GFX]** motion-graphic
overlay/diagram. Most early scenes lean CAD/GFX by necessity; the back half is
where your remaining build time buys real footage.

### ACT I — Premise & hook (0:00–1:30)

- **S1 · Cold open / hook (0:00–0:20)** — **[SHOOT]** Fast montage of a hand on
  the joystick, marble rolling, a wall *snapping up* in its path, marble dropping
  into a pit, LED flash. No narration yet, just sound. *(Shoot this once one
  actuated tile + table works — even a bench rig is enough.)*
- **S2 · Title + thesis (0:20–0:45)** — **[CAD]** Full-table hero turntable (view
  #1) with title over it. VO states the premise: a maze that fights back, due at
  OpenSauce 2026.
- **S3 · What it is (0:45–1:30)** — **[CAD]** + **[GFX]** Hero turntable into the
  animated system block diagram (#9). Establish the two motion systems (tilt vs
  actuated surface) and the cast of subsystems.

### ACT II — The stakes (1:30–2:30)

- **S4 · The deadline (1:30–2:00)** — **[SHOOT]** You, to camera, in the shop:
  "OpenSauce is in [X weeks], I'm half done, and I just started filming." Honest
  framing that earns the graphics-heavy approach. **[GFX]** countdown/calendar.
- **S5 · Why it's hard (2:00–2:30)** — **[CAD]** Tilt cutaway (#2) + exploded
  elevator (#5). VO lays out the four hard problems: tilt a heavy table precisely,
  make every tile move, make traps invisible, make the maze *see*.

### ACT III — Gross motion: the tilt table (2:30–4:30)

- **S6 · The tilt concept (2:30–3:10)** — **[CAD]** Tilt-mechanism cutaway + motion
  loop (#2). VO: three motors, three cranks, three rods → 2-axis tilt.
- **S7 · The kinematics problem (3:10–3:50)** — **[CAD]** Single-leg close-up (#3)
  with the over-center freeze. VO: the first build problem — early geometry could
  jam past center; the fix was limiting crank swing to ±70° and going closed-loop.
  *(Escalating problem #1.)*
- **S8 · Building the base (3:50–4:30)** — **[STILL]** Any photos you have of the
  base/motors/gearboxes + **[SHOOT]** new footage of the motors running and the
  table tilting on the bench. **[GFX]** torque/stroke callouts from
  `hardware_config.yaml`.

### ACT IV — Fine motion: the maze that moves (4:30–6:30)

- **S9 · The actuated surface (4:30–5:10)** — **[CAD]** Elevator turntable + tile
  state cycle floor→wall→hole (#4). VO introduces the signature mechanism.
- **S10 · The architecture fork (5:10–5:50)** — **[CAD]** Vertical vs Horizontal
  comparison (#7) from your explorer. VO: the real design decision — coverage vs
  density — and that you built a *tool* to decide. *(Shows depth; escalating
  problem #2: packing the actuator was non-trivial.)*
- **S11 · Making 100 tiles move (5:50–6:30)** — **[STILL]**/**[SHOOT]** Print
  farm, laser-cut parts, the servo bench rig. **[GFX]** part-count tally. VO: the
  grind of manufacturing repeats. *(Escalating problem #3: scale/repeatability.)*

### ACT V — The maze fights back (6:30–8:00)

- **S12 · Invisible traps (6:30–7:00)** — **[CAD]**/**[GFX]** Electromagnet
  diagram (#8) over a maze cutaway (#6). VO: magnets under the floor.
- **S13 · Giving it eyes (7:00–7:45)** — **[GFX]** CV tracking overlay (#10) +
  **[SHOOT]** the real control-center webcam stream you already have running. VO:
  camera + OpenCV → the maze reacts.
- **S14 · The low point (7:45–8:00)** — **[SHOOT]** to camera: the worst moment —
  a failure that nearly sank it (pick the real one: brownouts, servo calibration
  hell, a cracked print, wiring the 40-conductor service loop). Quiet, honest.
  *(Story low point.)*

### ACT VI — Final push & reveal (8:00–11:00)

- **S15 · The push (8:00–8:45)** — **[SHOOT]** Time-lapse of integration:
  electronics into the base, maze stack assembled, first full power-on. **[CAD]**
  wireframe→shaded reveal (#12) as the transition into the finished machine.
- **S16 · First full run (8:45–9:30)** — **[SHOOT]** First clean end-to-end play:
  tilt, traps firing, a pit drop, a win. Let it breathe.
- **S17 · The reveal at OpenSauce (9:30–10:40)** — **[SHOOT]** Real visitors at
  the booth: hands on the stick, faces, walls snapping up, laughter, a crowd. The
  emotional payoff. **[GFX]** light score/leaderboard overlays.
- **S18 · Close (10:40–11:00)** — **[SHOOT]** You + the machine; thanks, what's
  next, subscribe. **[CAD]** final hero turntable to end card.

### What you still need to shoot (priority order)
1. The booth/visitor footage at OpenSauce (S17) — irreplaceable, the whole payoff.
2. A clean first full run (S16) and the hook montage (S1).
3. Table tilting + motors running on the bench (S8).
4. Integration time-lapse + first power-on (S15).
5. Two pieces to camera: the deadline (S4) and the low point (S14).
6. The live CV/webcam stream capture (S13) and servo bench rig (S11).

Everything else can be CAD, stills, or motion graphics — which is exactly the gap
you wanted to fill.

---

## Part 4 — Voiceover script skeleton

Record against this; it's matched scene-for-scene to Part 3. `[ ]` are beats to
personalize with your real numbers and real war stories. Tone: maker-honest, a
little self-deprecating, building to genuine pride.

**S1 — Cold open** *(no VO; sound design only — joystick, roll, the snap of a
wall, the drop)*

**S2 — Title / thesis**
> "This is a marble maze. But it's not the one from your childhood. This one moves.
> It tilts, the walls rise and fall, and it is actively trying to beat you. I'm
> building it for OpenSauce 2026 — and I am running out of time."

**S3 — What it is**
> "Here's the idea. You tilt the whole table to roll a steel marble to the finish.
> But underneath, the maze is rebuilding itself — floors drop into pits, walls
> shoot up to block your path — and a camera overhead watches the marble and
> decides when to spring the next trap. Two completely different machines stacked
> into one: the thing that *tilts*, and the thing that *changes*. Let me show you
> both."

**S4 — The deadline**
> "Quick confession. OpenSauce is in [X weeks]. I'm about halfway through the
> build — and I only just started filming it. So a lot of what you're about to see
> is the actual CAD, because the camera wasn't rolling when I made it. Stick with
> me; the design is the story anyway."

**S5 — Why it's hard**
> "Four problems had to be solved. One: tilt a heavy table in two axes, precisely
> enough to steer a marble. Two: make a hundred individual floor tiles move on
> command. Three: hide the traps so they feel like magic. And four — give the
> whole thing eyes."

**S6 — Tilt concept**
> "Start with the tilt. No gimbal, no gears under the middle — just three motors
> around the edge. Each one spins a little crank, each crank pushes a rod, and each
> rod lifts one point under the table. Three lifts, and the whole surface can lean
> any direction I want."

**S7 — Kinematics problem**
> "Sounds simple. It wasn't. My first crank geometry could swing *past* center and
> lock up solid — a marble maze that bricks itself mid-game. The fix: limit every
> crank to about seventy degrees of swing, which gives me just under an inch of
> stroke and about ten degrees of tilt. And these are closed-loop steppers, so the
> table always knows the angle it's actually at — not the angle I asked for."

**S8 — Building the base**
> "Three NEMA 23s, each behind a five-to-one gearbox — call it five and a half
> newton-meters at each leg. Heim joints on both ends of every rod so nothing
> binds as it tilts. [War story: what went wrong building the base.]"

**S9 — Actuated surface**
> "Now the fun part — the maze that won't hold still. Every tile sits on its own
> little elevator: a motor spins a pinion, the pinion climbs a rack, and the tile
> rides up or down. Three states per tile. Down flat, you've got floor. All the
> way up, it's a wall. Drop it out, and now there's a hole where the marble fell
> through."

**S10 — Architecture fork**
> "There were two ways to build that elevator. Put the actuator *inside* every
> tile — total freedom, every tile can move, but each one gets fat. Or tuck the
> actuator in a bay *next door* — tighter, denser, but only some tiles can move.
> I couldn't eyeball it, so I built a little 3D tool to compare them side by side.
> [Which you picked, and why.]"

**S11 — Making them all move**
> "Knowing the design is one thing. Making [N] of them is another. [The print
> farm / laser cutter / calibration grind.] Every servo had to learn its own
> floor, wall, and hole positions by hand."

**S12 — Invisible traps**
> "But raising walls is the obvious trap. Here's the sneaky one. Under the floor,
> right at the edge of each pit, there's an electromagnet. The marble is steel. So
> the maze can just... reach up through the floor and *pull* — a gentle nudge you
> can fight, or a hard yank you can't."

**S13 — Giving it eyes**
> "And to know when to pull, it has to see. A camera looks straight down, OpenCV
> finds the marble [X] times a second, and the maze knows exactly where you are —
> and where you're about to be. That's when the traps stop being random and start
> feeling personal."

**S14 — Low point**
> "And then [the real low point]. [What broke, how it felt, the moment you weren't
> sure it'd be ready.]"

**S15 — The push**
> "So I did what every maker does with a deadline. [The all-nighter / the final
> integration.] Electronics into the base, the maze stack on top, power on — and
> hold my breath."

**S16 — First full run**
> "[React to the first clean run.] It tilts. The walls move. The traps fire. The
> marble drops. It... actually works."

**S17 — The reveal**
> "And then I put it on a table at OpenSauce, and I let go." *(let visitor footage
> and sound carry)* "[What it was like watching strangers fight your machine.]"

**S18 — Close**
> "It started as a childhood toy that I wanted to make impossible. [Sign-off,
> what's next, thanks for watching.]"
