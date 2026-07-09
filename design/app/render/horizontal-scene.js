import { COLORS, FRAME_OUTER } from '../constants.js';
import {
  HORIZONTAL_CELL,
  getHorizontalCellFrame,
  getHorizontalCellKey,
  isHorizontalConfigurable,
  isHorizontalPassage,
  isHorizontalPerimeter,
  isHorizontalPost,
} from '../models/horizontal-actuator.js';
import { addBox, addCylinder, addModuleGuides, addSphere } from './primitives.js';

function isRevealCell(row, col) {
  return row === 0 && col === 0;
}

function addRegularRails(THREE, group, model, start) {
  const railHeight = 1.7;
  const center = start + model.usedSpan / 2;
  for (let line = 0; line <= model.size; line += 1) {
    const offset = start + line * model.pitch + model.separatorWidth / 2;
    addBox(THREE, group, { width: model.separatorWidth, height: railHeight, depth: model.usedSpan, color: COLORS.frame, x: offset, y: railHeight / 2, z: center, opacity: 0.88 });
    addBox(THREE, group, { width: model.usedSpan, height: railHeight, depth: model.separatorWidth, color: COLORS.frame, x: center, y: railHeight / 2, z: offset, opacity: 0.88 });
  }
}

function findMarblePlacement(model, grid) {
  for (let row = model.size - 2; row >= 1; row -= 1) {
    for (let col = 1; col < model.size - 1; col += 1) {
      if (isHorizontalPassage(row, col)) return { row, col };
      if (isHorizontalConfigurable(row, col, model.size) && grid[row][col] === HORIZONTAL_CELL.OPEN) return { row, col };
    }
  }
  return { row: 1, col: 1 };
}

