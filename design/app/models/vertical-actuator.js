import { FRAME_OUTER, MODULES, formatMm, formatPercent } from '../constants.js';

export const VERTICAL_CELL = Object.freeze({ OPEN: 0, BLOCKER: 1, HOLE: 2, BORDER: 3 });
export const VERTICAL_PRESETS = Object.freeze([75, 60, 50]);
export const VERTICAL_PHASE_COUNT = 3;
export const VERTICAL_TRAP_PATTERNS = Object.freeze({
  pulseFloor: {
    id: 'pulseFloor',
    label: 'Pulse Floor',
    short: 'P',
    states: [VERTICAL_CELL.OPEN, VERTICAL_CELL.HOLE, VERTICAL_CELL.OPEN],
  },
  pulseGate: {
    id: 'pulseGate',
    label: 'Pulse Gate',
    short: 'G',
    states: [VERTICAL_CELL.BLOCKER, VERTICAL_CELL.OPEN, VERTICAL_CELL.BLOCKER],
  },
  collapseGate: {
    id: 'collapseGate',
    label: 'Collapse Gate',
    short: 'C',
    states: [VERTICAL_CELL.BLOCKER, VERTICAL_CELL.OPEN, VERTICAL_CELL.HOLE],
  },
  timedBridge: {
    id: 'timedBridge',
    label: 'Timed Bridge',
    short: 'B',
    states: [VERTICAL_CELL.OPEN, VERTICAL_CELL.BLOCKER, VERTICAL_CELL.OPEN],
  },
});

export const VERTICAL_DEFAULTS = Object.freeze({
  tileSize: 75,
  borderThickness: 4,
  movingWallThickness: 4,
  marbleDiameter: 40,
  blockerFactor: 0.55,
  pocketFactor: 0.5,
  servoAngleRangeDeg: 180,
  usableStrokeFactor: 0.85,
  gearClearance: 4,
  pinionPitchDiameter: 999,
  pinionAutoFit: true,
  moduleSize: 300,
});

export function isVerticalBorder(row, col, size) {
  return row === 0 || col === 0 || row === size - 1 || col === size - 1;
}

