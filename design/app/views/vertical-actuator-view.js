import { DPR, FRAME_OUTER, MODULES, formatDegrees, formatMm, formatPercent } from '../constants.js';
import {
  VERTICAL_CELL,
  VERTICAL_PHASE_COUNT,
  VERTICAL_PRESETS,
  computeVerticalStats,
  getVerticalCellFrame,
  isVerticalBorder,
} from '../models/vertical-actuator.js';
import { createSceneHost } from '../render/scene-host.js';
import { buildVerticalScene } from '../render/vertical-scene.js';

const PRESET_OPTIONS = VERTICAL_PRESETS;
const FIELDS = [
  { key: 'tileSize', label: 'Tile XY Envelope', min: 8, max: 140, step: 1, help: 'Full XY footprint of one tile package, including the moving surface, walls, actuator body, and structural margin.', kind: 'mm' },
  { key: 'borderThickness', label: 'Border Thickness', min: 1, max: 20, step: 0.5, help: 'Thickness of the outer sliding-element wall. This reduces the moving face size available at the top of the tile.', kind: 'mm' },
  { key: 'movingWallThickness', label: 'Inner Wall Thickness', min: 1, max: 20, step: 0.5, help: 'Thickness of the internal walls or guides that reduce the usable cavity for the rack, pinion, and moving internals.', kind: 'mm' },
  { key: 'marbleDiameter', label: 'Marble Diameter', min: 4, max: 64, step: 0.5, help: 'Diameter of the marble used for blocker, pocket, and clearance calculations.', kind: 'mm' },
  { key: 'blockerFactor', label: 'Blocker Travel Factor', min: 0.35, max: 0.9, step: 0.01, help: 'Multiplier applied to marble diameter to derive how far the blocker rises above the floor. Values a little above 50% give a confident block.', kind: 'percent' },
  { key: 'pocketFactor', label: 'Pocket Depth Factor', min: 0.35, max: 0.9, step: 0.01, help: 'Multiplier applied to marble diameter to derive how far the tile drops below flush for the pocket / hole position.', kind: 'percent' },
  { key: 'servoAngleRangeDeg', label: 'Servo Angle Range', min: 90, max: 300, step: 5, help: 'Angular sweep available to the pinion gear. Stroke is derived from pitch diameter and this angle range.', kind: 'deg' },
  { key: 'usableStrokeFactor', label: 'Usable Stroke Factor', min: 0.5, max: 1, step: 0.01, help: 'Discount factor applied to theoretical rack stroke to account for non-ideal geometry, linkage losses, and practical margin.', kind: 'percent' },
  { key: 'gearClearance', label: 'Gear Clearance Budget', min: 0, max: 20, step: 0.5, help: 'Radial clearance budget held back around the pinion so the largest fitting pitch diameter is not unrealistically optimistic.', kind: 'mm' },
  { key: 'pinionPitchDiameter', label: 'Pinion Pitch Diameter', min: 4, max: 140, step: 0.5, help: 'Pitch diameter used for stroke calculations. By default this auto-fits to the largest diameter that still fits the cavity. Moving this slider switches to a manual request.', kind: 'mm' },
  { key: 'moduleSize', label: 'Module XY Size', min: 100, max: 800, step: 10, help: 'Nominal XY size of each manufacturing module. The app packs dense tiles continuously across the full 3 x 3 field.', kind: 'mm' },
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
      <label for="vertical-${field.key}">${field.label}</label>
      <div class="control-row">
        <input id="vertical-${field.key}" data-field="${field.key}" type="range" min="${field.min}" max="${field.max}" step="${field.step}">
        <span class="control-value" data-value-for="${field.key}"></span>
      </div>
      <div class="control-help">${field.help}</div>
    </div>
  `).join('');
}

function presetMarkup() {
  return `
    <div class="toolbar-row">
      <div class="toolbar-label">Tile presets for the vertical calculator</div>
      ${PRESET_OPTIONS.map((preset) => `<button type="button" class="chip-button" data-vertical-preset="${preset}">${preset} mm</button>`).join('')}
    </div>
  `;
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
      ${Array.from({ length: VERTICAL_PHASE_COUNT }, (_, phase) => `<button type="button" class="chip-button" data-phase-preview="${phase}">Phase ${phase}</button>`).join('')}
    </div>
  `;
}