export function buildHorizontalScene({ THREE, model, grid, bayAssignments = {} }) {
  const group = new THREE.Group();
  const frameCenter = -FRAME_OUTER / 2 + model.rim + model.gridExtent / 2;
  const deckThickness = 2.2;
  const housingHeight = Math.max(12, model.mechanismDepth - 4);
  const topFace = Math.max(4, model.passageSize * 0.84);
  const topHighlight = topFace * 0.8;
  const bayTop = Math.max(4, model.passageSize * 0.72);
  const start = -FRAME_OUTER / 2 + model.rim + model.globalInset;
  const marbleRadius = model.marbleDiameter / 2;
  const visualPinionRadius = Math.max(3.5, Math.min(model.selectedPinionPitchDiameter / 2, model.outerHousingSize * 0.46));
  const reservedByBayKey = new Map(Object.values(bayAssignments).map((assignment) => [assignment.bayKey, assignment]));

  addBox(THREE, group, { width: FRAME_OUTER, height: 5, depth: FRAME_OUTER, color: COLORS.frame, x: 0, y: -2.5, z: 0 });
  addBox(THREE, group, { width: model.gridExtent, height: 5, depth: model.gridExtent, color: COLORS.translucentDeck, x: frameCenter, y: -(housingHeight / 2) - 4, z: frameCenter, opacity: 0.18, roughness: 0.9 });
  addRegularRails(THREE, group, model, start);

  for (let row = 0; row < model.size; row += 1) {
    for (let col = 0; col < model.size; col += 1) {
      const cell = getHorizontalCellFrame(model, row, col);
      const x = -FRAME_OUTER / 2 + cell.centerX;
      const z = -FRAME_OUTER / 2 + cell.centerY;
      const reveal = isRevealCell(row, col);
      const assignment = bayAssignments[getHorizontalCellKey(row, col)];
      const bayReservation = reservedByBayKey.get(getHorizontalCellKey(row, col));

      if (isHorizontalPerimeter(row, col, model.size) || isHorizontalPost(row, col)) {
        addBox(THREE, group, { width: model.passageSize, height: model.blockerHeight, depth: model.passageSize, color: COLORS.fixedWall, x, y: model.blockerHeight / 2, z });
        if (bayReservation) {
          addBox(THREE, group, { width: bayTop, height: housingHeight, depth: bayTop, color: COLORS.housing, x, y: -(housingHeight / 2) - 1, z, opacity: reveal ? 0.24 : 0.92, roughness: 0.72 });
          addBox(THREE, group, { width: bayTop * 0.62, height: Math.max(10, housingHeight - 8), depth: bayTop * 0.62, color: COLORS.actuated, x, y: -(housingHeight / 2) - 1, z, opacity: reveal ? 0.54 : 1, roughness: 0.56 });
          addCylinder(THREE, group, { radiusTop: Math.max(3, Math.min(visualPinionRadius * 0.44, bayTop * 0.34)), radiusBottom: Math.max(3, Math.min(visualPinionRadius * 0.44, bayTop * 0.34)), height: 3.4, color: COLORS.linkage, x, y: -(housingHeight / 2) + Math.max(4, visualPinionRadius * 0.35), z, roughness: 0.24, metalness: 0.34 });
        }
        continue;
      }

      if (isHorizontalPassage(row, col)) {
        addBox(THREE, group, { width: model.passageSize, height: deckThickness, depth: model.passageSize, color: COLORS.fixedFloor, x, y: deckThickness / 2, z, opacity: reveal ? 0.42 : 1 });
        addBox(THREE, group, { width: model.passageSize * 0.76, height: 0.8, depth: model.passageSize * 0.76, color: 0xf0f4f7, x, y: deckThickness + 0.4, z, opacity: reveal ? 0.22 : 0.16, roughness: 0.22 });
        if (bayReservation) {
          addBox(THREE, group, { width: bayTop, height: housingHeight, depth: bayTop, color: COLORS.housing, x, y: -(housingHeight / 2) - 1, z, opacity: reveal ? 0.24 : 0.92, roughness: 0.72 });
          addBox(THREE, group, { width: bayTop * 0.62, height: Math.max(10, housingHeight - 8), depth: bayTop * 0.62, color: COLORS.actuated, x, y: -(housingHeight / 2) - 1, z, opacity: reveal ? 0.54 : 1, roughness: 0.56 });
          addCylinder(THREE, group, { radiusTop: Math.max(3, Math.min(visualPinionRadius * 0.44, bayTop * 0.34)), radiusBottom: Math.max(3, Math.min(visualPinionRadius * 0.44, bayTop * 0.34)), height: 3.4, color: COLORS.linkage, x, y: -(housingHeight / 2) + Math.max(4, visualPinionRadius * 0.35), z, roughness: 0.24, metalness: 0.34 });
        }
        continue;
      }

      if (!isHorizontalConfigurable(row, col, model.size)) continue;

      const state = grid[row][col];

      if (state === HORIZONTAL_CELL.HOLE) {
        addBox(THREE, group, { width: topFace, height: housingHeight + 8, depth: topFace, color: COLORS.hole, x, y: -(housingHeight + 8) / 2, z });
        addCylinder(THREE, group, { radiusTop: topFace * 0.44, radiusBottom: topFace * 0.44, height: 1.4, color: COLORS.holeRing, x, y: 0.9, z });
        addCylinder(THREE, group, { radiusTop: topFace * 0.34, radiusBottom: topFace * 0.34, height: 1.8, color: COLORS.hole, x, y: 1.3, z });
        continue;
      }

      addBox(THREE, group, { width: topFace, height: deckThickness, depth: topFace, color: state === HORIZONTAL_CELL.STATIC_WALL ? COLORS.blocker : COLORS.open, x, y: deckThickness / 2, z, opacity: reveal ? 0.42 : 1 });
      addBox(THREE, group, { width: topHighlight, height: 0.8, depth: topHighlight, color: 0xf0f4f7, x, y: deckThickness + 0.4, z, opacity: reveal ? 0.22 : 0.16, roughness: 0.22 });

      if (state === HORIZONTAL_CELL.STATIC_WALL) {
        addBox(THREE, group, { width: topFace, height: model.blockerHeight, depth: topFace, color: COLORS.blocker, x, y: model.blockerHeight / 2, z, opacity: reveal ? 0.46 : 1 });
      }

      if (state === HORIZONTAL_CELL.ACTUATED && assignment) {
        const bayFrame = getHorizontalCellFrame(model, assignment.bayRow, assignment.bayCol);
        const bayX = -FRAME_OUTER / 2 + bayFrame.centerX;
        const bayZ = -FRAME_OUTER / 2 + bayFrame.centerY;
        const bridgeWidth = assignment.orientation === 'row' ? Math.abs(bayX - x) + bayTop * 0.72 : bayTop * 0.34;
        const bridgeDepth = assignment.orientation === 'row' ? bayTop * 0.34 : Math.abs(bayZ - z) + bayTop * 0.72;

        addBox(THREE, group, { width: topFace, height: model.blockerHeight, depth: topFace, color: COLORS.actuated, x, y: model.blockerHeight / 2, z, opacity: reveal ? 0.62 : 1, roughness: 0.62 });
        addBox(THREE, group, { width: bridgeWidth, height: Math.max(10, housingHeight - 10), depth: bridgeDepth, color: COLORS.housing, x: (x + bayX) / 2, y: -(housingHeight / 2) - 1, z: (z + bayZ) / 2, opacity: reveal ? 0.22 : 0.76, roughness: 0.72 });
        addCylinder(THREE, group, { radiusTop: visualPinionRadius, radiusBottom: visualPinionRadius, height: 2.8, color: COLORS.linkage, x: (x + bayX) / 2, y: -(housingHeight / 2) + Math.max(4, visualPinionRadius * 0.55), z: (z + bayZ) / 2, roughness: 0.24, metalness: 0.34, opacity: reveal ? 0.54 : 0.96 });
        addBox(THREE, group, { width: assignment.orientation === 'row' ? Math.abs(bayX - x) : 1.8, height: 1.8, depth: assignment.orientation === 'row' ? 1.8 : Math.abs(bayZ - z), color: COLORS.linkage, x: (x + bayX) / 2, y: 1.1, z: (z + bayZ) / 2, metalness: 0.26, roughness: 0.34 });
        addBox(THREE, group, { width: assignment.orientation === 'row' ? 2.1 : topFace * 0.42, height: Math.max(10, housingHeight - 8), depth: assignment.orientation === 'row' ? topFace * 0.42 : 2.1, color: COLORS.linkage, x: x, y: -(housingHeight / 2) - 1, z, opacity: reveal ? 0.3 : 0.82, roughness: 0.42 });
      }
    }
  }

  const marbleCell = findMarblePlacement(model, grid);
  const marbleFrame = getHorizontalCellFrame(model, marbleCell.row, marbleCell.col);
  addSphere(THREE, group, {
    radius: marbleRadius,
    color: 0xe0e5ef,
    x: -FRAME_OUTER / 2 + marbleFrame.centerX,
    y: deckThickness + marbleRadius + 0.35,
    z: -FRAME_OUTER / 2 + marbleFrame.centerY,
    roughness: 0.12,
    metalness: 0.85,
  });

  addModuleGuides(THREE, group, model, 1.2, 0.16);
  const rimOffset = -FRAME_OUTER / 2 + model.rim / 2;
  addBox(THREE, group, { width: FRAME_OUTER, height: 5, depth: model.rim, color: COLORS.frame, x: 0, y: 2.5, z: rimOffset });
  addBox(THREE, group, { width: FRAME_OUTER, height: 5, depth: model.rim, color: COLORS.frame, x: 0, y: 2.5, z: -rimOffset });
  addBox(THREE, group, { width: model.rim, height: 5, depth: FRAME_OUTER - 2 * model.rim, color: COLORS.frame, x: rimOffset, y: 2.5, z: 0 });
  addBox(THREE, group, { width: model.rim, height: 5, depth: FRAME_OUTER - 2 * model.rim, color: COLORS.frame, x: -rimOffset, y: 2.5, z: 0 });
  return { group, target: { x: frameCenter, y: Math.max(model.blockerHeight, marbleRadius), z: frameCenter } };
}
