import test from "node:test";
import assert from "node:assert/strict";
import { build, describe, nested, shadow, total } from "./src/service.ts";

test("imports really work", () => {
  assert.equal(build(4).count, 8);
  assert.equal(describe(3), "{ count: 6 }");
  assert.equal(nested(), 8);
  assert.equal(shadow((value: number) => value + 1), 4);
  assert.equal(total(build(2)), 4);
});
