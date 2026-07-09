import {
  VERTICAL_DEFAULTS,
  computeVerticalMazeMeta,
  computeVerticalModel,
  cycleVerticalCell,
  generateValidVerticalMaze,
  resolveVerticalDisplayGrid,
} from './models/vertical-actuator.js';
import { HORIZONTAL_DEFAULTS, computeHorizontalModel, createHorizontalGrid, cycleHorizontalCell, randomizeHorizontalGrid } from './models/horizontal-actuator.js';

function buildVerticalState(inputs, previousState = {}) {
  const model = computeVerticalModel(inputs);
  const previousModelSize = previousState.model?.size;
  if (!previousState.baseGrid || previousModelSize !== model.size) {
    const generated = generateValidVerticalMaze(model, previousState);
    return {
      inputs: { ...inputs },
      model,
      randomizeCount: previousState.randomizeCount ?? 0,
      ...generated,
      grid: resolveVerticalDisplayGrid(model, generated.baseGrid, generated.dynamicTraps, generated.previewPhase),
    };
  }

  const baseGrid = previousState.baseGrid;
  const dynamicTraps = previousState.dynamicTraps ?? [];
  const rewardTiles = previousState.rewardTiles ?? [];
  const bonusTimeTiles = previousState.bonusTimeTiles ?? [];
  const previewPhase = previousState.previewPhase ?? 0;
  const meta = computeVerticalMazeMeta(model, baseGrid, dynamicTraps, rewardTiles, bonusTimeTiles, previousState);
  return {
    inputs: { ...inputs },
    model,
    baseGrid,
    dynamicTraps,
    rewardTiles,
    bonusTimeTiles,
    previewPhase,
    randomizeCount: previousState.randomizeCount ?? 0,
    ...meta,
    grid: resolveVerticalDisplayGrid(model, baseGrid, dynamicTraps, previewPhase),
  };
}

function buildHorizontalState(inputs, previousGrid, previousMeta = {}) {
  const model = computeHorizontalModel(inputs);
  return { inputs: { ...inputs }, model, grid: createHorizontalGrid(model, previousGrid), randomizeCount: previousMeta.randomizeCount ?? 0 };
}

export function createStore() {
  const listeners = new Set();
  let state = {
    currentView: 'vertical',
    vertical: buildVerticalState(VERTICAL_DEFAULTS, { randomizeCount: 0, previewPhase: 0 }),
    horizontal: buildHorizontalState(HORIZONTAL_DEFAULTS, undefined, { randomizeCount: 0 }),
  };
  const emit = (previous) => listeners.forEach((listener) => listener(state, previous));
  return {
    getState() { return state; },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    setView(view) { if (state.currentView === view) return; const previous = state; state = { ...state, currentView: view }; emit(previous); },
    updateInputs(view, patch) {
      const previous = state;
      state = view === 'vertical'
        ? { ...state, vertical: buildVerticalState({ ...state.vertical.inputs, ...patch }, state.vertical) }
        : { ...state, horizontal: buildHorizontalState({ ...state.horizontal.inputs, ...patch }, state.horizontal.grid, state.horizontal) };
      emit(previous);
    },
    cycleCell(view, row, col) {
      const previous = state;
      if (view === 'vertical') {
        const next = cycleVerticalCell(state.vertical.baseGrid, row, col, state.vertical.dynamicTraps, state.vertical.rewardTiles, state.vertical.bonusTimeTiles);
        const vertical = buildVerticalState(state.vertical.inputs, { ...state.vertical, ...next });
        state = { ...state, vertical };
      } else {
        state = { ...state, horizontal: { ...state.horizontal, grid: cycleHorizontalCell(state.horizontal.grid, row, col) } };
      }
      emit(previous);
    },
    randomizeGrid(view) {
      const previous = state;
      if (view === 'vertical') {
        const generated = generateValidVerticalMaze(state.vertical.model, state.vertical);
        state = {
          ...state,
          vertical: {
            inputs: { ...state.vertical.inputs },
            model: state.vertical.model,
            randomizeCount: (state.vertical.randomizeCount ?? 0) + 1,
            ...generated,
            grid: resolveVerticalDisplayGrid(state.vertical.model, generated.baseGrid, generated.dynamicTraps, generated.previewPhase),
          },
        };
      } else {
        state = {
          ...state,
          horizontal: {
            ...state.horizontal,
            grid: randomizeHorizontalGrid(state.horizontal.model, state.horizontal.grid),
            randomizeCount: (state.horizontal.randomizeCount ?? 0) + 1,
          },
        };
      }
      emit(previous);
    },
    setVerticalPreviewPhase(phase) {
      const previous = state;
      const previewPhase = ((phase % state.vertical.phaseCount) + state.vertical.phaseCount) % state.vertical.phaseCount;
      state = {
        ...state,
        vertical: {
          ...state.vertical,
          previewPhase,
          grid: resolveVerticalDisplayGrid(state.vertical.model, state.vertical.baseGrid, state.vertical.dynamicTraps, previewPhase),
        },
      };
      emit(previous);
    },
  };
}