export function computeVerticalModel(inputs) {
  const tileSize = Math.max(inputs.tileSize, 1);
  const outerMovingSize = Math.max(0, tileSize - 2 * inputs.borderThickness);
  const innerCavitySize = Math.max(0, outerMovingSize - 2 * inputs.movingWallThickness);
  const recommendedBallDiameter = outerMovingSize * 0.6;
  const blockerHeight = inputs.marbleDiameter * inputs.blockerFactor;
  const pocketDepth = inputs.marbleDiameter * inputs.pocketFactor;
  const requiredStroke = blockerHeight + pocketDepth;
  const maxPinionPitchDiameter = Math.max(0, innerCavitySize - 2 * inputs.gearClearance);
  const requestedPinionPitchDiameter = inputs.pinionAutoFit ? maxPinionPitchDiameter : inputs.pinionPitchDiameter;
  const selectedPinionPitchDiameter = Math.min(requestedPinionPitchDiameter, maxPinionPitchDiameter);
  const theoreticalStroke = Math.PI * selectedPinionPitchDiameter * (inputs.servoAngleRangeDeg / 360);
  const usableStroke = theoreticalStroke * inputs.usableStrokeFactor;
  const strokeMargin = usableStroke - requiredStroke;
  const mechanismDepth = Math.max(
    selectedPinionPitchDiameter + inputs.movingWallThickness * 2,
    requiredStroke + inputs.borderThickness * 2,
    inputs.marbleDiameter + inputs.borderThickness * 2,
  );
  const cellsPerModule = Math.max(2, Math.floor(inputs.moduleSize / tileSize));
  const size = cellsPerModule * MODULES;
  const packedSpan = size * tileSize;
  const actualModuleSpan = cellsPerModule * tileSize;
  const remainder = inputs.moduleSize - actualModuleSpan;
  const gridExtent = inputs.moduleSize * MODULES;
  const rim = (FRAME_OUTER - gridExtent) / 2;
  const globalInset = Math.max(0, (gridExtent - packedSpan) / 2);
  const clearance = (outerMovingSize - inputs.marbleDiameter) / 2;
  const totalDepth = blockerHeight + mechanismDepth + 8;
  const warnings = [];

  if (outerMovingSize <= 0) warnings.push('Border thickness fully consumes the tile envelope. Reduce border thickness or increase tile size.');
  if (innerCavitySize <= 0) warnings.push('Moving wall thickness fully consumes the interior cavity. Reduce wall thickness or increase tile size.');
  if (clearance < 1) warnings.push(`Marble clearance is only ${formatMm(clearance)} per side inside the moving element. The ball may bind during aggressive transitions.`);
  if (inputs.marbleDiameter > outerMovingSize) warnings.push('Marble diameter exceeds the moving wall opening. The ball will not fit inside the tile.');
  if (maxPinionPitchDiameter <= 0) warnings.push('No viable pinion pitch diameter fits inside the current cavity and clearance budget.');
  if (!inputs.pinionAutoFit && inputs.pinionPitchDiameter > maxPinionPitchDiameter && maxPinionPitchDiameter > 0) warnings.push(`Pinion pitch diameter was clamped to the current max-fit value of ${formatMm(maxPinionPitchDiameter)}.`);
  if (strokeMargin < 0) warnings.push(`Usable stroke falls short by ${formatMm(Math.abs(strokeMargin))}. Reduce travel demand or increase the pitch diameter / servo angle budget.`);
  if (blockerHeight < inputs.marbleDiameter * 0.5) warnings.push(`Blocker height is below 50% of the marble diameter. Raise blocker factor if reliable blocking is needed.`);
  if (pocketDepth < inputs.marbleDiameter * 0.45) warnings.push(`Pocket depth is shallower than about half the marble diameter. The ball may not commit to the pocket cleanly.`);
  if (selectedPinionPitchDiameter > innerCavitySize * 0.8) warnings.push('Pinion pitch diameter is using most of the cavity width. Packaging tolerance will be tight.');
  if (remainder > 0.1) warnings.push(`Each ${formatMm(inputs.moduleSize, 0)} module leaves ${formatMm(remainder)} of unused XY slack after packing ${cellsPerModule} dense cells.`);
  if (rim < 6) warnings.push(`Perimeter rim is only ${formatMm(rim)}. Border stiffness may be too low for a dense all-actuated field.`);
  if (gridExtent > FRAME_OUTER) warnings.push('The packed modules exceed the fixed 36 inch frame envelope.');

  return {
    ...inputs,
    presetLabel: VERTICAL_PRESETS.includes(tileSize) ? `${tileSize}mm preset` : 'custom',
    tileSize,
    outerMovingSize,
    innerCavitySize,
    recommendedBallDiameter,
    blockerHeight,
    pocketDepth,
    requiredStroke,
    maxPinionPitchDiameter,
    requestedPinionPitchDiameter,
    selectedPinionPitchDiameter,
    theoreticalStroke,
    usableStroke,
    strokeMargin,
    mechanismDepth,
    cellsPerModule,
    size,
    packedSpan,
    actualModuleSpan,
    remainder,
    gridExtent,
    rim,
    globalInset,
    clearance,
    totalDepth,
    desc: 'The vertical calculator now starts from the tile envelope preset, then derives blocker travel, pocket depth, and max-fit pinion stroke from the moving-element geometry.',
    topNote: 'All interior tiles are editable. Click a tile to cycle open floor, actuated blocker, and hole. The 75 / 60 / 50 presets are intended as the starting point for the mechanism calculator.',
    sectionNote: `Derived with blocker factor ${formatPercent(inputs.blockerFactor)} and pocket factor ${formatPercent(inputs.pocketFactor)}. The pinion defaults to the largest pitch diameter that fits the cavity.`,
    warnings,
  };
}

function isInterior(row, col, size) {
  return row > 0 && col > 0 && row < size - 1 && col < size - 1;
}

function coordKey(row, col) {
  return `${row},${col}`;
}

function neighbors4(row, col, size) {
  return [
    { row: row - 1, col },
    { row: row + 1, col },
    { row, col: col - 1 },
    { row, col: col + 1 },
  ].filter((candidate) => isInterior(candidate.row, candidate.col, size));
}

function shuffle(items) {
  const copy = [...items];
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [copy[index], copy[swapIndex]] = [copy[swapIndex], copy[index]];
  }
  return copy;
}

function cloneGrid(grid) {
  return grid.map((row) => [...row]);
}

function dynamicTrapMap(dynamicTraps = []) {
  const map = new Map();
  for (const trap of dynamicTraps) map.set(coordKey(trap.row, trap.col), trap);
  return map;
}

function trapStateAt(trap, phase) {
  const sequence = trap.states ?? VERTICAL_TRAP_PATTERNS[trap.patternId]?.states ?? [];
  if (!sequence.length) return VERTICAL_CELL.OPEN;
  return sequence[((phase % sequence.length) + sequence.length) % sequence.length];
}

