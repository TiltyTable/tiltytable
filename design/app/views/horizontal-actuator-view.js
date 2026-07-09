import { DPR, FRAME_OUTER, MODULES, formatDegrees, formatMm, formatPercent } from '../constants.js';
import {
  HORIZONTAL_CELL,
  HORIZONTAL_PRESETS,
  computeHorizontalStats,
  getHorizontalCellFrame,
  getHorizontalCellKey,
  isHorizontalConfigurable,
  isHorizontalPassage,
  isHorizontalPerimeter,
  isHorizontalPost,
} from '../models/horizontal-actuator.js';
import { createSceneHost } from '../render/scene-host.js';
import { buildHorizontalScene } from '../render/horizontal-scene.js';

const PRESET_OPTIONS = HORIZONTAL_PRESETS;

const FIELDS = [
  { key: 'tileSize', label: 'Tile XY Envelope', min: 8, max: 140, step: 1, help: 'Full XY footprint of one horizontal tile package, including the moving surface, slider housing, and structural margin.', kind: 'mm' },
  { key: 'borderThickness', label: 'Border Thickness', min: 1, max: 20, step: 0.5, help: 'Thickness of the outer slider wall. This reduces the moving face size available at the top of the tile.', kind: 'mm' },
  { key: 'movingWallThickness', label: 'Inner Wall Thickness', min: 1, max: 20, step: 0.5, help: 'Thickness of the internal guides that reduce the inner cavity while still leaving the actuator bay underneath a neighboring fixed cell.', kind: 'mm' },
  { key: 'marbleDiameter', label: 'Marble Diameter', min: 4, max: 64, step: 0.5, help: 'Diameter of the marble used for blocker, pocket, and clearance calculations.', kind: 'mm' },
  { key: 'blockerFactor', label: 'Blocker Travel Factor', min: 0.35, max: 0.9, step: 0.01, help: 'Multiplier applied to marble diameter to derive how far the blocker rises above the floor. Values a little above 50% give a confident block.', kind: 'percent' },
  { key: 'pocketFactor', label: 'Pocket Depth Factor', min: 0.35, max: 0.9, step: 0.01, help: 'Multiplier applied to marble diameter to derive how far the tile drops below flush for the pocket / hole position.', kind: 'percent' },
  { key: 'servoAngleRangeDeg', label: 'Servo Angle Range', min: 90, max: 300, step: 5, help: 'Angular sweep available to the pinion gear. Stroke is derived from pitch diameter and this angle range.', kind: 'deg' },
  { key: 'usableStrokeFactor', label: 'Usable Stroke Factor', min: 0.5, max: 1, step: 0.01, help: 'Discount factor applied to theoretical rack stroke to account for non-ideal geometry, linkage losses, and practical margin.', kind: 'percent' },
  { key: 'gearClearance', label: 'Gear Clearance Budget', min: 0, max: 20, step: 0.5, help: 'Radial clearance budget held back around the pinion so the largest fitting pitch diameter is not unrealistically optimistic.', kind: 'mm' },
  { key: 'pinionPitchDiameter', label: 'Pinion Pitch Diameter', min: 4, max: 140, step: 0.5, help: 'Pitch diameter used for stroke calculations. By default this auto-fits to the largest diameter that still fits the outer slider envelope. Moving this slider switches to a manual request.', kind: 'mm' },
  { key: 'moduleSize', label: 'Module XY Size', min: 100, max: 800, step: 10, help: 'Nominal XY size of each manufacturing module. The app packs the regular lattice continuously across the full 3 x 3 field.', kind: 'mm' },
];

const CAMERA_PRESETS = [
  { id: 'top', label: 'Top' },
  { id: 'side', label: 'Side' },
  { id: 'isometric', label: 'Isometric' },
];

function formatFieldValue(field, value) {
  if (field.kind === 'percent') return formatPercent(value, 0);
  if (field.kind === 'deg') return formatDegrees(value, 0);
  return formatMm(value, field.step < 1 ? 1 : 0);
}

function controlMarkup() {
  return FIELDS.map((field) => `
    <div class="control-group">
      <label for="horizontal-${field.key}">${field.label}</label>
      <div class="control-row">
        <input id="horizontal-${field.key}" data-field="${field.key}" type="range" min="${field.min}" max="${field.max}" step="${field.step}">
        <span class="control-value" data-value-for="${field.key}"></span>
      </div>
      <div class="control-help">${field.help}</div>
    </div>
  `).join('');
}

