import { COLORS, FRAME_OUTER } from '../constants.js';
import { getVerticalCellFrame, isVerticalBorder } from '../models/vertical-actuator.js';
import { addBox, addCylinder, addModuleGuides, addSphere } from './primitives.js';

function isRevealCell(row, col) {
  return row === 0 && col === 0;
}

function trapMap(dynamicTraps = []) {
  return new Map(dynamicTraps.map((trap) => [`${trap.row},${trap.col}`, trap]));
}

function rewardMap(tiles = []) {
  return new Map(tiles.map((tile) => [`${tile.row},${tile.col}`, tile]));
}

function trapColor(trap) {
  if (!trap) return 0x7a5cff;
  if (trap.patternId === 'pulseFloor') return 0xff6f61;
  if (trap.patternId === 'pulseGate') return 0x7a5cff;
  if (trap.patternId === 'collapseGate') return 0xff9b4a;
  return 0x63c4ff;
}

function rewardColor(tile) {
  return tile?.color === 'yellow' ? 0xf6d34e : 0x4e8bff;
}

function findMarbleCell(grid, start) {
  if (start && grid[start.row]?.[start.col] === 0) return start;
  for (let row = grid.length - 2; row >= 1; row -= 1) {
    for (let col = 1; col < grid.length - 1; col += 1) {
      if (grid[row][col] === 0) return { row, col };
    }
  }
  return { row: 1, col: 1 };
}

