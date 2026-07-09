# Active Context

## Current Focus
Polish and extend the new modular workspace after landing the first pass of the Vertical Actuator and Horizontal Actuator architecture explorer.

## Recent Changes
- 2026-04-12 | Planned the redesign around Vertical Actuator vs Horizontal Actuator.
- 2026-04-12 | Decided that Horizontal Actuator should use the simulator's fixed-floor and fixed-wall grid rules as the source of truth.
- 2026-04-12 | Decided that Vertical Actuator should support open floor, actuated blocker, and hole tile states.
- 2026-04-12 | Promoted `marble-maze/` to its own scaffolded workspace with memory bank and Cursor rules.
- 2026-04-12 | Replaced the old single-file comparison tool with a modular `index.html` app shell, dedicated models/views/renderers, and a legacy redirect shim.
- 2026-04-12 | Added separate 3D scene builders for Vertical Actuator and Horizontal Actuator, plus independent settings and stats panels for each view.
- 2026-04-12 | Seeded the memory bank and README so future sessions can treat `marble-maze/` as a self-contained project workspace.
- 2026-04-12 | Removed the remaining legacy frame-adjective language and replaced the old reference docs with architecture-specific Vertical/Horizontal docs.
- 2026-04-12 | Improved the 3D scenes with stronger packaging cues: ghosted reveal corners for cutaway-style inspection, richer actuator housings, and clearer horizontal rail structure.
- 2026-04-12 | Ran a fresh manual browser QA pass after reload: confirmed view switching, independent slider updates for both architectures, no legacy frame-adjective terminology, and no browser console errors.
- 2026-04-12 | Removed the fog-heavy 3D look, added reference marbles to both scenes, added hover help on every control variable, and changed the packing math so module joins no longer create visible internal gaps.
- 2026-04-12 | Re-ran browser QA after the render/control pass: verified tooltip/help affordances, clean console output, and independent slider updates on both architecture views after reload.
- 2026-04-12 | Removed the broken cross-section feature from the live UI instead of keeping a misleading diagram.
- 2026-04-12 | Renamed the config variables to be more explicit, widened all exposed control ranges, and added stronger explanatory notes for how the key dimensions relate.
- 2026-04-12 | Normalized the Horizontal Actuator 3D slot states so open, wall, hole, and actuated cells share the same visible XY footprint.
- 2026-04-12 | Replaced the unreliable `?` tooltip behavior with always-visible helper text under each control and verified it visually in the browser.
- 2026-04-12 | Expanded the config slider ranges substantially in both tabs and verified the new live max bounds in the browser DOM after reload.
- 2026-04-12 | Reworked the Vertical Actuator tab into a preset-led mechanism calculator using 75 / 60 / 50 tile presets, derived blocker/pocket stroke math, and max-fit pinion sizing.
- 2026-04-12 | Added standard 3D camera presets (top, side, isometric) to the viewer controls and updated the vertical reveal cell to reflect the derived pinion / rack assumptions more directly.
- 2026-04-12 | Fixed the vertical pinion control so the UI now shows the effective fitted pitch diameter in auto-fit mode instead of the raw sentinel request value.
- 2026-04-12 | Added a visible `Randomize Maze` action to the top-down panel for both architectures and confirmed it appears in the live app.
- 2026-04-12 | Hardened maze randomization so each click guarantees a changed layout and added a visible shuffle counter to the randomize action itself.
- 2026-04-12 | Replaced the fake vertical randomizer with a real valid-maze generator: guaranteed safe start-to-end route, branch regions, and avoidable trap holes off the protected path.
- 2026-04-12 | Added vertical maze metadata to state and surfaced start/end, path length, branch count, and trap count in the vertical view.
- 2026-04-12 | Verified the generator in code across repeated runs: safe path always exists, holes stay off the guaranteed route, and randomize always changes the vertical grid state.
- 2026-04-12 | Improved vertical endpoint selection so the generator picks among several strong perimeter anchor pairs instead of overusing the same corner-to-corner route.
- 2026-04-12 | Redesigned the vertical maze generator around a denser static skeleton plus a dynamic trap-phase layer, with phase-aware validation over `(cell, phase)` states instead of static `OPEN`-only BFS.
- 2026-04-12 | Added phase preview controls, dynamic trap badges in the top-down view, richer difficulty stats, and dynamic trap highlighting in the vertical 3D scene.
- 2026-04-12 | Final validation pass for the advanced generator showed strong variety with no invalid mazes and nontrivial averages for turns, branches, trap cells, and deception delta.
- 2026-04-12 | Reframed the vertical puzzle around visible route tradeoffs: multiple route candidates, blue reward tiles, yellow bonus-time tiles, and route summaries that compare safer vs riskier plans.
- 2026-04-12 | Added route-portfolio generation, reward/time annotations, and route scoring to the vertical state and UI, while keeping it as a planning tool rather than a full physics sim.
- 2026-04-12 | Code-level validation now confirms two route families are present with distinct reward and trap-exposure profiles in generated vertical levels.

## Open Questions
- Whether the other HTML tools should eventually move into the same shared shell or remain independent utilities.
- Whether the first-pass 3D scenes should gain more explicit cutaways, labels, or animation to explain actuator travel.
- Whether canvas-specific QA should move to a stronger automation stack later, since the current browser MCP can validate DOM interactions reliably but is weaker for canvas-targeted clicks.

## Next Session Should
- Tune the first-pass rendering and interaction details after more hands-on review in the browser.
- Decide whether to keep `grid-comparison.html` as a long-term redirect or retire it once users fully move to `index.html`.
- Decide whether the new architecture docs should grow into fuller engineering references or stay concise.
- Consider adding labels or animated motion states to the 3D scenes now that the cutaway-style reveal structure is in place.
- Consider whether the control help should evolve from `title` tooltips into richer inline descriptions or a collapsible glossary.
