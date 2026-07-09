# Technical Context

## Environment
- macOS workspace
- Browser-based app with native ES modules
- Three.js loaded from `esm.sh`
- No bundler or package manager required for the current workspace

## Key File Paths
- Workspace root: `design/`
- App entrypoint: `design/index.html`
- Legacy redirect: `design/grid-comparison.html`
- Memory bank: `design/memory-bank/`
- Cursor rule: `.cursor/rules/memory-bank.mdc`

## Local Serve Command
```sh
cd design
python3 -m http.server 8765
```

## Reference Documents
- `PRD.md`
- `vertical-actuator-architecture.md`
- `horizontal-actuator-architecture.md`
- `fbd-visualization.html`
- `wall-actuator-calc.html`

## Constraints
- The browser tooling available through Cursor cannot open `file://` URLs, so HTTP serving is required for interactive validation.
- There is currently no git repository rooted at `marble-maze/`.
