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

  function backIntent(state, overlayOpen = false) {
    if (overlayOpen) return "close-overlay";
    if (state === "initials" || state === "level_select") return "abandon";
    if (state === "leaderboard") return "continue";
    if (["setup", "hardware_fault", "attract"].includes(state)) return null;
    return "open-overlay";
  }

  return { shiftInitials, backIntent };
});
