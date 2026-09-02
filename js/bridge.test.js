"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

test("pinned package versions are installed", () => {
  assert.equal(require("@microsoft/powerquery-parser/package.json").version, "2.0.0");
  assert.equal(require("@microsoft/powerquery-formatter/package.json").version, "1.0.0");
});