function presetToolbarMarkup() {
  return `
    <div class="toolbar-row">
      <div class="toolbar-label">Tile presets for the horizontal calculator</div>
      ${PRESET_OPTIONS.map((preset) => `<button type="button" class="chip-button" data-horizontal-preset="${preset}">${preset} mm</button>`).join('')}
    </div>
  `;
}

function controlsNoteMarkup() {
  return `<div class="control-note"><strong>Start with the 75 / 60 / 50 presets.</strong> The horizontal calculator now uses the same public geometry model as Vertical. The only meaningful differences are that fixed lattice cells reserve the underfloor bay space, and the pinion auto-fits against the outer slider envelope instead of the inner cavity.</div>`;
}

function viewerToolbarMarkup() {
  return `
    <div class="toolbar-row">
      <div class="toolbar-label">Standard 3D views</div>
      ${CAMERA_PRESETS.map((preset) => `<button type="button" class="chip-button" data-camera-preset="${preset.id}">${preset.label}</button>`).join('')}
    </div>
  `;
}

function mazeToolbarMarkup() {
  return `
    <div class="toolbar-row">
      <div class="toolbar-label" data-role="randomize-status">Maze not randomized yet</div>
      <button type="button" class="chip-button" data-maze-action="randomize">Randomize Maze</button>
    </div>
  `;
}

function legendMarkup() {
  return `
    <div class="legend-item"><span class="legend-swatch" style="background:#8e715f"></span>Fixed wall / post</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#3f7896"></span>Fixed floor</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#6e9946"></span>Open floor</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#c8a27f"></span>Static wall</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#d76a50"></span>Actuated blocker</div>
    <div class="legend-item"><span class="legend-swatch" style="background:transparent;border:2px dashed #f4c26f"></span>Reserved actuator bay overlay</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#0f1116;border-color:#ff6f61"></span>Hole</div>
    <div class="legend-item"><span class="legend-swatch" style="background:transparent;border:2px dashed #ffb35a"></span>300mm module</div>
  `;
}

function renderWarningList(node, warnings) {
  node.innerHTML = warnings.map((warning) => `<div>${warning}</div>`).join('');
  node.classList.toggle('hidden', warnings.length === 0);
}

function configureCanvas(canvas, width, height) {
  canvas.width = width * DPR;
  canvas.height = height * DPR;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const context = canvas.getContext('2d');
  context.setTransform(DPR, 0, 0, DPR, 0, 0);
  return context;
}

function findHitTarget(targets, x, y) {
  for (let index = targets.length - 1; index >= 0; index -= 1) {
    const target = targets[index];
    if (x >= target.x && x <= target.x + target.w && y >= target.y && y <= target.y + target.h) return target;
  }
  return null;
}