function baseStateAt(baseGrid, dynamicMap, row, col, phase) {
  const trap = dynamicMap.get(coordKey(row, col));
  if (trap) return trapStateAt(trap, phase);
  return baseGrid[row][col];
}

export function resolveVerticalDisplayGrid(model, baseGrid, dynamicTraps = [], phase = 0) {
  const dynamicMap = dynamicTrapMap(dynamicTraps);
  const resolved = [];
  for (let row = 0; row < model.size; row += 1) {
    resolved[row] = [];
    for (let col = 0; col < model.size; col += 1) {
      resolved[row][col] = isVerticalBorder(row, col, model.size)
        ? VERTICAL_CELL.BORDER
        : baseStateAt(baseGrid, dynamicMap, row, col, phase);
    }
  }
  return resolved;
}

function buildPath(model, grid, start, end) {
  if (!start || !end) return [];
  const queue = [start];
  const parents = new Map();
  const visited = new Set([coordKey(start.row, start.col)]);
  while (queue.length) {
    const current = queue.shift();
    if (current.row === end.row && current.col === end.col) break;
    for (const next of neighbors4(current.row, current.col, model.size)) {
      const nextKey = coordKey(next.row, next.col);
      if (visited.has(nextKey)) continue;
      if (grid[next.row][next.col] !== VERTICAL_CELL.OPEN) continue;
      visited.add(nextKey);
      parents.set(nextKey, current);
      queue.push(next);
    }
  }
  if (!visited.has(coordKey(end.row, end.col))) return [];
  const path = [];
  let cursor = end;
  while (cursor) {
    path.push(cursor);
    cursor = parents.get(coordKey(cursor.row, cursor.col));
  }
  path.reverse();
  return path;
}

function countTurns(path) {
  if (!path || path.length < 3) return 0;
  let turns = 0;
  for (let index = 2; index < path.length; index += 1) {
    const prev = path[index - 2];
    const mid = path[index - 1];
    const next = path[index];
    const dx1 = mid.col - prev.col;
    const dy1 = mid.row - prev.row;
    const dx2 = next.col - mid.col;
    const dy2 = next.row - mid.row;
    if (dx1 !== dx2 || dy1 !== dy2) turns += 1;
  }
  return turns;
}

function pathSet(path) {
  return new Set(path.map((cell) => coordKey(cell.row, cell.col)));
}

function getPerimeterAnchors(model) {
  const anchors = [];
  for (let col = 1; col < model.size - 1; col += 1) {
    anchors.push({ row: 1, col, side: 'top' });
    anchors.push({ row: model.size - 2, col, side: 'bottom' });
  }
  for (let row = 2; row < model.size - 2; row += 1) {
    anchors.push({ row, col: 1, side: 'left' });
    anchors.push({ row, col: model.size - 2, side: 'right' });
  }
  return anchors;
}

function getDefaultEndpoints(model) {
  const center = Math.floor(model.size / 2);
  return {
    start: { row: center, col: 1 },
    end: { row: center, col: model.size - 2 },
  };
}

function routeDivergence(pathA, pathB) {
  const setA = pathSet(pathA);
  const setB = pathSet(pathB);
  let overlap = 0;
  for (const key of setA) if (setB.has(key)) overlap += 1;
  return 1 - overlap / Math.max(setA.size, setB.size, 1);
}

function chooseMazeEndpoints(model, openGrid, previousMeta = {}) {
  const anchors = getPerimeterAnchors(model).filter((anchor) => openGrid[anchor.row][anchor.col] === VERTICAL_CELL.OPEN);
  const previousStartKey = previousMeta.start ? coordKey(previousMeta.start.row, previousMeta.start.col) : null;
  const previousEndKey = previousMeta.end ? coordKey(previousMeta.end.row, previousMeta.end.col) : null;
  const pairs = [];
  for (let i = 0; i < anchors.length; i += 1) {
    for (let j = i + 1; j < anchors.length; j += 1) {
      const a = anchors[i];
      const b = anchors[j];
      if (a.side === b.side) continue;
      const path = buildPath(model, openGrid, a, b);
      if (!path.length) continue;
      const turns = countTurns(path);
      const distance = Math.abs(a.row - b.row) + Math.abs(a.col - b.col);
      const previousPenalty = (
        previousStartKey === coordKey(a.row, a.col) && previousEndKey === coordKey(b.row, b.col)
      ) || (
        previousStartKey === coordKey(b.row, b.col) && previousEndKey === coordKey(a.row, a.col)
      ) ? 5 : 0;
      pairs.push({
        start: { row: a.row, col: a.col },
        end: { row: b.row, col: b.col },
        path,
        score: path.length * 1.8 + turns * 3 + distance * 0.35 + Math.random() * 0.8 - previousPenalty,
      });
    }
  }
  if (!pairs.length) return null;
  pairs.sort((a, b) => b.score - a.score);
  return pairs.slice(0, Math.min(12, pairs.length));
}

