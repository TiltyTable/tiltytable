(function (root) {
  "use strict";
  const cellKeys = Array.from({ length: 12 }, (_, row) =>
    Array.from({ length: 12 }, (_, col) => `${String.fromCharCode(65 + col)}${row + 1}`)
  ).flat();

  function keyToRowCol(key) {
    const match = /^([A-L])(1[0-2]|[1-9])$/.exec(String(key).toUpperCase());
    if (!match) throw new Error(`Invalid cell ${key}`);
    return [Number(match[2]) - 1, match[1].charCodeAt(0) - 65];
  }
  function rowColToKey(row, col) {
    if (row < 0 || row > 11 || col < 0 || col > 11) throw new Error("Cell outside board");
    return `${String.fromCharCode(65 + col)}${row + 1}`;
  }
  function seededRandom(seed) {
    let value = (Number(seed) || 1) >>> 0;
    return () => {
      value = (value * 1664525 + 1013904223) >>> 0;
      return value / 4294967296;
    };
  }
  function neighbors(key) {
    const [row, col] = keyToRowCol(key);
    return [[row - 1, col], [row + 1, col], [row, col - 1], [row, col + 1]]
      .filter(([r, c]) => r >= 0 && r < 12 && c >= 0 && c < 12)
      .map(([r, c]) => rowColToKey(r, c));
  }
  function reachable(start, cells) {
    if (!cells[start] || cells[start].value !== 0) return new Set();
    const seen = new Set([start]), queue = [start];
    while (queue.length) {
      const current = queue.shift();
      neighbors(current).forEach((next) => {
        if (!seen.has(next) && cells[next].value === 0) { seen.add(next); queue.push(next); }
      });
    }
    return seen;
  }
  function reachableDistances(start, cells) {
    if (!cells[start] || cells[start].value !== 0) return {};
    const distance = { [start]: 0 }, queue = [start];
    while (queue.length) {
      const current = queue.shift();
      neighbors(current).forEach((next) => {
        if (distance[next] === undefined && cells[next].value === 0) {
          distance[next] = distance[current] + 1;
          queue.push(next);
        }
      });
    }
    return distance;
  }
  function moveCell(start, deltaRow, deltaCol, cells) {
    const [row, col] = keyToRowCol(start);
    const nextRow = row + deltaRow, nextCol = col + deltaCol;
    if (nextRow < 0 || nextRow > 11 || nextCol < 0 || nextCol > 11) {
      return { key: start, blocked: true };
    }
    const key = rowColToKey(nextRow, nextCol);
    if (cells[key]?.value === 1) return { key: start, blocked: true };
    return { key, blocked: false };
  }
  const api = { cellKeys, keyToRowCol, rowColToKey, seededRandom, reachable, reachableDistances, moveCell };
  root.TiltyEditorLogic = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