function drawTop(canvas, model, grid, bayAssignments = {}) {
  const maxSize = Math.min(canvas.parentElement.clientWidth - 8, 640);
  const margin = 42;
  const square = Math.max(320, maxSize);
  const context = configureCanvas(canvas, square, square);
  const scale = (square - margin * 2) / FRAME_OUTER;
  const framePx = FRAME_OUTER * scale;
  const ox = margin;
  const oy = margin;
  const hitTargets = [];
  const assignments = Object.values(bayAssignments);
  const reservedByBayKey = new Map(assignments.map((assignment) => [assignment.bayKey, assignment]));
  const alternativeByBayKey = new Map(assignments.filter((assignment) => assignment.canFlip && assignment.alternative).map((assignment) => [assignment.alternative.key, assignment]));

  context.fillStyle = '#0c1015';
  context.fillRect(0, 0, square, square);
  context.fillStyle = '#3f6278';
  context.fillRect(ox, oy, framePx, framePx);
  context.fillStyle = '#111821';
  context.fillRect(ox + model.rim * scale, oy + model.rim * scale, model.gridExtent * scale, model.gridExtent * scale);

  const railPx = model.separatorWidth * scale;
  const usedGridPx = model.usedSpan * scale;
  const gridStart = ox + (model.rim + model.globalInset) * scale;
  context.fillStyle = 'rgba(63, 98, 120, 0.82)';
  for (let line = 0; line <= model.size; line += 1) {
    const offset = gridStart + line * model.pitch * scale;
    context.fillRect(offset, gridStart, railPx, usedGridPx);
    context.fillRect(gridStart, offset, usedGridPx, railPx);
  }

  for (let row = 0; row < model.size; row += 1) {
    for (let col = 0; col < model.size; col += 1) {
      const cell = getHorizontalCellFrame(model, row, col);
      const x = ox + cell.x * scale;
      const y = oy + cell.y * scale;
      const size = cell.size * scale;
      const inset = Math.max(1.1, size * 0.08);
      const inner = Math.max(2, inset * 1.8);
      const key = getHorizontalCellKey(row, col);
      const editable = isHorizontalConfigurable(row, col, model.size);
      const reservedAssignment = reservedByBayKey.get(key);
      const alternativeAssignment = alternativeByBayKey.get(key);

      if (isHorizontalPerimeter(row, col, model.size) || isHorizontalPost(row, col)) {
        context.fillStyle = '#8e715f';
      } else if (isHorizontalPassage(row, col)) {
        context.fillStyle = '#3f7896';
      } else {
        const state = grid[row][col];
        if (state === HORIZONTAL_CELL.STATIC_WALL) context.fillStyle = '#c8a27f';
        else if (state === HORIZONTAL_CELL.HOLE) context.fillStyle = '#0f1116';
        else if (state === HORIZONTAL_CELL.ACTUATED) context.fillStyle = '#d76a50';
        else context.fillStyle = '#6e9946';
      }
      context.fillRect(x + inset, y + inset, size - inset * 2, size - inset * 2);

      if (editable) hitTargets.push({ type: 'cell', row, col, x, y, w: size, h: size });

      if (editable && grid[row][col] === HORIZONTAL_CELL.HOLE) {
        context.strokeStyle = '#ff6f61';
        context.lineWidth = 1.2;
        context.strokeRect(x + inset * 1.5, y + inset * 1.5, size - inset * 3, size - inset * 3);
      }

      if (reservedAssignment) {
        context.strokeStyle = 'rgba(244, 194, 111, 0.85)';
        context.lineWidth = 1.2;
        context.setLineDash([4, 3]);
        context.strokeRect(x + inner, y + inner, size - inner * 2, size - inner * 2);
        context.setLineDash([]);
        hitTargets.push({ type: 'bay', row: reservedAssignment.gateRow, col: reservedAssignment.gateCol, x, y, w: size, h: size });
      }

      if (alternativeAssignment) {
        context.strokeStyle = 'rgba(51, 68, 85, 0.95)';
        context.lineWidth = 1.1;
        context.setLineDash([5, 4]);
        context.strokeRect(x + inner, y + inner, size - inner * 2, size - inner * 2);
        context.setLineDash([]);
        hitTargets.push({ type: 'bay', row: alternativeAssignment.gateRow, col: alternativeAssignment.gateCol, x, y, w: size, h: size });
      }
    }
  }

  assignments.forEach((assignment) => {
    const gate = getHorizontalCellFrame(model, assignment.gateRow, assignment.gateCol);
    const bay = getHorizontalCellFrame(model, assignment.bayRow, assignment.bayCol);
    const gateX = ox + gate.centerX * scale;
    const gateY = oy + gate.centerY * scale;
    const bayX = ox + bay.centerX * scale;
    const bayY = oy + bay.centerY * scale;
    context.strokeStyle = 'rgba(244, 194, 111, 0.76)';
    context.lineWidth = 1.6;
    context.beginPath();
    context.moveTo(gateX, gateY);
    context.lineTo(bayX, bayY);
    context.stroke();
    context.fillStyle = '#f4c26f';
    context.beginPath();
    context.arc(bayX, bayY, Math.max(2, model.separatorWidth * scale * 0.18), 0, Math.PI * 2);
    context.fill();
  });

  context.strokeStyle = 'rgba(255, 179, 90, 0.42)';
  context.lineWidth = 1.2;
  context.setLineDash([7, 5]);
  for (let index = 0; index <= MODULES; index += 1) {
    const position = ox + (model.rim + index * model.moduleSize) * scale;
    context.beginPath();
    context.moveTo(position, oy + model.rim * scale);
    context.lineTo(position, oy + (model.rim + model.gridExtent) * scale);
    context.stroke();
    context.beginPath();
    context.moveTo(ox + model.rim * scale, position);
    context.lineTo(ox + (model.rim + model.gridExtent) * scale, position);
    context.stroke();
  }
  context.setLineDash([]);

  context.fillStyle = '#d3e0ea';
  context.font = 'bold 12px JetBrains Mono';
  context.textAlign = 'center';
  context.fillText('914.4mm (36in)', ox + framePx / 2, oy - 10);
  context.fillStyle = 'rgba(211, 224, 234, 0.74)';
  context.font = '10px JetBrains Mono';
  context.fillText(`${model.size}x${model.size} lattice with underfloor bay reservations`, ox + framePx / 2, oy + framePx + 18);
  return hitTargets;
}

