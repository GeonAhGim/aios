/**
 * Deterministic pseudo-random generators for property tests (no fast-check
 * dependency). Seeded mulberry32 so a failing case is reproducible by seed.
 */

import type { Drawing, DrawingCollection, DrawingPoint, DrawingStyle } from "../model";
import { DRAWING_KINDS } from "../model";
import {
  createFibonacci,
  createHorizontalLine,
  createRectangle,
  createTrendLine,
  createVerticalLine,
  type DrawingOptions,
} from "../tools";

export interface Rng {
  /** Uniform in [0, 1). */
  next(): number;
  int(min: number, maxInclusive: number): number;
  pick<T>(items: readonly T[]): T;
  bool(): boolean;
}

export function createRng(seed: number): Rng {
  let a = seed >>> 0;
  const next = (): number => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    next,
    int: (min, max) => min + Math.floor(next() * (max - min + 1)),
    pick: (items) => items[Math.floor(next() * items.length)]!,
    bool: () => next() < 0.5,
  };
}

/** Finite numbers spanning magnitudes, integers, negatives and fractions; never -0. */
export function genNumber(rng: Rng): number {
  const shape = rng.int(0, 4);
  let value: number;
  switch (shape) {
    case 0:
      value = rng.int(-1_000_000, 1_000_000);
      break;
    case 1:
      value = (rng.next() - 0.5) * 2 ** rng.int(-20, 40);
      break;
    case 2:
      value = Number((rng.next() * 100).toFixed(rng.int(0, 8)));
      break;
    case 3:
      value = rng.pick([0, 1, -1, Number.MAX_SAFE_INTEGER, Number.MIN_VALUE, 1e300, -1e300]);
      break;
    default:
      value = 1_700_000_000_000 + rng.int(0, 10_000_000);
  }
  return value === 0 ? 0 : value;
}

export function genPoint(rng: Rng): DrawingPoint {
  return { time: genNumber(rng), price: genNumber(rng) };
}

export function genStyle(rng: Rng): DrawingStyle {
  const style: { color?: string; lineWidth?: number } = {};
  if (rng.bool()) style.color = rng.pick(["#ff0000", "rgba(0, 0, 0, 0.5)", "red", "#00ff00aa"]);
  if (rng.bool()) style.lineWidth = rng.pick([1, 2, 0.5, 3.25]);
  return style;
}

export function genOptions(rng: Rng): DrawingOptions {
  const options: { locked?: boolean; style?: DrawingStyle } = {};
  if (rng.bool()) options.locked = rng.bool();
  if (rng.bool()) options.style = genStyle(rng);
  return options;
}

export function genDrawing(rng: Rng, id: string): Drawing {
  const kind = rng.pick(DRAWING_KINDS);
  const options = genOptions(rng);
  switch (kind) {
    case "trendline":
      return createTrendLine(id, genPoint(rng), genPoint(rng), options);
    case "horizontal-line":
      return createHorizontalLine(id, genNumber(rng), options);
    case "vertical-line":
      return createVerticalLine(id, genNumber(rng), options);
    case "rectangle":
      return createRectangle(id, genPoint(rng), genPoint(rng), options);
    case "fibonacci": {
      const levels = rng.bool()
        ? undefined
        : Array.from({ length: rng.int(1, 6) }, () => Number((rng.next() * 2 - 0.5).toFixed(3)) || 0);
      return createFibonacci(id, genPoint(rng), genPoint(rng), levels, options);
    }
  }
}

export function genCollection(rng: Rng, maxSize = 12): DrawingCollection {
  const size = rng.int(0, maxSize);
  return Array.from({ length: size }, (_, i) => genDrawing(rng, `d-${i}-${rng.int(0, 9999)}`));
}
