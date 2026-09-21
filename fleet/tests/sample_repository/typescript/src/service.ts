import { inspect } from "node:util";
import { Basket as Box } from "./index.ts";
import { double as twice } from "./model.ts";
import * as model from "./model.ts";
import type { Countable } from "./model.ts";

/** Build a basket through a class alias re-exported by the barrel. */
export function build(count: number): Box {
  return new Box(twice(count));
}

export function describe(count: number): string {
  return inspect({ count: model.double(count) });
}

/** The parameter shadows the imported `twice`; the call below is the
 * parameter, not the import. */
export function shadow(twice: (value: number) => number): number {
  return twice(3);
}

export function nested(): number {
  function run(): number {
    return twice(4);
  }

  return run();
}

export function total(basket: Countable): number {
  return basket.count;
}
