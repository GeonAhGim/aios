import { describe, expect, it } from "vitest";
import { DEFAULT_FIBONACCI_LEVELS, assertValidDrawing, type Drawing, type DrawingCollection } from "../model";
import {
  addDrawing,
  anchorCount,
  createFibonacci,
  createHorizontalLine,
  createRectangle,
  createTrendLine,
  createVerticalLine,
  fibonacciLevelPrices,
  findDrawing,
  moveAnchor,
  moveDrawing,
  rectangleBounds,
  removeDrawing,
  setLocked,
  trendLineSlope,
  updateDrawing,
} from "../tools";
import { createRng, genCollection, genDrawing } from "./arbitraries";
import { approxEqual, expectDrawingError } from "./helpers";

const p1 = { time: 10, price: 100 };
const p2 = { time: 20, price: 120 };

describe("creation (5 tools)", () => {
  it("creates each kind with only the fields that were given", () => {
    expect(createTrendLine("t", p1, p2)).toEqual({ id: "t", kind: "trendline", points: [p1, p2] });
    expect(createHorizontalLine("h", 5)).toEqual({ id: "h", kind: "horizontal-line", price: 5 });
    expect(createVerticalLine("v", 7)).toEqual({ id: "v", kind: "vertical-line", time: 7 });
    expect(createRectangle("r", p1, p2)).toEqual({ id: "r", kind: "rectangle", points: [p1, p2] });
    expect(createFibonacci("f", p1, p2)).toEqual({
      id: "f",
      kind: "fibonacci",
      points: [p1, p2],
      levels: DEFAULT_FIBONACCI_LEVELS,
    });
    expect(Object.keys(createTrendLine("t", p1, p2))).toEqual(["id", "kind", "points"]);
  });

  it("copies input points and levels so later mutation of inputs is isolated", () => {
    const src = { time: 1, price: 2 };
    const levels = [0, 1];
    const fib = createFibonacci("f", src, p2, levels);
    src.time = 999;
    levels.push(2);
    expect(fib.points[0]).toEqual({ time: 1, price: 2 });
    expect(fib.levels).toEqual([0, 1]);
  });

  it("applies locked/style options and validates them", () => {
    const line = createHorizontalLine("h", 1, { locked: true, style: { color: "red", lineWidth: 2 } });
    expect(line).toEqual({ id: "h", kind: "horizontal-line", price: 1, locked: true, style: { color: "red", lineWidth: 2 } });
    expectDrawingError(() => createHorizontalLine("h", 1, { style: { lineWidth: -1 } }), "CHART_DRAWING_INVALID", "h");
    expectDrawingError(() => createVerticalLine("v", Number.NaN), "CHART_DRAWING_INVALID", "v");
    expectDrawingError(() => createFibonacci("f", p1, p2, []), "CHART_DRAWING_INVALID", "f");
  });
});

describe("moveDrawing", () => {
  const delta = { time: 5, price: -10 };

  it("translates every anchor and leaves the original untouched", () => {
    const trend = createTrendLine("t", p1, p2);
    const moved = moveDrawing(trend, delta);
    expect(moved.points).toEqual([{ time: 15, price: 90 }, { time: 25, price: 110 }]);
    expect(trend.points).toEqual([p1, p2]);
    expect(moveDrawing(createHorizontalLine("h", 100), delta).price).toBe(90);
    expect(moveDrawing(createVerticalLine("v", 10), delta).time).toBe(15);
    expect(moveDrawing(createRectangle("r", p1, p2), delta).points[1]).toEqual({ time: 25, price: 110 });
    const fib = moveDrawing(createFibonacci("f", p1, p2), delta);
    expect(fib.points[0]).toEqual({ time: 15, price: 90 });
    expect(fib.levels).toEqual(DEFAULT_FIBONACCI_LEVELS);
  });

  it("ignores the irrelevant axis for single-axis lines", () => {
    expect(moveDrawing(createHorizontalLine("h", 1), { time: 1e9, price: 0 })).toEqual(createHorizontalLine("h", 1));
    expect(moveDrawing(createVerticalLine("v", 1), { time: 0, price: 1e9 })).toEqual(createVerticalLine("v", 1));
  });

  it("refuses to move a locked drawing and to produce non-finite anchors", () => {
    expectDrawingError(() => moveDrawing(setLocked(createHorizontalLine("h", 1), true), delta), "CHART_DRAWING_INVALID", "h");
    expectDrawingError(() => moveDrawing(createHorizontalLine("h", 1), { time: 0, price: Number.NaN }), "CHART_DRAWING_INVALID");
    expect(moveDrawing(setLocked(createHorizontalLine("h", 1), false), delta).price).toBe(-9);
  });

  it("property: moving by d then by -d restores the drawing (seeded)", () => {
    const rng = createRng(0xc0ffee);
    for (let i = 0; i < 200; i++) {
      const d = genDrawing(rng, `d${i}`);
      if (d.locked) continue;
      const delta = { time: rng.int(-1000, 1000), price: rng.int(-1000, 1000) };
      const back = moveDrawing(moveDrawing(d, delta), { time: -delta.time, price: -delta.price });
      expect(approxEqual(back, d), `seed case ${i}: ${JSON.stringify({ d, back })}`).toBe(true);
    }
  });
});

