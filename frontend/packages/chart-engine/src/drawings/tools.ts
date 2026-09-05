/**
 * CH-4 — pure create/move/delete logic for the five drawing tools.
 *
 * Nothing here mutates: every function returns a new `Drawing` or
 * `DrawingCollection` and validates its result, so an invalid drawing can
 * never enter a collection. Errors surface as `DrawingError`; there is no
 * silent no-op path (unknown id, duplicate id, out-of-range anchor all throw).
 */

import {
  DEFAULT_FIBONACCI_LEVELS,
  DrawingError,
  assertValidDrawing,
  hasPoints,
  type Drawing,
  type DrawingCollection,
  type DrawingDelta,
  type DrawingPoint,
  type DrawingStyle,
  type FibonacciDrawing,
  type HorizontalLineDrawing,
  type RectangleDrawing,
  type TrendLineDrawing,
  type TwoPointDrawing,
  type VerticalLineDrawing,
} from "./model";

export interface DrawingOptions {
  readonly locked?: boolean;
  readonly style?: DrawingStyle;
}

/** Copies only the options that are present, keeping drawings free of `undefined` keys. */
function withOptions<T extends Drawing>(drawing: T, options: DrawingOptions): T {
  const next: Drawing = { ...drawing };
  if (options.locked !== undefined) (next as { locked?: boolean }).locked = options.locked;
  if (options.style !== undefined) (next as { style?: DrawingStyle }).style = { ...options.style };
  assertValidDrawing(next);
  return next as T;
}

function clonePoint(p: DrawingPoint): DrawingPoint {
  return { time: p.time, price: p.price };
}

// ── creation ────────────────────────────────────────────────────────────────

export function createTrendLine(
  id: string,
  p1: DrawingPoint,
  p2: DrawingPoint,
  options: DrawingOptions = {},
): TrendLineDrawing {
  return withOptions({ id, kind: "trendline", points: [clonePoint(p1), clonePoint(p2)] }, options);
}

export function createHorizontalLine(
  id: string,
  price: number,
  options: DrawingOptions = {},
): HorizontalLineDrawing {
  return withOptions({ id, kind: "horizontal-line", price }, options);
}

export function createVerticalLine(id: string, time: number, options: DrawingOptions = {}): VerticalLineDrawing {
  return withOptions({ id, kind: "vertical-line", time }, options);
}

export function createRectangle(
  id: string,
  p1: DrawingPoint,
  p2: DrawingPoint,
  options: DrawingOptions = {},
): RectangleDrawing {
  return withOptions({ id, kind: "rectangle", points: [clonePoint(p1), clonePoint(p2)] }, options);
}

export function createFibonacci(
  id: string,
  p1: DrawingPoint,
  p2: DrawingPoint,
  levels: readonly number[] = DEFAULT_FIBONACCI_LEVELS,
  options: DrawingOptions = {},
): FibonacciDrawing {
  return withOptions(
    { id, kind: "fibonacci", points: [clonePoint(p1), clonePoint(p2)], levels: [...levels] },
    options,
  );
}

// ── editing ─────────────────────────────────────────────────────────────────

function shiftPoint(p: DrawingPoint, delta: DrawingDelta): DrawingPoint {
  return { time: p.time + delta.time, price: p.price + delta.price };
}

/** Translates every anchor by `delta` (chart-space). Locked drawings throw. */
export function moveDrawing<T extends Drawing>(drawing: T, delta: DrawingDelta): T {
  assertUnlocked(drawing);
  let next: Drawing;
  switch (drawing.kind) {
    case "horizontal-line":
      next = { ...drawing, price: drawing.price + delta.price };
      break;
    case "vertical-line":
      next = { ...drawing, time: drawing.time + delta.time };
      break;
    default:
      next = {
        ...drawing,
        points: [shiftPoint(drawing.points[0], delta), shiftPoint(drawing.points[1], delta)],
      };
  }
  assertValidDrawing(next);
  return next as T;
}

/**
 * Replaces one anchor. Two-point drawings accept index 0 or 1; horizontal
 * lines use `point.price`, vertical lines use `point.time` (index must be 0).
 */
