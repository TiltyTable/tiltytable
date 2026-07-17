const assert = require("assert");
const {
  shiftInitials,
  backIntent,
  cabinetButtonIntent,
  cabinetNavigationKeys,
  cellKeyToCoordinates,
  ballOverlayVisible,
  initialsConfirmIntent,
} = require("../arcade/static/ui_logic.js");

assert.strictEqual(shiftInitials("AAA", 0, 1), "BAA");
assert.strictEqual(shiftInitials("AAA", 1, -1), "AZA");
assert.strictEqual(shiftInitials("ZED", 0, 1), "AED");

assert.strictEqual(backIntent("playing"), "open-overlay");
assert.strictEqual(backIntent("placement"), "open-overlay");
assert.strictEqual(backIntent("level_select"), "abandon");
assert.strictEqual(backIntent("initials"), "abandon");
assert.strictEqual(backIntent("leaderboard"), "continue");
assert.strictEqual(backIntent("attract"), null);
assert.strictEqual(backIntent("playing", true), "close-overlay");
assert.strictEqual(backIntent("rules", false, "practice"), "level-select");
assert.strictEqual(backIntent("rules", false, "gauntlet"), "open-overlay");

assert.strictEqual(cabinetButtonIntent(0, 0, 1, 0), "confirm");
assert.strictEqual(cabinetButtonIntent(2, 4, 2, 5), "back");
assert.strictEqual(cabinetButtonIntent(2, 4, 2, 4), null);
assert.strictEqual(cabinetButtonIntent(2, 4, 3, 5), "back");
assert.deepStrictEqual(cabinetNavigationKeys(0, 0, 2, 0), ["ArrowUp", "ArrowUp"]);
assert.deepStrictEqual(cabinetNavigationKeys(2, 3, 2, 5), ["ArrowDown", "ArrowDown"]);
assert.deepStrictEqual(cabinetNavigationKeys(2, 3, 2, 3), []);

assert.strictEqual(cellKeyToCoordinates("A1"), "(0,0)");
assert.strictEqual(cellKeyToCoordinates("G6"), "(6,5)");
assert.strictEqual(cellKeyToCoordinates("L12"), "(11,11)");
assert.strictEqual(cellKeyToCoordinates("nope"), "—");
assert.strictEqual(ballOverlayVisible(false, "playing"), true);
assert.strictEqual(ballOverlayVisible(false, "placement"), true);
assert.strictEqual(ballOverlayVisible(false, "attract"), false);
assert.strictEqual(ballOverlayVisible(true, "attract"), true);
assert.strictEqual(initialsConfirmIntent(0), "next");
assert.strictEqual(initialsConfirmIntent(1), "next");
assert.strictEqual(initialsConfirmIntent(2), "submit");

console.log("arcade UI logic tests passed");
