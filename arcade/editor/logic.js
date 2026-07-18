(function (root) {
  "use strict";
  const cellKeys = Array.from({ length: 12 }, (_, row) =>
    Array.from({ length: 12 }, (_, col) => `${String.fromCharCode(65 + col)}${row + 1}`)
  ).flat();

  function keyToRowCol(key) {
    const match = /^([A-L])(1[0-2]|[1-9])$/i.exec(String(key));
    if (!match) throw new Error(`Invalid cell ${key}`);
    return [Number(match[2]) - 1, match[1].toUpperCase().charCodeAt(0) - 65];
  }

  function rowColToKey(row, col) {
    if (row < 0 || row > 11 || col < 0 || col > 11) return null;
    return `${String.fromCharCode(65 + col)}${row + 1}`;
  }

  function neighbors(key) {
    const [row, col] = keyToRowCol(key);
    return [[row - 1, col], [row + 1, col], [row, col - 1], [row, col + 1]]
      .map(([r, c]) => rowColToKey(r, c)).filter(Boolean);
  }

  function dynamicState(cell, elapsedSeconds) {
    const dynamic = cell.dynamic;
    if (!dynamic) return cell;
    if ((dynamic.type || "cycle") === "cycle") {
      const pattern = dynamic.pattern || [];
      const interval = Number(dynamic.intervalSeconds) || 1;
      if (!pattern.length) return cell;
      return pattern[Math.floor(elapsedSeconds / interval) % pattern.length];
    }
    const arm = Number(dynamic.armDelaySeconds) || 0;
    const warning = Number(dynamic.warnDurationSeconds) || 1;
    if (elapsedSeconds < arm) return cell;
    if (elapsedSeconds >= arm + warning) {
      return { value: -1, color: dynamic.trapColor || "#FF0000" };
    }
    const progress = (elapsedSeconds - arm) / warning;
    const initial = Number(dynamic.initialIntervalSeconds) || 1;
    const minimum = Number(dynamic.minIntervalSeconds) || 0.1;
    const interval = Math.max(minimum, initial - progress * (initial - minimum));
    const on = Math.floor((elapsedSeconds - arm) / interval) % 2 === 0;
    return { value: 0, color: on ? dynamic.trapColor : dynamic.floorColor };
  }

  const api = { cellKeys, keyToRowCol, rowColToKey, neighbors, dynamicState };
  root.MazeEditorLogic = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
