import { FRAME_OUTER, MODULES, formatMm, formatPercent } from '../constants.js';
import { VERTICAL_DEFAULTS, VERTICAL_PRESETS } from './vertical-actuator.js';

export const HORIZONTAL_CELL = Object.freeze({ OPEN: 0, STATIC_WALL: 1, HOLE: 2, ACTUATED: 3 });
export const HORIZONTAL_PRESETS = VERTICAL_PRESETS;
export const HORIZONTAL_DEFAULTS = Object.freeze({ ...VERTICAL_DEFAULTS });

export function isHorizontalPost(row, col) { return row % 2 === 0 && col % 2 === 0; }
export function isHorizontalPassage(row, col) { return row % 2 === 1 && col % 2 === 1; }
export function isHorizontalPerimeter(row, col, size) { return row === 0 || col === 0 || row === size - 1 || col === size - 1; }
export function isHorizontalConfigurable(row, col, size) { return !isHorizontalPost(row, col) && !isHorizontalPassage(row, col) && !isHorizontalPerimeter(row, col, size); }
export function getHorizontalOrientation(row) { return row % 2 === 0 ? 'row' : 'column'; }
export function getHorizontalCellKey(row, col) { return `${row},${col}`; }

function clampToPositive(value) {
  return Math.max(0, value);
}

function formatHorizontalPresetLabel(tileSize) {
  return HORIZONTAL_PRESETS.includes(tileSize) ? `${tileSize}mm preset` : 'custom';
}