function createBlockedVerticalBaseGrid(model) {
  const next = [];
  for (let row = 0; row < model.size; row += 1) {
    next[row] = [];
    for (let col = 0; col < model.size; col += 1) {
      next[row][col] = isVerticalBorder(row, col, model.size) ? VERTICAL_CELL.BORDER : VERTICAL_CELL.BLOCKER;
    }
  }
  return next;
}

function countBlockedNeighbors(grid, row, col, size) {
  return neighbors4(row, col, size).filter((candidate) => grid[candidate.row][candidate.col] === VERTICAL_CELL.BLOCKER).length;
}

function growStaticSkeleton(model, previousMeta = {}) {
  const interiorCells = Math.max(1, (model.size - 2) * (model.size - 2));
  const targetOpenCount = Math.max(model.size * 2, Math.floor(interiorCells * 0.62));
  const anchors = getPerimeterAnchors(model);
  const previousStartKey = previousMeta.start ? coordKey(previousMeta.start.row, previousMeta.start.col) : null;
  const startPool = shuffle(anchors).sort((a, b) => {
    const aPenalty = previousStartKey === coordKey(a.row, a.col) ? 2 : 0;
    const bPenalty = previousStartKey === coordKey(b.row, b.col) ? 2 : 0;
    return aPenalty - bPenalty + Math.random() * 0.5;
  });
  const start = { row: startPool[0].row, col: startPool[0].col };
  const baseGrid = createBlockedVerticalBaseGrid(model);
  const visited = new Set([coordKey(start.row, start.col)]);
  const stack = [start];
  baseGrid[start.row][start.col] = VERTICAL_CELL.OPEN;

  while (stack.length && visited.size < targetOpenCount) {
    const current = stack[stack.length - 1];
    const options = neighbors4(current.row, current.col, model.size)
      .filter((candidate) => !visited.has(coordKey(candidate.row, candidate.col)))
      .map((candidate) => {
        const openness = countBlockedNeighbors(baseGrid, candidate.row, candidate.col, model.size);
        const centerBias = Math.abs(candidate.row - model.size / 2) + Math.abs(candidate.col - model.size / 2);
        return { candidate, score: openness * 1.8 - centerBias * 0.08 + Math.random() * 1.4 };
      })
      .sort((a, b) => b.score - a.score);

    if (!options.length) {
      stack.pop();
      continue;
    }
    const pickPool = options.slice(0, Math.min(3, options.length));
    const next = pickPool[Math.floor(Math.random() * pickPool.length)].candidate;
    visited.add(coordKey(next.row, next.col));
    baseGrid[next.row][next.col] = VERTICAL_CELL.OPEN;
    stack.push(next);
  }
  return baseGrid;
}

function addStaticLoops(model, baseGrid, preferredPathLength) {
  const candidates = [];
  for (let row = 1; row < model.size - 1; row += 1) {
    for (let col = 1; col < model.size - 1; col += 1) {
      if (baseGrid[row][col] !== VERTICAL_CELL.BLOCKER) continue;
      const openNeighbors = neighbors4(row, col, model.size).filter((candidate) => baseGrid[candidate.row][candidate.col] === VERTICAL_CELL.OPEN);
      if (openNeighbors.length >= 2) candidates.push({ row, col });
    }
  }
  let loops = 0;
  for (const candidate of shuffle(candidates)) {
    if (loops >= 3) break;
    const trial = cloneGrid(baseGrid);
    trial[candidate.row][candidate.col] = VERTICAL_CELL.OPEN;
    const pairList = chooseMazeEndpoints(model, trial, {}) ?? [];
    const best = pairList[0];
    if (!best || best.path.length - 1 < preferredPathLength - 3) continue;
    baseGrid[candidate.row][candidate.col] = VERTICAL_CELL.OPEN;
    loops += 1;
  }
}