describe("moveAnchor", () => {
  it("replaces only the addressed anchor", () => {
    const trend = createTrendLine("t", p1, p2);
    expect(moveAnchor(trend, 1, { time: 30, price: 1 }).points).toEqual([p1, { time: 30, price: 1 }]);
    expect(moveAnchor(trend, 0, { time: 0, price: 0 }).points).toEqual([{ time: 0, price: 0 }, p2]);
    expect(moveAnchor(createHorizontalLine("h", 1), 0, { time: 99, price: 2 }).price).toBe(2);
    expect(moveAnchor(createVerticalLine("v", 1), 0, { time: 99, price: 2 }).time).toBe(99);
  });

  it("reports anchor count per kind and rejects out-of-range indices", () => {
    expect(anchorCount(createTrendLine("t", p1, p2))).toBe(2);
    expect(anchorCount(createHorizontalLine("h", 1))).toBe(1);
    expectDrawingError(() => moveAnchor(createTrendLine("t", p1, p2), 2, p1), "CHART_DRAWING_ANCHOR_OUT_OF_RANGE", "t");
    expectDrawingError(() => moveAnchor(createHorizontalLine("h", 1), 1, p1), "CHART_DRAWING_ANCHOR_OUT_OF_RANGE", "h");
    expectDrawingError(() => moveAnchor(createHorizontalLine("h", 1), -1, p1), "CHART_DRAWING_ANCHOR_OUT_OF_RANGE");
    expectDrawingError(() => moveAnchor(createHorizontalLine("h", 1), 0.5, p1), "CHART_DRAWING_ANCHOR_OUT_OF_RANGE");
    expectDrawingError(() => moveAnchor(setLocked(createVerticalLine("v", 1), true), 0, p1), "CHART_DRAWING_INVALID");
  });
});

describe("derived geometry", () => {
  it("normalises rectangle corners regardless of drag direction", () => {
    const bounds = { timeFrom: 10, timeTo: 20, priceLow: 100, priceHigh: 120 };
    expect(rectangleBounds(createRectangle("r", p1, p2))).toEqual(bounds);
    expect(rectangleBounds(createRectangle("r", p2, p1))).toEqual(bounds);
    expect(rectangleBounds(createRectangle("r", { time: 20, price: 100 }, { time: 10, price: 120 }))).toEqual(bounds);
  });

  it("computes fibonacci level prices from the p1→p2 span (works for downtrends)", () => {
    const up = fibonacciLevelPrices(createFibonacci("f", p1, p2, [0, 0.5, 1, 1.618]));
    expect(up).toEqual([
      { level: 0, price: 100 },
      { level: 0.5, price: 110 },
      { level: 1, price: 120 },
      { level: 1.618, price: 100 + 1.618 * 20 },
    ]);
    const down = fibonacciLevelPrices(createFibonacci("f", p2, p1, [0.5]));
    expect(down).toEqual([{ level: 0.5, price: 110 }]);
  });

  it("returns slope in price-per-time and null for vertical two-point drawings", () => {
    expect(trendLineSlope(createTrendLine("t", p1, p2))).toBe(2);
    expect(trendLineSlope(createTrendLine("t", p1, { time: 10, price: 500 }))).toBeNull();
  });
});

describe("collection ops", () => {
  const a = createHorizontalLine("a", 1);
  const b = createVerticalLine("b", 2);

  it("add/update/remove are pure and preserve order", () => {
    const empty: DrawingCollection = [];
    const one = addDrawing(empty, a);
    const two = addDrawing(one, b);
    expect(empty).toEqual([]);
    expect(one).toEqual([a]);
    expect(two).toEqual([a, b]);

    const updated = updateDrawing(two, createHorizontalLine("a", 9));
    expect(updated.map((d) => d.id)).toEqual(["a", "b"]);
    expect((updated[0] as { price: number }).price).toBe(9);
    expect(two[0]).toBe(a);

    expect(removeDrawing(two, "a")).toEqual([b]);
    expect(two).toEqual([a, b]);
    expect(findDrawing(two, "b")).toBe(b);
    expect(findDrawing(two, "zzz")).toBeUndefined();
  });

  it("throws instead of silently ignoring duplicate, unknown or invalid drawings", () => {
    expectDrawingError(() => addDrawing([a], createTrendLine("a", p1, p2)), "CHART_DRAWING_DUPLICATE", "a");
    expectDrawingError(() => updateDrawing([a], b), "CHART_DRAWING_UNKNOWN", "b");
    expectDrawingError(() => removeDrawing([a], "nope"), "CHART_DRAWING_UNKNOWN", "nope");
    const broken = { id: "x", kind: "horizontal-line", price: Number.NaN } as Drawing;
    expectDrawingError(() => addDrawing([], broken), "CHART_DRAWING_INVALID", "x");
    expectDrawingError(() => updateDrawing([a], { ...a, price: Number.POSITIVE_INFINITY }), "CHART_DRAWING_INVALID", "a");
  });

  it("property: every generated collection is valid and has unique ids (seeded)", () => {
    const rng = createRng(42);
    for (let i = 0; i < 50; i++) {
      const collection = genCollection(rng);
      collection.forEach(assertValidDrawing);
      expect(new Set(collection.map((d) => d.id)).size).toBe(collection.length);
    }
  });
});