export function computeHorizontalModel(inputs) {
  const tileSize = Math.max(inputs.tileSize, 1);
  const outerMovingSize = Math.max(0, tileSize - 2 * inputs.borderThickness);
  const innerCavitySize = Math.max(0, outerMovingSize - 2 * inputs.movingWallThickness);
  const recommendedBallDiameter = outerMovingSize * 0.6;
  const passageSize = outerMovingSize;
  const separatorWidth = Math.max(0, tileSize - passageSize);
  const pitch = tileSize;
  const cellsPerModule = Math.max(2, Math.floor(inputs.moduleSize / pitch));
  const size = cellsPerModule * MODULES;
  const usedSpan = size * pitch + separatorWidth;
  const actualModuleSpan = cellsPerModule * pitch;
  const remainder = inputs.moduleSize - actualModuleSpan;
  const gridExtent = inputs.moduleSize * MODULES;
  const rim = (FRAME_OUTER - gridExtent) / 2;
  const globalInset = Math.max(0, (gridExtent - usedSpan) / 2);
  const clearance = (outerMovingSize - inputs.marbleDiameter) / 2;
  const blockerHeight = inputs.marbleDiameter * inputs.blockerFactor;
  const pocketDepth = inputs.marbleDiameter * inputs.pocketFactor;
  const requiredStroke = blockerHeight + pocketDepth;
  const outerHousingSize = outerMovingSize;
  const maxPinionPitchDiameter = clampToPositive(outerHousingSize - inputs.gearClearance * 2);
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
  const serviceDepth = Math.max(12, mechanismDepth - 4);
  const totalDepth = blockerHeight + mechanismDepth + 8;
  const warnings = [];

  if (outerMovingSize <= 0) warnings.push('Border thickness fully consumes the tile envelope. Reduce border thickness or increase tile size.');
  if (innerCavitySize <= 0) warnings.push('Moving wall thickness fully consumes the interior cavity. Reduce wall thickness or increase tile size.');
  if (inputs.marbleDiameter <= separatorWidth) warnings.push('Marble diameter must stay larger than the structural separator width or the ball can ride the fixed lattice instead of entering the moving slots.');
  if (clearance < 1) warnings.push(`Opening clearance is only ${formatMm(clearance)} per side inside the moving element. The ball may bind during aggressive transitions.`);
  if (inputs.marbleDiameter > outerMovingSize) warnings.push('Marble diameter exceeds the moving wall opening. The ball will not fit inside the tile.');
  if (maxPinionPitchDiameter <= 0) warnings.push('No viable pinion pitch diameter fits inside the current outer slider envelope and clearance budget.');
  if (!inputs.pinionAutoFit && inputs.pinionPitchDiameter > maxPinionPitchDiameter && maxPinionPitchDiameter > 0) warnings.push(`Pinion pitch diameter was clamped to the current max-fit value of ${formatMm(maxPinionPitchDiameter)}.`);
  if (strokeMargin < 0) warnings.push(`Usable stroke falls short by ${formatMm(Math.abs(strokeMargin))}. Reduce travel demand or increase the pitch diameter / servo angle budget.`);
  if (blockerHeight < inputs.marbleDiameter * 0.5) warnings.push(`Blocker height is below 50% of the marble diameter. Raise blocker factor if reliable blocking is needed.`);
  if (pocketDepth < inputs.marbleDiameter * 0.45) warnings.push(`Pocket depth is shallower than about half the marble diameter. The ball may not commit to the pocket cleanly.`);
  if (selectedPinionPitchDiameter > outerHousingSize * 0.8) warnings.push('Pinion pitch diameter is using most of the outer slider envelope. Packaging tolerance will be tight.');
  if (remainder > 0.1) warnings.push(`Each ${formatMm(inputs.moduleSize, 0)} module leaves ${formatMm(remainder)} of unused XY slack after packing ${cellsPerModule} regular cells.`);
  if (rim < 4) warnings.push(`Perimeter rim is only ${formatMm(rim)}. The frame is being kept very thin, so border stiffness may become the limiting factor.`);
  if (gridExtent > FRAME_OUTER) warnings.push('The packed modules exceed the fixed 36 inch frame envelope.');

  return {
    ...inputs,
    presetLabel: formatHorizontalPresetLabel(tileSize),
    tileSize,
    borderThickness: inputs.borderThickness,
    movingWallThickness: inputs.movingWallThickness,
    outerMovingSize,
    innerCavitySize,
    recommendedBallDiameter,
    passageSize,
    separatorWidth,
    pitch,
    cellsPerModule,
    size,
    usedSpan,
    actualModuleSpan,
    remainder,
    gridExtent,
    rim,
    globalInset,
    clearance,
    blockerHeight,
    pocketDepth,
    requiredStroke,
    outerHousingSize,
    maxPinionPitchDiameter,
    requestedPinionPitchDiameter,
    selectedPinionPitchDiameter,
    theoreticalStroke,
    usableStroke,
    strokeMargin,
    mechanismDepth,
    serviceDepth,
    totalDepth,
    desc: 'The horizontal calculator now starts from the same tile envelope inputs as Vertical, then derives blocker travel, pocket depth, and a larger max-fit pinion stroke by sizing the pinion against the outer slider envelope instead of the inner cavity.',
    topNote: 'Only the mixed-parity slots are editable. Click a slot to cycle open floor, static wall, hole, and actuated blocker. Click a reservation overlay to flip that blocker to the opposite neighboring bay while keeping the same fixed floor / wall tile above it.',
    sectionNote: `Derived with blocker factor ${formatPercent(inputs.blockerFactor)} and pocket factor ${formatPercent(inputs.pocketFactor)}. The pinion defaults to the largest pitch diameter that fits the outer slider envelope, while fixed lattice cells reserve the underfloor bay.`,
    warnings,
  };
}

export function createHorizontalGrid(model, previous = []) {
  const next = [];
  for (let row = 0; row < model.size; row += 1) {
    next[row] = [];
    for (let col = 0; col < model.size; col += 1) {
      const prevValue = previous[row]?.[col];
      next[row][col] = isHorizontalConfigurable(row, col, model.size)
        ? (Number.isInteger(prevValue) ? prevValue : HORIZONTAL_CELL.OPEN)
        : HORIZONTAL_CELL.OPEN;
    }
  }
  return next;
}

function gridsMatch(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  for (let row = 0; row < a.length; row += 1) {
    if (!a[row] || !b[row] || a[row].length !== b[row].length) return false;
    for (let col = 0; col < a[row].length; col += 1) {
      if (a[row][col] !== b[row][col]) return false;
    }
  }
  return true;
}