function collectOpenCandidates(model, grid, excludeSet) {
  const cells = [];
  for (let row = 1; row < model.size - 1; row += 1) {
    for (let col = 1; col < model.size - 1; col += 1) {
      const key = coordKey(row, col);
      if (excludeSet.has(key)) continue;
      if (grid[row][col] !== VERTICAL_CELL.OPEN) continue;
      cells.push({ row, col });
    }
  }
  return cells;
}

function choosePatternForPhase(openPhase, preferred) {
  for (const patternId of preferred) {
    const pattern = VERTICAL_TRAP_PATTERNS[patternId];
    if (pattern.states[openPhase % pattern.states.length] === VERTICAL_CELL.OPEN) return patternId;
  }
  return preferred[0];
}

function assignDynamicTraps(model, baseGrid, routeA, routeB) {
  const dynamicTraps = [];
  const routeASet = pathSet(routeA.path);
  const routeBSet = pathSet(routeB.path);
  const shared = new Set([...routeASet].filter((key) => routeBSet.has(key)));
  const routeAOnly = routeA.path.filter((cell) => !shared.has(coordKey(cell.row, cell.col)));
  const routeBOnly = routeB.path.filter((cell) => !shared.has(coordKey(cell.row, cell.col)));

  for (const gate of [routeAOnly[Math.floor(routeAOnly.length * 0.5)], routeBOnly[Math.floor(routeBOnly.length * 0.5)]].filter(Boolean)) {
    const key = coordKey(gate.row, gate.col);
    if (dynamicTraps.some((trap) => coordKey(trap.row, trap.col) === key)) continue;
    const phase = gate.sideBias ?? 0;
    const patternId = choosePatternForPhase(phase, ['collapseGate', 'pulseGate']);
    const pattern = VERTICAL_TRAP_PATTERNS[patternId];
    dynamicTraps.push({ row: gate.row, col: gate.col, patternId, label: pattern.label, short: pattern.short, states: pattern.states, role: 'routeGate' });
  }

  const shortcutCandidates = collectOpenCandidates(model, baseGrid, new Set([...routeASet, ...routeBSet]));
  for (const candidate of shuffle(shortcutCandidates).slice(0, 4)) {
    const pattern = VERTICAL_TRAP_PATTERNS.timedBridge;
    dynamicTraps.push({ row: candidate.row, col: candidate.col, patternId: pattern.id, label: pattern.label, short: pattern.short, states: pattern.states, role: 'shortcut' });
  }

  const branchLeaves = shortcutCandidates.filter((candidate) => {
    const openNeighbors = neighbors4(candidate.row, candidate.col, model.size).filter((next) => baseGrid[next.row][next.col] === VERTICAL_CELL.OPEN);
    return openNeighbors.length <= 1;
  });
  for (const candidate of shuffle(branchLeaves.length ? branchLeaves : shortcutCandidates).slice(0, Math.max(2, Math.floor((branchLeaves.length || shortcutCandidates.length) * 0.35)))) {
    const key = coordKey(candidate.row, candidate.col);
    if (dynamicTraps.some((trap) => coordKey(trap.row, trap.col) === key)) continue;
    const pattern = VERTICAL_TRAP_PATTERNS.pulseFloor;
    dynamicTraps.push({ row: candidate.row, col: candidate.col, patternId: pattern.id, label: pattern.label, short: pattern.short, states: pattern.states, role: 'branchTrap' });
  }

  return dynamicTraps;
}

function buildOptimisticGrid(model, baseGrid, dynamicTraps = []) {
  const optimistic = cloneGrid(baseGrid);
  for (const trap of dynamicTraps) {
    const opensSometimes = trap.states.some((state) => state === VERTICAL_CELL.OPEN);
    optimistic[trap.row][trap.col] = opensSometimes ? VERTICAL_CELL.OPEN : baseGrid[trap.row][trap.col];
  }
  return optimistic;
}

function buildPhaseAwarePath(model, baseGrid, dynamicTraps, start, end, startPhase = 0) {
  const trapMap = dynamicTrapMap(dynamicTraps);
  if (baseStateAt(baseGrid, trapMap, start.row, start.col, startPhase) !== VERTICAL_CELL.OPEN) return [];
  const queue = [{ row: start.row, col: start.col, phase: startPhase }];
  const visited = new Set([`${start.row},${start.col},${startPhase}`]);
  const parents = new Map();
  while (queue.length) {
    const current = queue.shift();
    if (current.row === end.row && current.col === end.col) {
      const path = [];
      let cursor = current;
      while (cursor) {
        path.push(cursor);
        cursor = parents.get(`${cursor.row},${cursor.col},${cursor.phase}`);
      }
      path.reverse();
      return path;
    }
    const nextPhase = (current.phase + 1) % VERTICAL_PHASE_COUNT;
    for (const next of neighbors4(current.row, current.col, model.size)) {
      const nextState = baseStateAt(baseGrid, trapMap, next.row, next.col, nextPhase);
      if (nextState !== VERTICAL_CELL.OPEN) continue;
      const key = `${next.row},${next.col},${nextPhase}`;
      if (visited.has(key)) continue;
      visited.add(key);
      parents.set(key, current);
      queue.push({ row: next.row, col: next.col, phase: nextPhase });
    }
  }
  return [];
}

