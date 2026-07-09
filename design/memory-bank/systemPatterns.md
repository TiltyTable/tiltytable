# System Patterns

## Workspace Architecture

This workspace is rooted directly at `marble-maze/`.

```text
marble-maze/
├── index.html
├── grid-comparison.html
├── app/
│   ├── main.js
│   ├── store.js
│   ├── models/
│   ├── render/
│   └── views/
├── styles/app.css
├── memory-bank/
└── .cursor/rules/
```

## Architecture Pattern
- Shared shell, separate views.
- Separate model and renderer per architecture.
- Thin shared infrastructure only for app shell, store orchestration, CSS theme, and Three.js bootstrapping.

## Grid Logic Rules
- Vertical Actuator: dense editable interior grid; border cells are fixed.
- Horizontal Actuator: simulator-defined regular placement of fixed wall and fixed floor cells; only legal mixed-parity slots are configurable.

## Workflow Patterns
- Keep memory-bank updates concise and factual.
- Treat legacy docs as reference material, not canonical software behavior.
- Prefer modular ES files over returning to single-file monoliths.