export function getHorizontalBayCandidates(model, row, col) {
  if (!isHorizontalConfigurable(row, col, model.size)) return [];
  const orientation = getHorizontalOrientation(row);
  const candidates = orientation === 'row'
    ? [
        { row, col: col - 1, flip: -1, label: 'left', orientation },
        { row, col: col + 1, flip: 1, label: 'right', orientation },
      ]
    : [
        { row: row - 1, col, flip: -1, label: 'up', orientation },
        { row: row + 1, col, flip: 1, label: 'down', orientation },
      ];
  return candidates
    .filter((candidate) => candidate.row >= 0 && candidate.col >= 0 && candidate.row < model.size && candidate.col < model.size)
    .filter((candidate) => isHorizontalPost(candidate.row, candidate.col))
    .map((candidate) => ({
      ...candidate,
      key: getHorizontalCellKey(candidate.row, candidate.col),
      perimeter: isHorizontalPerimeter(candidate.row, candidate.col, model.size),
    }));
}

export function getHorizontalDefaultFlip(model, row, col) {
  const candidates = getHorizontalBayCandidates(model, row, col);
  const preferred = candidates.find((candidate) => candidate.flip === 1 && !candidate.perimeter)
    ?? candidates.find((candidate) => !candidate.perimeter)
    ?? candidates.find((candidate) => candidate.flip === 1)
    ?? candidates[0];
  return preferred?.flip ?? 1;
}

export function resolveHorizontalActuators(model, grid, preferredFlips = {}) {
  const nextGrid = grid.map((line) => [...line]);
  const bayAssignments = {};
  const bayOccupancy = {};
  const droppedActuators = [];

  for (let row = 0; row < model.size; row += 1) {
    for (let col = 0; col < model.size; col += 1) {
      if (nextGrid[row][col] !== HORIZONTAL_CELL.ACTUATED) continue;
      const key = getHorizontalCellKey(row, col);
      const candidates = getHorizontalBayCandidates(model, row, col);
      const defaultFlip = getHorizontalDefaultFlip(model, row, col);
      const requestedFlip = preferredFlips[key] === -1 || preferredFlips[key] === 1 ? preferredFlips[key] : defaultFlip;
      const ordered = [
        candidates.find((candidate) => candidate.flip === requestedFlip),
        candidates.find((candidate) => candidate.flip !== requestedFlip),
      ].filter(Boolean);
      const chosen = ordered.find((candidate) => !bayOccupancy[candidate.key]);

      if (!chosen) {
        nextGrid[row][col] = HORIZONTAL_CELL.OPEN;
        droppedActuators.push({ row, col });
        continue;
      }

      bayOccupancy[chosen.key] = key;
      bayAssignments[key] = {
        gateRow: row,
        gateCol: col,
        gateKey: key,
        bayRow: chosen.row,
        bayCol: chosen.col,
        bayKey: chosen.key,
        flip: chosen.flip,
        defaultFlip,
        label: chosen.label,
        orientation: chosen.orientation,
      };
    }
  }

  Object.values(bayAssignments).forEach((assignment) => {
    const candidates = getHorizontalBayCandidates(model, assignment.gateRow, assignment.gateCol);
    const alternative = candidates.find((candidate) => candidate.flip !== assignment.flip) ?? null;
    assignment.alternative = alternative;
    assignment.canFlip = Boolean(alternative && (!bayOccupancy[alternative.key] || bayOccupancy[alternative.key] === assignment.gateKey));
  });

  const bayFlips = Object.fromEntries(Object.values(bayAssignments).map((assignment) => [assignment.gateKey, assignment.flip]));
  const warnings = droppedActuators.length
    ? [`${droppedActuators.length} actuated blocker${droppedActuators.length === 1 ? '' : 's'} were reset to open floor because no adjacent actuator bay remained free.`]
    : [];

  return { grid: nextGrid, bayAssignments, bayFlips, bayOccupancy, droppedActuators, warnings };
}