export function moveAnchor<T extends Drawing>(drawing: T, anchorIndex: number, point: DrawingPoint): T {
  assertUnlocked(drawing);
  const count = anchorCount(drawing);
  if (!Number.isInteger(anchorIndex) || anchorIndex < 0 || anchorIndex >= count) {
    throw new DrawingError(
      "CHART_DRAWING_ANCHOR_OUT_OF_RANGE",
      drawing.id,
      `anchor ${anchorIndex} out of range [0, ${count})`,
    );
  }
  let next: Drawing;
  switch (drawing.kind) {
    case "horizontal-line":
      next = { ...drawing, price: point.price };
      break;
    case "vertical-line":
      next = { ...drawing, time: point.time };
      break;
    default: {
      const points: [DrawingPoint, DrawingPoint] = [drawing.points[0], drawing.points[1]];
      points[anchorIndex] = clonePoint(point);
      next = { ...drawing, points };
    }
  }
  assertValidDrawing(next);
  return next as T;
}

export function anchorCount(drawing: Drawing): number {
  return hasPoints(drawing) ? 2 : 1;
}

export function setLocked<T extends Drawing>(drawing: T, locked: boolean): T {
  return { ...drawing, locked };
}

function assertUnlocked(drawing: Drawing): void {
  if (drawing.locked === true) {
    throw new DrawingError("CHART_DRAWING_INVALID", drawing.id, "drawing is locked");
  }
}

// ── derived geometry ────────────────────────────────────────────────────────

export interface RectangleBounds {
  readonly timeFrom: number;
  readonly timeTo: number;
  readonly priceLow: number;
  readonly priceHigh: number;
}

/** Normalises the two corners regardless of drag direction. */
export function rectangleBounds(rect: RectangleDrawing): RectangleBounds {
  const [a, b] = rect.points;
  return {
    timeFrom: Math.min(a.time, b.time),
    timeTo: Math.max(a.time, b.time),
    priceLow: Math.min(a.price, b.price),
    priceHigh: Math.max(a.price, b.price),
  };
}

export interface FibonacciLevelPrice {
  readonly level: number;
  readonly price: number;
}

/** Price of each level: p1.price + level × (p2.price − p1.price). */
export function fibonacciLevelPrices(fib: FibonacciDrawing): readonly FibonacciLevelPrice[] {
  const [p1, p2] = fib.points;
  const span = p2.price - p1.price;
  return fib.levels.map((level) => ({ level, price: p1.price + level * span }));
}

/** Slope of a two-point drawing in price-per-time; `null` when vertical. */
export function trendLineSlope(drawing: TwoPointDrawing): number | null {
  const [a, b] = drawing.points;
  const dt = b.time - a.time;
  return dt === 0 ? null : (b.price - a.price) / dt;
}

// ── collection ──────────────────────────────────────────────────────────────

export function findDrawing(collection: DrawingCollection, id: string): Drawing | undefined {
  return collection.find((d) => d.id === id);
}

export function addDrawing(collection: DrawingCollection, drawing: Drawing): DrawingCollection {
  assertValidDrawing(drawing);
  if (findDrawing(collection, drawing.id) !== undefined) {
    throw new DrawingError("CHART_DRAWING_DUPLICATE", drawing.id, "id already in collection");
  }
  return [...collection, drawing];
}

/** Replaces the drawing with the same id in place (order preserved). */
export function updateDrawing(collection: DrawingCollection, drawing: Drawing): DrawingCollection {
  assertValidDrawing(drawing);
  const idx = indexOfOrThrow(collection, drawing.id);
  const next = collection.slice();
  next[idx] = drawing;
  return next;
}

export function removeDrawing(collection: DrawingCollection, id: string): DrawingCollection {
  const idx = indexOfOrThrow(collection, id);
  return [...collection.slice(0, idx), ...collection.slice(idx + 1)];
}

function indexOfOrThrow(collection: DrawingCollection, id: string): number {
  const idx = collection.findIndex((d) => d.id === id);
  if (idx < 0) throw new DrawingError("CHART_DRAWING_UNKNOWN", id, "no drawing with this id");
  return idx;
}
