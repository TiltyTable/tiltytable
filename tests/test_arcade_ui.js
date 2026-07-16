const assert = require("assert");
const {
  shiftInitials,
  backIntent,
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

console.log("arcade UI logic tests passed");
