export interface Countable {
  readonly count: number;
}

/** Count fruit in one basket. */
export class Basket implements Countable {
  readonly count: number;

  constructor(count: number) {
    this.count = count;
  }
}

export function double(value: number): number {
  return value * 2;
}