export function buildVerticalScene({ THREE, model, grid, start, end, dynamicTraps = [], rewardTiles = [], bonusTimeTiles = [], previewPhase = 0 }) {
  const group = new THREE.Group();
  const frameCenter = -FRAME_OUTER / 2 + model.rim + model.gridExtent / 2;
  const deckThickness = 2.4;
  const housingHeight = Math.max(12, model.mechanismDepth - 4);
  const topFace = Math.max(4, model.outerMovingSize);
  const innerFace = Math.max(4, model.innerCavitySize);
  const blockerWidth = Math.max(innerFace * 0.84, topFace * 0.68);
  const guideWidth = Math.max(1.5, model.movingWallThickness * 0.8);
  const marbleRadius = model.marbleDiameter / 2;
  const pinionRadius = Math.max(2, model.selectedPinionPitchDiameter / 2);
  const rackWidth = Math.max(2.4, model.movingWallThickness * 1.4);
  const holeDiameter = Math.max(4, topFace * 0.72);
  const topInset = (model.tileSize - topFace) / 2;
  const traps = trapMap(dynamicTraps);
  const rewards = rewardMap(rewardTiles);
  const bonusTimes = rewardMap(bonusTimeTiles);

  addBox(THREE, group, { width: FRAME_OUTER, height: 4, depth: FRAME_OUTER, color: COLORS.frame, x: 0, y: -2, z: 0, roughness: 0.82 });
  addBox(THREE, group, { width: model.gridExtent, height: 4, depth: model.gridExtent, color: COLORS.translucentDeck, x: frameCenter, y: -housingHeight - 2.2, z: frameCenter, opacity: 0.18, roughness: 0.92 });

  for (let row = 0; row < model.size; row += 1) {
    for (let col = 0; col < model.size; col += 1) {
      const cell = getVerticalCellFrame(model, row, col);
      const x = -FRAME_OUTER / 2 + cell.centerX;
      const z = -FRAME_OUTER / 2 + cell.centerY;
      const border = isVerticalBorder(row, col, model.size);
      const state = grid[row][col];
      const reveal = isRevealCell(row, col);
      const trap = traps.get(`${row},${col}`);
      const reward = rewards.get(`${row},${col}`);
      const bonus = bonusTimes.get(`${row},${col}`);

      if (border) {
        addBox(THREE, group, { width: topFace, height: model.blockerHeight, depth: topFace, color: COLORS.fixedWall, x, y: model.blockerHeight / 2, z });
        addBox(THREE, group, { width: topFace * 0.9, height: 1.2, depth: topFace * 0.9, color: 0xb48b6d, x, y: model.blockerHeight + 0.6, z, roughness: 0.42 });
        continue;
      }

      addBox(THREE, group, { width: topFace, height: housingHeight, depth: topFace, color: COLORS.housing, x, y: -(housingHeight / 2) - 1, z, opacity: reveal ? 0.2 : 0.9 });
      addBox(THREE, group, { width: innerFace, height: Math.max(10, housingHeight - 6), depth: innerFace, color: 0x2a3643, x, y: -(housingHeight / 2) - 1, z, opacity: reveal ? 0.12 : 0.5, roughness: 0.62 });

      if (reveal) {
        addCylinder(THREE, group, { radiusTop: pinionRadius, radiusBottom: pinionRadius, height: rackWidth * 1.6, color: COLORS.linkage, x: x + innerFace * 0.14, y: -(housingHeight / 2) + pinionRadius + 2, z, rotationX: Math.PI / 2, roughness: 0.28, metalness: 0.34 });
        addBox(THREE, group, { width: rackWidth, height: Math.max(model.requiredStroke, model.blockerHeight + model.pocketDepth), depth: rackWidth * 1.6, color: COLORS.linkage, x: x + innerFace * 0.3, y: -(housingHeight / 2) + model.requiredStroke / 2 + 2, z, roughness: 0.35, metalness: 0.18 });
        addBox(THREE, group, { width: model.movingWallThickness * 2, height: housingHeight * 0.82, depth: topFace * 0.6, color: COLORS.actuated, x: x - innerFace * 0.18, y: -(housingHeight / 2) + housingHeight * 0.09, z, roughness: 0.56, opacity: 0.9 });
      } else {
        addBox(THREE, group, { width: Math.max(8, innerFace * 0.46), height: Math.max(10, housingHeight - 6), depth: topFace * 0.4, color: COLORS.actuated, x, y: -(housingHeight / 2) - 1, z, roughness: 0.58, opacity: 0.92 });
      }

      addBox(THREE, group, { width: guideWidth, height: housingHeight, depth: guideWidth, color: COLORS.linkage, x: x - innerFace * 0.28, y: -(housingHeight / 2) + 1, z, roughness: 0.34, metalness: 0.26 });
      addBox(THREE, group, { width: guideWidth, height: housingHeight, depth: guideWidth, color: COLORS.linkage, x: x + innerFace * 0.28, y: -(housingHeight / 2) + 1, z, roughness: 0.34, metalness: 0.26 });

      if (state === 2) {
        addBox(THREE, group, { width: holeDiameter, height: housingHeight + 8, depth: holeDiameter, color: COLORS.hole, x, y: -(housingHeight + 8) / 2, z, roughness: 0.95 });
        addCylinder(THREE, group, { radiusTop: holeDiameter * 0.48, radiusBottom: holeDiameter * 0.48, height: 1.4, color: COLORS.holeRing, x, y: 0.9, z });
        addCylinder(THREE, group, { radiusTop: holeDiameter * 0.38, radiusBottom: holeDiameter * 0.38, height: 1.8, color: COLORS.hole, x, y: 1.3, z });
      } else {
        addBox(THREE, group, { width: topFace, height: deckThickness, depth: topFace, color: state === 1 ? COLORS.blocker : COLORS.open, x, y: deckThickness / 2, z, opacity: reveal ? 0.42 : 1 });
        addBox(THREE, group, { width: topFace - Math.max(2, topInset), height: 0.8, depth: topFace - Math.max(2, topInset), color: 0xf0f4f7, x, y: deckThickness + 0.4, z, opacity: reveal ? 0.16 : 0.1, roughness: 0.22 });
      }

      if (state === 1) {
        addBox(THREE, group, { width: blockerWidth, height: model.blockerHeight, depth: blockerWidth, color: COLORS.blocker, x, y: model.blockerHeight / 2, z, roughness: 0.68, opacity: reveal ? 0.4 : 1 });
        addBox(THREE, group, { width: blockerWidth * 0.74, height: 0.9, depth: blockerWidth * 0.74, color: 0xf3d5b8, x, y: model.blockerHeight + 0.45, z, opacity: reveal ? 0.28 : 0.4, roughness: 0.28 });
      }

      if (trap) {
        addCylinder(THREE, group, {
          radiusTop: Math.max(3, topFace * 0.22),
          radiusBottom: Math.max(3, topFace * 0.22),
          height: 1.2,
          color: trapColor(trap),
          x,
          y: deckThickness + 0.7,
          z,
          roughness: 0.25,
          metalness: 0.14,
        });
      }

      if (reward) {
        addCylinder(THREE, group, {
          radiusTop: Math.max(2.6, topFace * 0.14),
          radiusBottom: Math.max(2.6, topFace * 0.14),
          height: 0.8,
          color: rewardColor(reward),
          x: x + topFace * 0.22,
          y: deckThickness + 0.55,
          z: z - topFace * 0.2,
          roughness: 0.24,
          metalness: 0.18,
        });
      }

      if (bonus) {
        addCylinder(THREE, group, {
          radiusTop: Math.max(2.6, topFace * 0.14),
          radiusBottom: Math.max(2.6, topFace * 0.14),
          height: 0.8,
          color: rewardColor(bonus),
          x: x + topFace * 0.22,
          y: deckThickness + 0.55,
          z: z + topFace * 0.2,
          roughness: 0.24,
          metalness: 0.18,
        });
      }
    }
  }

  const marbleCell = findMarbleCell(grid, start);
  const marbleFrame = getVerticalCellFrame(model, marbleCell.row, marbleCell.col);
  addSphere(THREE, group, {
    radius: marbleRadius,
    color: 0xe0e5ef,
    x: -FRAME_OUTER / 2 + marbleFrame.centerX,
    y: deckThickness + marbleRadius + 0.4,
    z: -FRAME_OUTER / 2 + marbleFrame.centerY,
    roughness: 0.12,
    metalness: 0.85,
  });

  if (end) {
    const endFrame = getVerticalCellFrame(model, end.row, end.col);
    addCylinder(THREE, group, {
      radiusTop: Math.max(3, topFace * 0.18),
      radiusBottom: Math.max(3, topFace * 0.18),
      height: 1.4,
      color: 0x63c4ff,
      x: -FRAME_OUTER / 2 + endFrame.centerX,
      y: deckThickness + 0.8,
      z: -FRAME_OUTER / 2 + endFrame.centerY,
      roughness: 0.28,
      metalness: 0.2,
    });
  }

  addModuleGuides(THREE, group, model, 1.2, 0.14);
  const rimOffset = -FRAME_OUTER / 2 + model.rim / 2;
  addBox(THREE, group, { width: FRAME_OUTER, height: 5, depth: model.rim, color: COLORS.frame, x: 0, y: 2.5, z: rimOffset });
  addBox(THREE, group, { width: FRAME_OUTER, height: 5, depth: model.rim, color: COLORS.frame, x: 0, y: 2.5, z: -rimOffset });
  addBox(THREE, group, { width: model.rim, height: 5, depth: FRAME_OUTER - 2 * model.rim, color: COLORS.frame, x: rimOffset, y: 2.5, z: 0 });
  addBox(THREE, group, { width: model.rim, height: 5, depth: FRAME_OUTER - 2 * model.rim, color: COLORS.frame, x: -rimOffset, y: 2.5, z: 0 });
  return { group, target: { x: frameCenter, y: marbleRadius + previewPhase * 0.01, z: frameCenter } };
}
