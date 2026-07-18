const assert = require("assert");
const logic = require("../arcade/editor/logic.js");

assert.strictEqual(logic.cellKeys.length, 144);
assert.deepStrictEqual(logic.keyToRowCol("L12"), [11, 11]);
assert.deepStrictEqual(logic.neighbors("A1").sort(), ["A2", "B1"]);

const cycle = {
  value: 1,
  color: "#4DFF00",
  dynamic: {
    type: "cycle",
    intervalSeconds: 1,
    pattern: [
      { value: 1, color: "#4DFF00" },
      { value: 0, color: "#F49400" },
    ],
  },
};
assert.strictEqual(logic.dynamicState(cycle, 0.5).value, 1);
assert.strictEqual(logic.dynamicState(cycle, 1.1).value, 0);

const trap = {
  value: 0,
  color: "#F49400",
  dynamic: {
    type: "delayed_trap",
    armDelaySeconds: 2,
    warnDurationSeconds: 2,
    initialIntervalSeconds: 1,
    minIntervalSeconds: 0.1,
    trapColor: "#FF0000",
    floorColor: "#F49400",
  },
};
assert.strictEqual(logic.dynamicState(trap, 1).value, 0);
assert.strictEqual(logic.dynamicState(trap, 4.1).value, -1);
console.log("maze editor logic tests passed");