export function randomizeHorizontalGrid(model, previous = []) {
  const next = [];
  for (let row = 0; row < model.size; row += 1) {
    next[row] = [];
    for (let col = 0; col < model.size; col += 1) {
      if (!isHorizontalConfigurable(row, col, model.size)) {
        next[row][col] = HORIZONTAL_CELL.OPEN;
        continue;
      }
      const roll = Math.random();
      if (roll < 0.34) next[row][col] = HORIZONTAL_CELL.OPEN;
      else if (roll < 0.58) next[row][col] = HORIZONTAL_CELL.STATIC_WALL;
      else if (roll < 0.70) next[row][col] = HORIZONTAL_CELL.HOLE;
      else next[row][col] = HORIZONTAL_CELL.ACTUATED;
    }
  }
  if (gridsMatch(next, previous) && model.size > 2) {
    for (let row = 1; row < model.size - 1; row += 1) {
      for (let col = 1; col < model.size - 1; col += 1) {
        if (isHorizontalConfigurable(row, col, model.size)) {
          next[row][col] = (next[row][col] + 1) % 4;
          return next;
        }
      }
    }
  }
  return next;
}

export function cycleHorizontalCell(grid, row, col) {
  if (!isHorizontalConfigurable(row, col, grid.length)) return grid;
  const next = grid.map((line) => [...line]);
  next[row][col] = (next[row][col] + 1) % 4;
  return next;
}

export function getHorizontalCellFrame(model, row, col) {
  const x = model.rim + model.globalInset + model.separatorWidth + col * model.pitch;
  const y = model.rim + model.globalInset + model.separatorWidth + row * model.pitch;
  return { x, y, size: model.passageSize, centerX: x + model.passageSize / 2, centerY: y + model.passageSize / 2 };
}

export function computeHorizontalStats(model, grid, bayAssignments = {}) {
  let fixedWalls = 0;
  let fixedFloors = 0;
  let perimeterEdges = 0;
  let open = 0;
  let staticWall = 0;
  let hole = 0;
  let actuated = 0;
  let reservedBays = 0;
  let interiorBayCells = 0;
  const moduleBreakdown = Array.from({ length: MODULES }, () => Array.from({ length: MODULES }, () => ({ actuated: 0, hole: 0, reserved: 0 })));
  const reservedBayKeys = new Set(Object.values(bayAssignments).map((assignment) => assignment.bayKey));

  for (let row = 0; row < model.size; row += 1) {
    for (let col = 0; col < model.size; col += 1) {
      const key = getHorizontalCellKey(row, col);
      if (isHorizontalPost(row, col)) {
        fixedWalls += 1;
        if (!isHorizontalPerimeter(row, col, model.size)) interiorBayCells += 1;
        if (reservedBayKeys.has(key)) {
          reservedBays += 1;
          const moduleRow = Math.floor(row / model.cellsPerModule);
          const moduleCol = Math.floor(col / model.cellsPerModule);
          moduleBreakdown[moduleRow][moduleCol].reserved += 1;
        }
      } else if (isHorizontalPassage(row, col)) fixedFloors += 1;
      else if (isHorizontalPerimeter(row, col, model.size)) perimeterEdges += 1;
      else {
        const state = grid[row][col];
        const moduleRow = Math.floor(row / model.cellsPerModule);
        const moduleCol = Math.floor(col / model.cellsPerModule);
        if (state === HORIZONTAL_CELL.STATIC_WALL) staticWall += 1;
        else if (state === HORIZONTAL_CELL.HOLE) {
          hole += 1;
          moduleBreakdown[moduleRow][moduleCol].hole += 1;
        } else if (state === HORIZONTAL_CELL.ACTUATED) {
          actuated += 1;
          moduleBreakdown[moduleRow][moduleCol].actuated += 1;
        } else open += 1;
      }
    }
  }

  return {
    fixedWalls,
    fixedFloors,
    perimeterEdges,
    open,
    staticWall,
    hole,
    actuated,
    reservedBays,
    configurable: open + staticWall + hole + actuated,
    interiorBayCells,
    freeBays: Math.max(0, interiorBayCells - reservedBays),
    moduleBreakdown,
  };
}