function countBranchComponents(model, grid, routeSet) {
  const visited = new Set();
  let components = 0;
  for (let row = 1; row < model.size - 1; row += 1) {
    for (let col = 1; col < model.size - 1; col += 1) {
      const key = coordKey(row, col);
      if (visited.has(key) || routeSet.has(key) || grid[row][col] !== VERTICAL_CELL.OPEN) continue;
      components += 1;
      const queue = [{ row, col }];
      visited.add(key);
      while (queue.length) {
        const current = queue.shift();
        for (const next of neighbors4(current.row, current.col, model.size)) {
          const nextKey = coordKey(next.row, next.col);
          if (visited.has(nextKey) || routeSet.has(nextKey) || grid[next.row][next.col] !== VERTICAL_CELL.OPEN) continue;
          visited.add(nextKey);
          queue.push(next);
        }
      }
    }
  }
  return components;
}

function estimateRoute(route, dynamicTraps, rewardTiles, bonusTimeTiles) {
  const rewardMap = new Map((rewardTiles ?? []).map((tile) => [`${tile.row},${tile.col}`, tile]));
  const bonusMap = new Map((bonusTimeTiles ?? []).map((tile) => [`${tile.row},${tile.col}`, tile]));
  let points = 0;
  let bonusTime = 0;
  let trapExposure = 0;
  const trapMap = dynamicTrapMap(dynamicTraps);
  route.path.forEach((cell, index) => {
    const key = coordKey(cell.row, cell.col);
    const reward = rewardMap.get(key);
    const bonus = bonusMap.get(key);
    if (reward) points += reward.points;
    if (bonus) bonusTime += bonus.seconds;
    const trap = trapMap.get(key);
    if (trap) trapExposure += 1 + (trap.role === 'routeGate' ? 2 : 1);
  });
  const estimatedTime = route.pathLength + trapExposure * 1.25 - bonusTime;
  const label = route.intent;
  return {
    id: route.id,
    label,
    pathLength: route.pathLength,
    turns: route.turns,
    trapExposure,
    points,
    bonusTime,
    estimatedTime,
  };
}

function assignRewards(routeA, routeB) {
  const routeASet = pathSet(routeA.path);
  const routeBSet = pathSet(routeB.path);
  const rewardTiles = [];
  const bonusTimeTiles = [];
  for (const cell of routeB.path.filter((candidate) => !routeASet.has(coordKey(candidate.row, candidate.col)))) {
    if (Math.random() < 0.35) rewardTiles.push({ row: cell.row, col: cell.col, color: 'blue', points: 50 });
  }
  for (const cell of routeA.path.filter((candidate) => !routeBSet.has(coordKey(candidate.row, candidate.col)))) {
    if (Math.random() < 0.2) bonusTimeTiles.push({ row: cell.row, col: cell.col, color: 'yellow', seconds: 3 });
  }
  if (!rewardTiles.length && routeB.path.length > 2) {
    const cell = routeB.path[Math.floor(routeB.path.length * 0.6)];
    rewardTiles.push({ row: cell.row, col: cell.col, color: 'blue', points: 50 });
  }
  if (!bonusTimeTiles.length && routeA.path.length > 2) {
    const cell = routeA.path[Math.floor(routeA.path.length * 0.45)];
    bonusTimeTiles.push({ row: cell.row, col: cell.col, color: 'yellow', seconds: 3 });
  }
  return { rewardTiles, bonusTimeTiles };
}