function renderStats(node, model, stats) {
  const moduleCards = stats.moduleBreakdown.map((row, rowIndex) => row.map((entry, colIndex) => `
    <div class="state-card">
      <strong>M${rowIndex + 1}${colIndex + 1}</strong>
      <span>Blockers / holes / bays</span>
      <em>${entry.actuated} / ${entry.hole} / ${entry.reserved}</em>
    </div>
  `).join('')).join('');

  node.innerHTML = `
    <div class="stats-group">
      <h4>Pack Geometry</h4>
      <div class="stat-row"><span class="key">Outer frame</span><span class="value">${formatMm(FRAME_OUTER)}</span></div>
      <div class="stat-row"><span class="key">Tile preset</span><span class="value warn">${model.presetLabel}</span></div>
      <div class="stat-row"><span class="key">Tile XY envelope</span><span class="value">${formatMm(model.tileSize)}</span></div>
      <div class="stat-row"><span class="key">Module XY size</span><span class="value">${formatMm(model.moduleSize, 0)}</span></div>
      <div class="stat-row"><span class="key">Regular grid</span><span class="value warn">${model.size} x ${model.size}</span></div>
      <div class="stat-row"><span class="key">Cells per module</span><span class="value">${model.cellsPerModule} x ${model.cellsPerModule}</span></div>
      <div class="stat-row"><span class="key">Module slack</span><span class="value">${formatMm(model.remainder)}</span></div>
      <div class="stat-row"><span class="key">Perimeter rim</span><span class="value">${formatMm(model.rim)}</span></div>
    </div>
    <div class="stats-group">
      <h4>Representative Cell</h4>
      <div class="stat-row"><span class="key">Border thickness</span><span class="value">${formatMm(model.borderThickness)}</span></div>
      <div class="stat-row"><span class="key">Outer moving size</span><span class="value">${formatMm(model.outerMovingSize)}</span></div>
      <div class="stat-row"><span class="key">Inner wall thickness</span><span class="value">${formatMm(model.movingWallThickness)}</span></div>
      <div class="stat-row"><span class="key">Inner cavity size</span><span class="value">${formatMm(model.innerCavitySize)}</span></div>
      <div class="stat-row"><span class="key">Marble diameter</span><span class="value">${formatMm(model.marbleDiameter)}</span></div>
      <div class="stat-row"><span class="key">Recommended ball</span><span class="value">${formatMm(model.recommendedBallDiameter)}</span></div>
      <div class="stat-row"><span class="key">Opening clearance</span><span class="value ${model.clearance >= 1 ? 'good' : 'danger'}">${formatMm(model.clearance)}</span></div>
    </div>
    <div class="stats-group">
      <h4>Travel And Pinion</h4>
      <div class="stat-row"><span class="key">Blocker height</span><span class="value">${formatMm(model.blockerHeight)}</span></div>
      <div class="stat-row"><span class="key">Pocket depth</span><span class="value">${formatMm(model.pocketDepth)}</span></div>
      <div class="stat-row"><span class="key">Required stroke</span><span class="value warn">${formatMm(model.requiredStroke)}</span></div>
      <div class="stat-row"><span class="key">Servo angle range</span><span class="value">${formatDegrees(model.servoAngleRangeDeg)}</span></div>
      <div class="stat-row"><span class="key">Largest fit pinion</span><span class="value">${formatMm(model.maxPinionPitchDiameter)}</span></div>
      <div class="stat-row"><span class="key">Selected pinion</span><span class="value">${formatMm(model.selectedPinionPitchDiameter)}</span></div>
      <div class="stat-row"><span class="key">Theoretical stroke</span><span class="value">${formatMm(model.theoreticalStroke)}</span></div>
      <div class="stat-row"><span class="key">Usable stroke</span><span class="value">${formatMm(model.usableStroke)}</span></div>
      <div class="stat-row"><span class="key">Stroke margin</span><span class="value ${model.strokeMargin >= 0 ? 'good' : 'danger'}">${formatMm(model.strokeMargin)}</span></div>
    </div>
    <div class="stats-group">
      <h4>Tile States And Reservations</h4>
      <div class="stat-row"><span class="key">Fixed walls / posts</span><span class="value">${stats.fixedWalls}</span></div>
      <div class="stat-row"><span class="key">Fixed floors</span><span class="value good">${stats.fixedFloors}</span></div>
      <div class="stat-row"><span class="key">Perimeter edges</span><span class="value">${stats.perimeterEdges}</span></div>
      <div class="stat-row"><span class="key">Configurable slots</span><span class="value warn">${stats.configurable}</span></div>
      <div class="stat-row"><span class="key">Open floor</span><span class="value good">${stats.open}</span></div>
      <div class="stat-row"><span class="key">Static wall</span><span class="value">${stats.staticWall}</span></div>
      <div class="stat-row"><span class="key">Hole</span><span class="value danger">${stats.hole}</span></div>
      <div class="stat-row"><span class="key">Actuated blocker</span><span class="value warn">${stats.actuated}</span></div>
      <div class="stat-row"><span class="key">Reserved actuator bays</span><span class="value warn">${stats.reservedBays}</span></div>
      <div class="stat-row"><span class="key">Free interior bay slots</span><span class="value">${stats.freeBays}</span></div>
    </div>
    <div class="stats-group">
      <h4>Per Module</h4>
      <div class="state-grid">${moduleCards}</div>
    </div>
  `;
}

