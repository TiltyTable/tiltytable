(function exposeUiLogic(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.ArcadeUiLogic = api;
})(typeof window !== "undefined" ? window : globalThis, function buildUiLogic() {
  function shiftInitials(initials, index, delta) {
    const letters = String(initials || "AAA").padEnd(3, "A").slice(0, 3).split("");
    const current = Math.max(0, letters[index].toUpperCase().charCodeAt(0) - 65);
    letters[index] = String.fromCharCode(65 + ((current + delta + 26) % 26));
    return letters.join("");
  }

  function backIntent(state, overlayOpen = false, mode = null) {
    if (overlayOpen) return "close-overlay";
    if (state === "initials" || state === "level_select") return "abandon";
    if (state === "rules" && mode === "practice") return "level-select";
    if (state === "leaderboard") return "continue";
    if (["setup", "hardware_fault", "attract"].includes(state)) return null;
    return "open-overlay";
  }

  function cabinetButtonIntent(
    previousConfirm,
    previousBack,
    nextConfirm,
    nextBack,
  ) {
    if (nextBack > previousBack) return "back";
    if (nextConfirm > previousConfirm) return "confirm";
    return null;
  }

  function cabinetNavigationKeys(previousUp, previousDown, nextUp, nextDown) {
    const keys = [];
    const upSteps = Math.min(4, Math.max(0, nextUp - previousUp));
    const downSteps = Math.min(4, Math.max(0, nextDown - previousDown));
    for (let index = 0; index < upSteps; index += 1) keys.push("ArrowUp");
    for (let index = 0; index < downSteps; index += 1) keys.push("ArrowDown");
    return keys;
  }

  function cellKeyToCoordinates(key) {
    const match = /^([A-L])(1[0-2]|[1-9])$/i.exec(String(key || ""));
    if (!match) return "—";
    const x = match[1].toUpperCase().charCodeAt(0) - 65;
    const y = Number(match[2]) - 1;
    return `(${x},${y})`;
  }

  function ballOverlayVisible(debugEnabled, state) {
    return Boolean(debugEnabled || state === "placement" || state === "playing");
  }

  function initialsConfirmIntent(cursor) {
    return Number(cursor) < 2 ? "next" : "submit";
  }

  return {
    shiftInitials,
    backIntent,
    cabinetButtonIntent,
    cabinetNavigationKeys,
    cellKeyToCoordinates,
    ballOverlayVisible,
    initialsConfirmIntent,
  };
});