function buildRouteCandidates(model, optimisticGrid, start, end) {
  const primaryPath = buildPath(model, optimisticGrid, start, end);
  if (!primaryPath.length) return [];
  const primary = {
    id: 'A',
    intent: 'safer / lower reward',
    path: primaryPath,
    pathLength: primaryPath.length - 1,
    turns: countTurns(primaryPath),
  };
  const pathInterior = primaryPath.slice(1, -1);
  const alternateCandidates = [];
  for (const cell of pathInterior) {
    const trial = cloneGrid(optimisticGrid);
    trial[cell.row][cell.col] = VERTICAL_CELL.BLOCKER;
    const altPath = buildPath(model, trial, start, end);
    if (!altPath.length) continue;
    const divergence = routeDivergence(primaryPath, altPath);
    if (divergence < 0.22) continue;
    alternateCandidates.push({
      id: 'B',
      intent: 'riskier / higher reward',
      path: altPath,
      pathLength: altPath.length - 1,
      turns: countTurns(altPath),
      divergence,
    });
  }
  alternateCandidates.sort((a, b) => (b.divergence + b.turns * 0.04 + b.pathLength * 0.02) - (a.divergence + a.turns * 0.04 + a.pathLength * 0.02));
  const alternate = alternateCandidates[0];
  return alternate ? [primary, alternate] : [primary];
}

function scoreMaze(meta) {
  const primary = meta.routeCandidates.find((route) => route.id === 'A') || meta.routeCandidates[0];
  const alternate = meta.routeCandidates.find((route) => route.id === 'B');
  const routeBonus = alternate ? (alternate.estimatedTime >= primary.estimatedTime ? 6 : 0) + (alternate.points > primary.points ? 8 : 0) : 0;
  return meta.safePathLength * 1.7 + meta.turnCount * 3.2 + meta.branchCount * 3.5 + meta.dynamicTrapCount * 2.4 + meta.deceptionDelta * 5.5 + routeBonus;
}

export function computeVerticalMazeMeta(model, baseGrid, dynamicTraps = [], rewardTiles = [], bonusTimeTiles = [], previousMeta = {}) {
  const defaults = getDefaultEndpoints(model);
  const start = previousMeta.start && isInterior(previousMeta.start.row, previousMeta.start.col, model.size)
    ? { row: previousMeta.start.row, col: previousMeta.start.col }
    : { row: defaults.start.row, col: defaults.start.col };
  const end = previousMeta.end && isInterior(previousMeta.end.row, previousMeta.end.col, model.size)
    ? { row: previousMeta.end.row, col: previousMeta.end.col }
    : { row: defaults.end.row, col: defaults.end.col };
  const previewPhase = previousMeta.previewPhase ?? 0;
  const phasePath = buildPhaseAwarePath(model, baseGrid, dynamicTraps, start, end, 0);
  const solutionPath = phasePath.map(({ row, col }) => ({ row, col }));
  const optimisticGrid = buildOptimisticGrid(model, baseGrid, dynamicTraps);
  const routeCandidates = buildRouteCandidates(model, optimisticGrid, start, end).map((route) => estimateRoute(route, dynamicTraps, rewardTiles, bonusTimeTiles));
  const routeSet = pathSet(solutionPath);
  const trapCount = dynamicTraps.filter((trap) => trap.states.some((state) => state === VERTICAL_CELL.HOLE)).length;
  return {
    start,
    end,
    previewPhase,
    phaseCount: VERTICAL_PHASE_COUNT,
    dynamicTraps,
    rewardTiles,
    bonusTimeTiles,
    routeCandidates,
    timeBudgetSeconds: 30,
    solutionPath,
    phasePath,
    safePathExists: phasePath.length > 0,
    safePathLength: phasePath.length > 0 ? phasePath.length - 1 : 0,
    turnCount: countTurns(solutionPath),
    branchCount: countBranchComponents(model, optimisticGrid, routeSet),
    trapCount,
    dynamicTrapCount: dynamicTraps.length,
    optimisticPathLength: routeCandidates[0]?.pathLength ?? 0,
    deceptionDelta: routeCandidates.length > 1 ? Math.max(0, routeCandidates[1].pathLength - routeCandidates[0].pathLength) : 0,
  };
}