function setActiveButtons(buttons, activeValue, key) {
  buttons.forEach((button) => {
    button.classList.toggle('is-active', button.dataset[key] === String(activeValue));
  });
}

function recommendedBallForPreset(tileSize, borderThickness) {
  const outerMoving = Math.max(0, tileSize - 2 * borderThickness);
  return Math.max(4, Math.round(outerMoving * 0.6 * 2) / 2);
}

export function mountHorizontalActuatorView(container, store) {
  container.innerHTML = `
    <section class="view-shell">
      <section class="view-intro">
        <div>
          <div class="view-kicker">Preset-Led Mechanism Calculator</div>
          <h2>Horizontal Actuator</h2>
          <p>Start from the 75 / 60 / 50 tile presets, then derive moving size, cavity size, blocker travel, pocket depth, and the largest pitch-diameter pinion that still fits the horizontal slider envelope.</p>
        </div>
        <p>This page now behaves like the same mechanism calculator as Vertical. The only meaningful differences are that fixed floors and fixed walls reserve the underfloor bay space, and the pinion can grow larger because it is bounded by the outer slider envelope instead of the inner cavity.</p>
      </section>
      <div class="view-grid">
        <div class="panel-stack">
          <section class="panel">
            <div class="panel-header"><h3>Architecture Settings</h3><p>Pick a preset, then override the assumptions if you want to probe the horizontal mechanism margins.</p></div>
            ${presetToolbarMarkup()}
            <div class="panel-body"><div class="control-grid">${controlMarkup()}</div>${controlsNoteMarkup()}<div class="warning-list hidden" data-role="warnings"></div></div>
          </section>
          <section class="panel">
            <div class="panel-header"><h3>Top-Down Regular Lattice</h3><p data-role="desc"></p></div>
            ${mazeToolbarMarkup()}
            <div class="legend">${legendMarkup()}</div>
            <div class="canvas-wrap"><canvas data-role="top"></canvas></div>
            <div class="panel-note" data-role="top-note"></div>
          </section>
          <section class="panel">
            <div class="panel-header"><h3>3D Visualizer</h3><p>The reserved bay sits underneath a neighboring fixed lattice cell, so the scene focuses on bay occupancy and the larger horizontal pinion envelope.</p></div>
            ${viewerToolbarMarkup()}
            <div class="canvas-wrap"><div class="three-host" data-role="three"></div></div>
            <div class="panel-note">Drag to orbit. Scroll to zoom. The reference marble sits on a legal travel surface, while the offset blocker-to-bay linkage shows how the neighboring underfloor bay drives the blocker.</div>
          </section>
        </div>
        <div class="panel-stack">
          <section class="panel">
            <div class="panel-header"><h3>Specifications</h3><p>Derived geometry, travel, pinion sizing, and lattice reservations for the current horizontal calculator setup.</p></div>
            <div class="stats-section" data-role="stats"></div>
          </section>
        </div>
      </div>
    </section>
  `;

  const elements = {
    warnings: container.querySelector('[data-role="warnings"]'),
    desc: container.querySelector('[data-role="desc"]'),
    topNote: container.querySelector('[data-role="top-note"]'),
    top: container.querySelector('[data-role="top"]'),
    stats: container.querySelector('[data-role="stats"]'),
    three: container.querySelector('[data-role="three"]'),
    presetButtons: Array.from(container.querySelectorAll('[data-horizontal-preset]')),
    valueByField: {},
    cameraButtons: Array.from(container.querySelectorAll('[data-camera-preset]')),
    randomizeButton: container.querySelector('[data-maze-action="randomize"]'),
    randomizeStatus: container.querySelector('[data-role="randomize-status"]'),
  };

  const sceneHost = createSceneHost({ host: elements.three, builder: buildHorizontalScene, initialCamera: [580, 360, 680] });
  let hitTargets = [];
  let activeCamera = 'isometric';

  FIELDS.forEach((field) => {
    const input = container.querySelector(`[data-field="${field.key}"]`);
    const value = container.querySelector(`[data-value-for="${field.key}"]`);
    elements.valueByField[field.key] = value;
    input.addEventListener('input', () => {
      if (field.key === 'pinionPitchDiameter') {
        store.updateInputs('horizontal', { pinionPitchDiameter: Number(input.value), pinionAutoFit: false });
      } else {
        store.updateInputs('horizontal', { [field.key]: Number(input.value) });
      }
    });
  });

  elements.presetButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const preset = Number(button.dataset.horizontalPreset);
      const current = store.getState().horizontal.inputs;
      store.updateInputs('horizontal', {
        tileSize: preset,
        marbleDiameter: recommendedBallForPreset(preset, current.borderThickness),
        pinionPitchDiameter: current.pinionPitchDiameter,
        pinionAutoFit: true,
      });
    });
  });

  elements.cameraButtons.forEach((button) => {
    button.addEventListener('click', () => {
      activeCamera = button.dataset.cameraPreset;
      setActiveButtons(elements.cameraButtons, activeCamera, 'cameraPreset');
      sceneHost.setCameraPreset(activeCamera);
    });
  });

  elements.randomizeButton.addEventListener('click', () => {
    store.randomizeGrid('horizontal');
  });

  elements.top.addEventListener('click', (event) => {
    const rect = elements.top.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const hit = findHitTarget(hitTargets, x, y);
    if (!hit) return;
    if (hit.type === 'bay') store.flipHorizontalBay(hit.row, hit.col);
    else store.cycleCell('horizontal', hit.row, hit.col);
  });

  const render = (slice) => {
    FIELDS.forEach((field) => {
      const input = container.querySelector(`[data-field="${field.key}"]`);
      const displayValue = field.key === 'pinionPitchDiameter' ? slice.model.selectedPinionPitchDiameter : slice.inputs[field.key];
      input.value = displayValue;
      elements.valueByField[field.key].textContent = field.key === 'pinionPitchDiameter' && slice.inputs.pinionAutoFit
        ? `${formatFieldValue(field, displayValue)} auto`
        : formatFieldValue(field, displayValue);
    });
    setActiveButtons(elements.presetButtons, slice.model.tileSize, 'horizontalPreset');
    setActiveButtons(elements.cameraButtons, activeCamera, 'cameraPreset');
    elements.randomizeButton.textContent = slice.randomizeCount > 0
      ? `Randomize Maze (${slice.randomizeCount})`
      : 'Randomize Maze';
    elements.randomizeStatus.textContent = slice.randomizeCount > 0
      ? `Maze actions · shuffle #${slice.randomizeCount}`
      : 'Maze actions · not randomized yet';
    renderWarningList(elements.warnings, [...slice.model.warnings, ...(slice.resolutionWarnings ?? [])]);
    elements.desc.textContent = slice.model.desc;
    elements.topNote.textContent = `${slice.model.topNote} ${slice.model.sectionNote}`;
    hitTargets = drawTop(elements.top, slice.model, slice.grid, slice.bayAssignments);
    renderStats(elements.stats, slice.model, computeHorizontalStats(slice.model, slice.grid, slice.bayAssignments));
    sceneHost.render({ model: slice.model, grid: slice.grid, bayAssignments: slice.bayAssignments });
    sceneHost.setCameraPreset(activeCamera);
  };

  const unsubscribe = store.subscribe((state) => {
    if (state.currentView !== 'horizontal') return;
    render(state.horizontal);
  });

  const resize = () => {
    const slice = store.getState().horizontal;
    render(slice);
    sceneHost.resize();
  };
  window.addEventListener('resize', resize);
  render(store.getState().horizontal);

  return () => {
    unsubscribe();
    window.removeEventListener('resize', resize);
    sceneHost.dispose();
  };
}
