const assert = require("assert");
const {
  keyToRowCol,
  rowColToKey,
  seededRandom,
  reachable,
} = require("../arcade/editor/logic.js");

assert.deepStrictEqual(keyToRowCol("A1"), [0, 0]);
assert.deepStrictEqual(keyToRowCol("L12"), [11, 11]);
assert.strictEqual(rowColToKey(5, 6), "G6");

const a = seededRandom(42);
const b = seededRandom(42);
assert.deepStrictEqual(
  Array.from({ length: 10 }, () => a()),
  Array.from({ length: 10 }, () => b())
);

const cells = {};
for (let row = 0; row < 12; row++) {
  for (let col = 0; col < 12; col++) {
    cells[rowColToKey(row, col)] = { value: col === 5 ? 1 : 0 };
  }
}
const left = reachable("A1", cells);
assert(left.has("E12"));
assert(!left.has("L12"));

console.log("editor logic tests passed");