export function generateValidVerticalMaze(model, previousMeta = {}) {
  let best = null;
  for (let attempt = 0; attempt < 96; attempt += 1) {
    const baseGrid = growStaticSkeleton(model, previousMeta);
    addStaticLoops(model, baseGrid, model.size + 2);
    const endpointPool = chooseMazeEndpoints(model, baseGrid, previousMeta);
    if (!endpointPool || endpointPool.length < 2) continue;
    const primaryCandidate = endpointPool[0];
    const alternateCandidate = endpointPool.find((candidate) => routeDivergence(primaryCandidate.path, candidate.path) > 0.24 && candidate.path.length > primaryCandidate.path.length) ?? endpointPool[1];
    if (!alternateCandidate) continue;

    const dynamicTraps = assignDynamicTraps(model, baseGrid, primaryCandidate, alternateCandidate);
    const rewards = assignRewards(primaryCandidate, alternateCandidate);
    const meta = computeVerticalMazeMeta(model, baseGrid, dynamicTraps, rewards.rewardTiles, rewards.bonusTimeTiles, {
      start: primaryCandidate.start,
      end: primaryCandidate.end,
      previewPhase: 0,
    });

    if (!meta.safePathExists) continue;
    if (meta.turnCount < Math.max(6, Math.floor(meta.safePathLength / 4))) continue;
    if (meta.branchCount < 2) continue;
    if (meta.dynamicTrapCount < 3) continue;
    if (meta.routeCandidates.length < 2) continue;

    const safer = meta.routeCandidates.find((route) => route.id === 'A') || meta.routeCandidates[0];
    const riskier = meta.routeCandidates.find((route) => route.id === 'B') || meta.routeCandidates[1];
    if (!riskier) continue;
    if (riskier.points <= safer.points) continue;
    if (riskier.trapExposure <= safer.trapExposure) continue;

    const score = scoreMaze(meta);
    const candidate = {
      baseGrid,
      dynamicTraps,
      rewardTiles: rewards.rewardTiles,
      bonusTimeTiles: rewards.bonusTimeTiles,
      previewPhase: 0,
      ...meta,
      score,
    };
    if (!best || candidate.score > best.score) best = candidate;
  }

  if (best) return best;
  const defaults = getDefaultEndpoints(model);
  const baseGrid = createBlockedVerticalBaseGrid(model);
  baseGrid[defaults.start.row][defaults.start.col] = VERTICAL_CELL.OPEN;
  baseGrid[defaults.end.row][defaults.end.col] = VERTICAL_CELL.OPEN;
  return {
    baseGrid,
    dynamicTraps: [],
    rewardTiles: [],
    bonusTimeTiles: [],
    previewPhase: 0,
    ...computeVerticalMazeMeta(model, baseGrid, [], [], [], defaults),
    score: 0,
  };
}

export function cycleVerticalCell(baseGrid, row, col, dynamicTraps = [], rewardTiles = [], bonusTimeTiles = []) {
  if (isVerticalBorder(row, col, baseGrid.length)) return { baseGrid, dynamicTraps, rewardTiles, bonusTimeTiles };
  const nextGrid = cloneGrid(baseGrid);
  nextGrid[row][col] = (nextGrid[row][col] + 1) % 3;
  const nextTraps = dynamicTraps.filter((trap) => trap.row !== row || trap.col !== col);
  const nextRewards = rewardTiles.filter((tile) => tile.row !== row || tile.col !== col);
  const nextBonus = bonusTimeTiles.filter((tile) => tile.row !== row || tile.col !== col);
  return { baseGrid: nextGrid, dynamicTraps: nextTraps, rewardTiles: nextRewards, bonusTimeTiles: nextBonus };
}

export function getVerticalCellFrame(model, row, col) {
  const x = model.rim + model.globalInset + col * model.tileSize;
  const y = model.rim + model.globalInset + row * model.tileSize;
  return { x, y, size: model.tileSize, centerX: x + model.tileSize / 2, centerY: y + model.tileSize / 2 };
}

export function computeVerticalStats(model, grid) {
  const counts = { open: 0, blocker: 0, hole: 0 };
  const moduleBreakdown = Array.from({ length: MODULES }, () => Array.from({ length: MODULES }, () => ({ blocker: 0, hole: 0 })));
  for (let row = 0; row < model.size; row += 1) {
    for (let col = 0; col < model.size; col += 1) {
      if (isVerticalBorder(row, col, model.size)) continue;
      const state = grid[row][col];
      const moduleRow = Math.floor(row / model.cellsPerModule);
      const moduleCol = Math.floor(col / model.cellsPerModule);
      if (state === VERTICAL_CELL.BLOCKER) { counts.blocker += 1; moduleBreakdown[moduleRow][moduleCol].blocker += 1; }
      else if (state === VERTICAL_CELL.HOLE) { counts.hole += 1; moduleBreakdown[moduleRow][moduleCol].hole += 1; }
      else counts.open += 1;
    }
  }
  return { ...counts, border: model.size * 4 - 4, interior: Math.max(0, (model.size - 2) * (model.size - 2)), moduleBreakdown };
}
