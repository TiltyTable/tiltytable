# Progress

## Completed
- [x] Defined the redesign direction around Vertical Actuator and Horizontal Actuator.
- [x] Chose the simulator regularity as the Horizontal Actuator source of truth.
- [x] Chose open floor, actuated blocker, and hole as the Vertical Actuator tile states.
- [x] Scoped `marble-maze/` as its own project workspace with memory bank support.
- [x] Rebuilt the app as a modular ES-module workspace rooted at `index.html`.
- [x] Added separate Vertical Actuator and Horizontal Actuator views with independent settings, grids, and stats.
- [x] Added separate Three.js scene builders to explain the integrated vertical package versus the adjacent horizontal actuator bay.
- [x] Seeded the memory bank, README, and Cursor rule for the standalone `marble-maze/` workspace.
- [x] Verified that the app serves over HTTP and loads its ES-module graph without browser console errors.
- [x] Removed the remaining legacy frame-adjective naming from the workspace and replaced the old reference docs with Vertical/Horizontal architecture docs.
- [x] Improved the 3D scenes with reveal corners, clearer actuator housings, and stronger horizontal rail structure.
- [x] Re-ran manual browser QA on the fresh code and confirmed independent slider behavior for both architectures, view switching, and clean browser console output.
- [x] Removed the fog-heavy 3D shadowing, added reference marbles, and made module joins pack continuously so the internal module boundaries are less noticeable.
- [x] Added hover/focus help affordances for every exposed control variable.
- [x] Re-ran browser QA after the render/control update and confirmed clean console output plus independent slider updates on both views after reload.
- [x] Removed the broken cross-section feature from the live UI.
- [x] Renamed the control variables to be more explicit and widened the range of every exposed configuration variable.
- [x] Normalized the Horizontal Actuator 3D slot states so they share the same visible XY footprint.
- [x] Replaced the unreliable `?` tooltip behavior with always-visible helper text under each control and verified it visually in the browser.
- [x] Expanded the slider ranges substantially in both tabs and verified the new live max bounds in the browser after reload.
- [x] Reworked the Vertical Actuator tab into a preset-led derived calculator for blocker height, pocket depth, pinion sizing, and stroke margin.
- [x] Added standard top / side / isometric 3D view buttons to the viewer controls.
- [x] Fixed the vertical pinion control so auto-fit mode displays the effective fitted pitch diameter instead of the raw sentinel request value.
- [x] Added a visible `Randomize Maze` action to the top-down panels.
- [x] Hardened the randomize action so it guarantees a changed layout and shows a visible shuffle counter.
- [x] Replaced the fake vertical randomizer with a real valid-maze generator that guarantees a safe route plus avoidable trap holes on branch paths.
- [x] Added vertical start/end metadata and surfaced basic maze-generation stats in the vertical view.
- [x] Verified in code that repeated vertical maze generation stays solvable, keeps holes off the protected route, and changes the grid state on randomize.
- [x] Improved the vertical endpoint generator so start/end vary across multiple strong perimeter anchor pairs instead of repeating the same route.
- [x] Redesigned the vertical generator around a richer static skeleton plus explicit dynamic trap phases and phase-aware path validation.
- [x] Added top-down phase preview controls, dynamic trap badges, and richer difficulty metrics to the vertical page.
- [x] Updated the vertical 3D scene so dynamic trap cells are visually distinct and phase-aware.
- [x] Final validation confirmed the advanced generator consistently produces solvable mazes with substantial turns, branches, dynamic traps, and route deception.
- [x] Added route-portfolio generation so the vertical puzzle now surfaces safer vs riskier route candidates instead of a single intended path.
- [x] Added visible blue reward tiles and yellow bonus-time tiles to the generated vertical mazes and surfaced route tradeoff summaries in the UI.
- [x] Validated that generated route families differ in reward and trap exposure rather than collapsing into the same strategic choice.

## In Progress
- [ ] Refine the first-pass interaction and visual polish after using the new workspace for a longer review cycle.

## Next Steps (Not Yet Started)
- [ ] Consider whether `fbd-visualization.html` and `wall-actuator-calc.html` should eventually join the same shell.
- [ ] Consider adding richer 3D labels, animated motion states, or explicit cutaways if the current static scenes are not explanatory enough.
- [ ] Decide whether the new architecture docs should remain concise or grow into fuller engineering references.
- [ ] Decide whether to introduce a stronger browser automation stack for canvas-targeted QA if the current browser MCP remains limited there.

## Known Issues
- Browser validation still requires a local HTTP server because the app uses native modules.