function controlsNoteMarkup() {
  return `<div class="control-note"><strong>Start with the 75 / 60 / 50 presets.</strong> The calculator derives blocker height, pocket depth, required stroke, and max-fit pinion diameter from the moving-element geometry. Pinion diameter starts in auto-fit mode and switches to manual only after you move that slider.</div>`;
}

function legendMarkup() {
  return `
    <div class="legend-item"><span class="legend-swatch" style="background:#8e715f"></span>Border wall</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#4b6b3f"></span>Open floor</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#c8a27f"></span>Static blocker</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#0f1116;border-color:#ff6f61"></span>Hole</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#7a5cff"></span>Dynamic trap</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#72d68d"></span>Start</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#63c4ff"></span>End</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#4e8bff"></span>Blue reward (+50)</div>
    <div class="legend-item"><span class="legend-swatch" style="background:#f6d34e"></span>Yellow time (+3s)</div>
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

function trapMap(maze) {
  return new Map((maze.dynamicTraps ?? []).map((trap) => [`${trap.row},${trap.col}`, trap]));
}

function rewardMap(tiles = []) {
  return new Map(tiles.map((tile) => [`${tile.row},${tile.col}`, tile]));
}

function drawRewardGlyph(context, tile, x, y, size) {
  if (tile.color === 'blue') {
    context.fillStyle = '#4e8bff';
    context.beginPath();
    context.arc(x + size * 0.78, y + size * 0.24, Math.max(5, size * 0.12), 0, Math.PI * 2);
    context.fill();
  } else if (tile.color === 'yellow') {
    context.fillStyle = '#f6d34e';
    context.beginPath();
    context.moveTo(x + size * 0.78, y + size * 0.08);
    context.lineTo(x + size * 0.92, y + size * 0.26);
    context.lineTo(x + size * 0.7, y + size * 0.26);
    context.closePath();
    context.fill();
  }
}

function drawTop(canvas, model, grid, maze) {
  const maxSize = Math.min(canvas.parentElement.clientWidth - 8, 640);
  const margin = 42;
  const square = Math.max(320, maxSize);
  const context = configureCanvas(canvas, square, square);
  const scale = (square - margin * 2) / FRAME_OUTER;
  const framePx = FRAME_OUTER * scale;
  const ox = margin;
  const oy = margin;
  const hitCells = [];
  const dynamicByKey = trapMap(maze);
  const rewardByKey = rewardMap(maze.rewardTiles);
  const bonusByKey = rewardMap(maze.bonusTimeTiles);

  context.fillStyle = '#0c1015';
  context.fillRect(0, 0, square, square);
  context.fillStyle = '#3f6278';
  context.fillRect(ox, oy, framePx, framePx);
  context.fillStyle = '#111821';
  context.fillRect(ox + model.rim * scale, oy + model.rim * scale, model.gridExtent * scale, model.gridExtent * scale);

  for (let row = 0; row < model.size; row += 1) {
    for (let col = 0; col < model.size; col += 1) {
      const cell = getVerticalCellFrame(model, row, col);
      const x = ox + cell.x * scale;
      const y = oy + cell.y * scale;
      const size = cell.size * scale;
      const faceInset = ((model.tileSize - model.outerMovingSize) / 2) * scale;
      const faceSize = Math.max(0, model.outerMovingSize * scale);
      const border = isVerticalBorder(row, col, model.size);
      const state = grid[row][col];
      const trap = dynamicByKey.get(`${row},${col}`);
      const reward = rewardByKey.get(`${row},${col}`);
      const bonus = bonusByKey.get(`${row},${col}`);
      hitCells.push({ row, col, x, y, w: size, h: size, editable: !border });
      if (border) context.fillStyle = '#8e715f';
      else if (state === VERTICAL_CELL.BLOCKER) context.fillStyle = '#c8a27f';
      else if (state === VERTICAL_CELL.HOLE) context.fillStyle = '#0f1116';
      else context.fillStyle = '#4b6b3f';
      context.fillRect(x + faceInset, y + faceInset, faceSize, faceSize);
      if (!border && state === VERTICAL_CELL.HOLE) {
        context.strokeStyle = '#ff6f61';
        context.lineWidth = 1.2;
        context.strokeRect(x + faceInset + faceSize * 0.12, y + faceInset + faceSize * 0.12, faceSize * 0.76, faceSize * 0.76);
      }
      if (trap) {
        context.fillStyle = '#7a5cff';
        context.fillRect(x + faceInset + 2, y + faceInset + 2, Math.max(16, faceSize * 0.28), Math.max(14, faceSize * 0.22));
        context.fillStyle = '#f7f7fb';
        context.font = 'bold 9px JetBrains Mono';
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillText(trap.short, x + faceInset + Math.max(8, faceSize * 0.14), y + faceInset + Math.max(7, faceSize * 0.11));
      }
      if (reward) drawRewardGlyph(context, reward, x + faceInset, y + faceInset, faceSize);
      if (bonus) drawRewardGlyph(context, bonus, x + faceInset, y + faceInset + faceSize * 0.12, faceSize * 0.88);
    }
  }

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
  context.fillText(`${model.size}x${model.size} dense field · preview phase ${maze.previewPhase}`, ox + framePx / 2, oy + framePx + 18);

  if (maze?.start) {
    const startCell = getVerticalCellFrame(model, maze.start.row, maze.start.col);
    const sx = ox + startCell.centerX * scale;
    const sy = oy + startCell.centerY * scale;
    context.fillStyle = '#72d68d';
    context.beginPath();
    context.arc(sx, sy, Math.max(5, startCell.size * scale * 0.15), 0, Math.PI * 2);
    context.fill();
    context.fillStyle = '#0f1318';
    context.font = 'bold 10px JetBrains Mono';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText('S', sx, sy + 0.5);
  }

  if (maze?.end) {
    const endCell = getVerticalCellFrame(model, maze.end.row, maze.end.col);
    const ex = ox + endCell.centerX * scale;
    const ey = oy + endCell.centerY * scale;
    context.fillStyle = '#63c4ff';
    context.beginPath();
    context.arc(ex, ey, Math.max(5, endCell.size * scale * 0.15), 0, Math.PI * 2);
    context.fill();
    context.fillStyle = '#0f1318';
    context.font = 'bold 10px JetBrains Mono';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText('E', ex, ey + 0.5);
  }

  return hitCells;
}

function trapPatternSummary(dynamicTraps = []) {
  const counts = {};
  for (const trap of dynamicTraps) counts[trap.short] = (counts[trap.short] ?? 0) + 1;
  return Object.entries(counts).map(([short, count]) => `${short}:${count}`).join(' · ') || 'none';
}

function routeSummaryMarkup(routes = []) {
  return routes.map((route) => `
    <div class="state-card">
      <strong>Route ${route.id}</strong>
      <span>${route.label}</span>
      <em>${route.pathLength} moves · ${route.turns} turns · ${route.trapExposure} trap pressure</em>
      <em>${route.points} pts · +${route.bonusTime}s</em>
    </div>
  `).join('');
}

function renderStats(node, model, stats, maze) {
  const moduleCards = stats.moduleBreakdown.map((row, rowIndex) => row.map((entry, colIndex) => `
    <div class="state-card">
      <strong>M${rowIndex + 1}${colIndex + 1}</strong>
      <span>Blockers / holes</span>
      <em>${entry.blocker} / ${entry.hole}</em>
    </div>
  `).join('')).join('');
  const routeCards = routeSummaryMarkup(maze.routeCandidates);

  node.innerHTML = `
    <div class="stats-group">
      <h4>Pack Geometry</h4>
      <div class="stat-row"><span class="key">Outer frame</span><span class="value">${formatMm(FRAME_OUTER)}</span></div>
      <div class="stat-row"><span class="key">Tile preset</span><span class="value warn">${model.presetLabel}</span></div>
      <div class="stat-row"><span class="key">Tile XY envelope</span><span class="value">${formatMm(model.tileSize)}</span></div>
      <div class="stat-row"><span class="key">Module XY size</span><span class="value">${formatMm(model.moduleSize, 0)}</span></div>
      <div class="stat-row"><span class="key">Cells per module</span><span class="value">${model.cellsPerModule} x ${model.cellsPerModule}</span></div>
      <div class="stat-row"><span class="key">Dense grid</span><span class="value warn">${model.size} x ${model.size}</span></div>
      <div class="stat-row"><span class="key">Module slack</span><span class="value">${formatMm(model.remainder)}</span></div>
      <div class="stat-row"><span class="key">Perimeter rim</span><span class="value">${formatMm(model.rim)}</span></div>
    </div>
    <div class="stats-group">
      <h4>Travel And Pinion</h4>
      <div class="stat-row"><span class="key">Required stroke</span><span class="value warn">${formatMm(model.requiredStroke)}</span></div>
      <div class="stat-row"><span class="key">Largest fit pinion</span><span class="value">${formatMm(model.maxPinionPitchDiameter)}</span></div>
      <div class="stat-row"><span class="key">Selected pinion</span><span class="value">${formatMm(model.selectedPinionPitchDiameter)}</span></div>
      <div class="stat-row"><span class="key">Usable stroke</span><span class="value">${formatMm(model.usableStroke)}</span></div>
      <div class="stat-row"><span class="key">Stroke margin</span><span class="value ${model.strokeMargin >= 0 ? 'good' : 'danger'}">${formatMm(model.strokeMargin)}</span></div>
    </div>
    <div class="stats-group">
      <h4>Risk / Reward Routes</h4>
      <div class="state-grid">${routeCards}</div>
      <div class="stat-row"><span class="key">Time budget</span><span class="value warn">${maze.timeBudgetSeconds}s</span></div>
      <div class="stat-row"><span class="key">Dynamic traps</span><span class="value danger">${maze.dynamicTrapCount}</span></div>
      <div class="stat-row"><span class="key">Trap patterns</span><span class="value">${trapPatternSummary(maze.dynamicTraps)}</span></div>
      <div class="stat-row"><span class="key">Start</span><span class="value">r${maze.start.row}, c${maze.start.col}</span></div>
      <div class="stat-row"><span class="key">End</span><span class="value">r${maze.end.row}, c${maze.end.col}</span></div>
    </div>
    <div class="stats-group">
      <h4>Phase-Aware Difficulty</h4>
      <div class="stat-row"><span class="key">Safe path</span><span class="value ${maze.safePathExists ? 'good' : 'danger'}">${maze.safePathExists ? 'yes' : 'no'}</span></div>
      <div class="stat-row"><span class="key">Safe route length</span><span class="value warn">${maze.safePathLength} moves</span></div>
      <div class="stat-row"><span class="key">Path turns</span><span class="value">${maze.turnCount}</span></div>
      <div class="stat-row"><span class="key">Branch regions</span><span class="value">${maze.branchCount}</span></div>
      <div class="stat-row"><span class="key">Optimistic route</span><span class="value">${maze.optimisticPathLength} moves</span></div>
      <div class="stat-row"><span class="key">Deception delta</span><span class="value warn">${maze.deceptionDelta}</span></div>
      <div class="stat-row"><span class="key">Trap cells with holes</span><span class="value danger">${maze.trapCount}</span></div>
    </div>
    <div class="stats-group">
      <h4>Tile States</h4>
      <div class="stat-row"><span class="key">Fixed border cells</span><span class="value">${stats.border}</span></div>
      <div class="stat-row"><span class="key">Editable interior cells</span><span class="value warn">${stats.interior}</span></div>
      <div class="stat-row"><span class="key">Open floor</span><span class="value good">${stats.open}</span></div>
      <div class="stat-row"><span class="key">Actuated blocker</span><span class="value">${stats.blocker}</span></div>
      <div class="stat-row"><span class="key">Hole</span><span class="value danger">${stats.hole}</span></div>
    </div>
    <div class="stats-group">
      <h4>Per Module</h4>
      <div class="state-grid">${moduleCards}</div>
    </div>
  `;
}

function recommendedBallForPreset(tileSize, borderThickness) {
  const outerMoving = Math.max(0, tileSize - 2 * borderThickness);
  return Math.max(4, Math.round(outerMoving * 0.6 * 2) / 2);
}

function setActiveButtons(buttons, activeValue, key) {
  buttons.forEach((button) => {
    button.classList.toggle('is-active', button.dataset[key] === String(activeValue));
  });
}

export function mountVerticalActuatorView(container, store) {
  container.innerHTML = `
    <section class="view-shell">
      <section class="view-intro">
        <div>
          <div class="view-kicker">Risk Reward Tilt Puzzle</div>
          <h2>Vertical Actuator</h2>
          <p>Start from the 75 / 60 / 50 tile presets, then generate mazes with at least two viable routes: a safer route with less reward and a riskier route with more dynamic traps and more upside.</p>
        </div>
        <p>This generator assumes the player sees the whole board from above. Difficulty therefore comes from choosing between route programs, trap timing, and reward opportunities, not from hidden information.</p>
      </section>
      <div class="view-grid">
        <div class="panel-stack">
          <section class="panel">
            <div class="panel-header"><h3>Architecture Settings</h3><p>Pick a preset, then override the assumptions if you want to probe the mechanism margins.</p></div>
            ${presetMarkup()}
            <div class="panel-body"><div class="control-grid">${controlMarkup()}</div>${controlsNoteMarkup()}<div class="warning-list hidden" data-role="warnings"></div></div>
          </section>
          <section class="panel">
            <div class="panel-header"><h3>Top-Down Dense Field</h3><p data-role="desc"></p></div>
            ${mazeToolbarMarkup()}
            <div class="legend">${legendMarkup()}</div>
            <div class="canvas-wrap"><canvas data-role="top"></canvas></div>
            <div class="panel-note" data-role="top-note"></div>
          </section>
          <section class="panel">
            <div class="panel-header"><h3>3D Visualizer</h3><p>The reveal cell now reflects route gates, timed bridges, and branch traps while reward and bonus-time tiles stay visible in the scene.</p></div>
            ${viewerToolbarMarkup()}
            <div class="canvas-wrap"><div class="three-host" data-role="three"></div></div>
            <div class="panel-note">Drag to orbit. Scroll to zoom. Use the standard view buttons for top, side, and isometric camera positions.</div>
          </section>
        </div>
        <div class="panel-stack">
          <section class="panel">
            <div class="panel-header"><h3>Specifications</h3><p>Derived geometry, route tradeoffs, and phase-aware trap information for the current vertical puzzle.</p></div>
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
    valueByField: {},
    presetButtons: Array.from(container.querySelectorAll('[data-vertical-preset]')),
    cameraButtons: Array.from(container.querySelectorAll('[data-camera-preset]')),
    phaseButtons: Array.from(container.querySelectorAll('[data-phase-preview]')),
    randomizeButton: container.querySelector('[data-maze-action="randomize"]'),
    randomizeStatus: container.querySelector('[data-role="randomize-status"]'),
  };

  const sceneHost = createSceneHost({ host: elements.three, builder: buildVerticalScene, initialCamera: [520, 360, 630] });
  let hitCells = [];
  let activeCamera = 'isometric';

  FIELDS.forEach((field) => {
    const input = container.querySelector(`[data-field="${field.key}"]`);
    const value = container.querySelector(`[data-value-for="${field.key}"]`);
    elements.valueByField[field.key] = value;
    input.addEventListener('input', () => {
      if (field.key === 'pinionPitchDiameter') {
        store.updateInputs('vertical', { pinionPitchDiameter: Number(input.value), pinionAutoFit: false });
      } else {
        store.updateInputs('vertical', { [field.key]: Number(input.value) });
      }
    });
  });

  elements.presetButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const preset = Number(button.dataset.verticalPreset);
      const current = store.getState().vertical.inputs;
      store.updateInputs('vertical', {
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

  elements.phaseButtons.forEach((button) => {
    button.addEventListener('click', () => {
      store.setVerticalPreviewPhase(Number(button.dataset.phasePreview));
    });
  });

  elements.randomizeButton.addEventListener('click', () => {
    store.randomizeGrid('vertical');
  });

  elements.top.addEventListener('click', (event) => {
    const rect = elements.top.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const hit = hitCells.find((cell) => cell.editable && x >= cell.x && x <= cell.x + cell.w && y >= cell.y && y <= cell.y + cell.h);
    if (hit) store.cycleCell('vertical', hit.row, hit.col);
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
    setActiveButtons(elements.presetButtons, slice.model.tileSize, 'verticalPreset');
    setActiveButtons(elements.cameraButtons, activeCamera, 'cameraPreset');
    setActiveButtons(elements.phaseButtons, slice.previewPhase, 'phasePreview');
    elements.randomizeButton.textContent = slice.randomizeCount > 0 ? `Randomize Maze (${slice.randomizeCount})` : 'Randomize Maze';
    const safer = slice.routeCandidates?.find((route) => route.id === 'A');
    const riskier = slice.routeCandidates?.find((route) => route.id === 'B');
    elements.randomizeStatus.textContent = safer && riskier
      ? `Route A: ${safer.points} pts / ${safer.estimatedTime.toFixed(1)}s · Route B: ${riskier.points} pts / ${riskier.estimatedTime.toFixed(1)}s`
      : 'Maze actions · not randomized yet';
    renderWarningList(elements.warnings, slice.model.warnings);
    elements.desc.textContent = slice.model.desc;
    elements.topNote.textContent = `${slice.model.topNote} ${slice.model.sectionNote} Purple badges mark dynamic trap cells, blue pips are score rewards, yellow markers are bonus-time tiles, and phase buttons preview the 3-step trap cycle.`;
    hitCells = drawTop(elements.top, slice.model, slice.grid, slice);
    renderStats(elements.stats, slice.model, computeVerticalStats(slice.model, slice.grid), slice);
    sceneHost.render({ model: slice.model, grid: slice.grid, start: slice.start, end: slice.end, dynamicTraps: slice.dynamicTraps, rewardTiles: slice.rewardTiles, bonusTimeTiles: slice.bonusTimeTiles, previewPhase: slice.previewPhase });
    sceneHost.setCameraPreset(activeCamera);
  };

  const unsubscribe = store.subscribe((state) => {
    if (state.currentView !== 'vertical') return;
    render(state.vertical);
  });

  const resize = () => {
    const slice = store.getState().vertical;
    render(slice);
    sceneHost.resize();
  };
  window.addEventListener('resize', resize);
  render(store.getState().vertical);

  return () => {
    unsubscribe();
    window.removeEventListener('resize', resize);
    sceneHost.dispose();
  };
}
