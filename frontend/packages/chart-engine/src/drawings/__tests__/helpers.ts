import { expect } from "vitest";
import { DrawingError } from "../model";

/** Asserts `fn` throws a `DrawingError` with `code` (and optionally `id`); returns it. */
export function expectDrawingError(fn: () => unknown, code: DrawingError["code"], id?: string): DrawingError {
  let caught: unknown;
  try {
    fn();
  } catch (error) {
    caught = error;
  }
  expect(caught).toBeInstanceOf(DrawingError);
  const err = caught as DrawingError;
  expect(err.code).toBe(code);
  if (id !== undefined) expect(err.drawingId).toBe(id);
  return err;
}

/** Deep equality where numbers may differ by floating-point rounding (rel 1e-9). */
export function approxEqual(a: unknown, b: unknown): boolean {
  if (typeof a === "number" && typeof b === "number") {
    if (a === b) return true;
    const scale = Math.max(Math.abs(a), Math.abs(b));
    return Math.abs(a - b) <= 1e-9 * Math.max(scale, 1);
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => approxEqual(v, b[i]));
  }
  if (typeof a === "object" && a !== null && typeof b === "object" && b !== null) {
    const ka = Object.keys(a).sort();
    const kb = Object.keys(b).sort();
    if (ka.length !== kb.length || ka.some((k, i) => k !== kb[i])) return false;
    return ka.every((k) => approxEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
  }
  return Object.is(a, b);
}
