export const DPR = window.devicePixelRatio || 1;
export const FRAME_OUTER = 914.4;
export const MODULES = 3;

export const COLORS = Object.freeze({
  frame: 0x3f6278,
  fixedWall: 0x8e715f,
  fixedFloor: 0x3f7896,
  open: 0x6e9946,
  blocker: 0xc8a27f,
  actuated: 0xd76a50,
  hole: 0x0f1116,
  holeRing: 0xff6f61,
  deck: 0x273140,
  housing: 0x334455,
  linkage: 0xf4c26f,
  translucentDeck: 0x263342,
});

export function formatNumber(value, digits = 1) {
  const numeric = Number(value);
  if (Number.isInteger(numeric)) return `${numeric}`;
  return numeric.toFixed(digits).replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
}

export function formatMm(value, digits = 1) {
  return `${formatNumber(value, digits)}mm`;
}

export function formatMmBare(value, digits = 1) {
  return formatNumber(value, digits);
}

export function formatPercent(value, digits = 0) {
  return `${formatNumber(value * 100, digits)}%`;
}

export function formatDegrees(value, digits = 0) {
  return `${formatNumber(value, digits)}deg`;
}
