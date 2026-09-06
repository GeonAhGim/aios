import { describe, expect, it } from "vitest";
import { FillBetweenError, computeFillSegments } from "../fillBetween";

describe("computeFillSegments", () => {
  it("bollinger-style band: base stays above target the whole way — one 'above' segment", () => {
    const base = [
      { time: 1, value: 110 },
      { time: 2, value: 112 },
      { time: 3, value: 108 },
    ];
    const target = [
      { time: 1, value: 90 },
      { time: 2, value: 91 },
      { time: 3, value: 89 },
    ];

    const segments = computeFillSegments(base, target);

    expect(segments).toHaveLength(1);
    expect(segments[0]!.direction).toBe("above");
    expect(segments[0]!.points.map((p) => p.time)).toEqual([1, 2, 3]);
  });

  it("ichimoku-style cloud: base crosses target — splits into above/below segments at the crossing", () => {
    const base = [
      { time: 1, value: 100 },
      { time: 2, value: 120 }, // crosses target between t1 and t2, then again between t2 and t3
      { time: 3, value: 90 },
    ];
    const target = [
      { time: 1, value: 110 },
      { time: 2, value: 110 },
      { time: 3, value: 110 },
    ];

    const segments = computeFillSegments(base, target);

    expect(segments.map((s) => s.direction)).toEqual(["below", "above", "below"]);
    // Crossing points are interpolated, not snapped to an existing bar.
    expect(segments[0]!.points.at(-1)!.time).toBeGreaterThan(1);
    expect(segments[0]!.points.at(-1)!.time).toBeLessThan(2);
    expect(segments[1]!.points[0]).toEqual(segments[0]!.points.at(-1));
  });

  it("returns no segments for two empty series", () => {
    expect(computeFillSegments([], [])).toEqual([]);
  });

  it("rejects series of different lengths (negative)", () => {
    const base = [{ time: 1, value: 1 }];
    const target = [
      { time: 1, value: 1 },
      { time: 2, value: 2 },
    ];
    expect(() => computeFillSegments(base, target)).toThrow(FillBetweenError);
    try {
      computeFillSegments(base, target);
    } catch (err) {
      expect((err as FillBetweenError).code).toBe("FILL_BETWEEN_LENGTH_MISMATCH");
    }
  });

  it("rejects series whose timestamps don't line up pairwise (negative)", () => {
    const base = [{ time: 1, value: 1 }];
    const target = [{ time: 2, value: 1 }];
    expect(() => computeFillSegments(base, target)).toThrow(/FILL_BETWEEN_TIME_MISMATCH/);
  });
});
