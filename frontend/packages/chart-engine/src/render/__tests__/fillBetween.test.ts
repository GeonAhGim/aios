import { describe, expect, it } from "vitest";
import { FillBetweenError, computeFillSegments } from "../fillBetween";
import type { FillSeriesPoint } from "../fillBetween";
import { type FillCorruption, FILL_CORRUPTIONS, createRng, genSeries, pickCorruptedFillSeries } from "./arbitraries";

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

function expectedFillCorruptionCode(corruption: FillCorruption): string {
  return corruption === "length_mismatch" ? "FILL_BETWEEN_LENGTH_MISMATCH" : "FILL_BETWEEN_TIME_MISMATCH";
}

describe("failure injection: randomized malformed base/target series fuzz", () => {
  // computeFillSegments is a pure value-space function with no I/O, so mocked
  // network/DB failure injection is structurally impossible (per DEPTH_CH
  // audit, task-2729, for this exact leaf and its chart-engine pure-domain
  // siblings). This seeded fuzzer stands in for it: it targets the
  // malformed-series-pair domain a mismatched indicator computation (two
  // outputs desynced by a partial recompute) could hand this function.
  it("fail-closed with the correct error code for 200 seeded corrupted series pairs", () => {
    const rng = createRng(0xf111);
    const exercised = new Set<FillCorruption>();
    for (let i = 0; i < 200; i++) {
      const { corruption, base, target } = pickCorruptedFillSeries(rng);
      exercised.add(corruption);
      expect(() => computeFillSegments(base, target), `corruption=${corruption} case ${i}`).toThrow(FillBetweenError);
      try {
        computeFillSegments(base, target);
        expect.unreachable();
      } catch (err) {
        expect((err as FillBetweenError).code, `corruption=${corruption} case ${i}`).toBe(expectedFillCorruptionCode(corruption));
      }
    }
    expect(exercised.size).toBe(FILL_CORRUPTIONS.length);
  });
});

describe("performance: numeric ms budget for bulk crossing computation", () => {
  it("computes fill segments for a 10,000-point frequently-crossing series within a fixed ms budget", () => {
    const rng = createRng(0x2000);
    const base: FillSeriesPoint[] = [];
    const target: FillSeriesPoint[] = [];
    for (let i = 0; i < 10_000; i++) {
      base.push({ time: i, value: Math.sin(i / 7) * 10 + (rng.next() - 0.5) });
      target.push({ time: i, value: Math.sin(i / 7 + 1) * 10 });
    }

    const start = performance.now();
    const segments = computeFillSegments(base, target);
    const elapsedMs = performance.now() - start;

    expect(segments.length).toBeGreaterThan(10);
    // Generous fixed budget (not a relative ratchet): a regression that made
    // the single linear scan accidentally quadratic (e.g. re-scanning from
    // the start on every crossing) would blow well past this for 10,000 bars.
    expect(elapsedMs).toBeLessThan(1000);
  });
});

describe("gate red reproduction: crossing interpolation", () => {
  /** Mimics a pre-hardening version that snaps a segment boundary to the
   * nearest existing bar instead of interpolating the exact crossing time. A
   * mutant, not part of the shipped module. */
  function legacySnapToNearestBar(base: readonly FillSeriesPoint[], target: readonly FillSeriesPoint[]): number[] {
    const boundaries: number[] = [];
    let currentDirection = base[0]!.value > target[0]!.value ? "above" : "below";
    for (let i = 1; i < base.length; i++) {
      const nextDirection = base[i]!.value > target[i]!.value ? "above" : "below";
      if (nextDirection !== currentDirection) {
        boundaries.push(base[i]!.time); // snapped, not interpolated
        currentDirection = nextDirection;
      }
    }
    return boundaries;
  }

  const base = [
    { time: 0, value: 100 },
    { time: 10, value: 120 },
  ];
  const target = [
    { time: 0, value: 110 },
    { time: 10, value: 110 },
  ];

  it("red: a naive implementation snaps the crossing boundary to bar 10 instead of the true crossing time", () => {
    const boundaries = legacySnapToNearestBar(base, target);
    expect(boundaries).toEqual([10]);
  });

  it("green: the shipped computeFillSegments interpolates the crossing strictly between bar 0 and bar 10", () => {
    const segments = computeFillSegments(base, target);
    const crossingTime = segments[0]!.points.at(-1)!.time;
    expect(crossingTime).toBeGreaterThan(0);
    expect(crossingTime).toBeLessThan(10);
  });
});

describe("D3 — property-based invariants & multi-instance isolation", () => {
  it("property: 300 seeded random series produce contiguous, correctly-alternating segments", () => {
    const rng = createRng(0xd3d3);
    for (let i = 0; i < 300; i++) {
      const length = rng.int(2, 30);
      const base = genSeries(rng, length, 100);
      const target = genSeries(rng, length, 100);
      const segments = computeFillSegments(base, target);

      expect(segments.length, `case ${i}`).toBeGreaterThan(0);
      expect(segments[0]!.points[0]!.time, `case ${i}`).toBe(base[0]!.time);
      expect(segments.at(-1)!.points.at(-1)!.time, `case ${i}`).toBe(base.at(-1)!.time);
      for (let s = 0; s < segments.length - 1; s++) {
        // Adjacent segments must meet at the exact same boundary point (no gap, no overlap)...
        expect(segments[s]!.points.at(-1), `case ${i} boundary ${s}`).toEqual(segments[s + 1]!.points[0]);
        // ...and a new segment is only created on a real direction flip.
        expect(segments[s]!.direction, `case ${i} boundary ${s}`).not.toBe(segments[s + 1]!.direction);
      }
    }
  });

  it("multi-instance isolation (D3): two independent series pairs computed interleaved never leak state", () => {
    const rng = createRng(0xd3d4);
    const seriesA = { base: genSeries(rng, 20, 100), target: genSeries(rng, 20, 50) };
    const seriesB = { base: genSeries(rng, 15, -50), target: genSeries(rng, 15, -100) };

    const segmentsA1 = computeFillSegments(seriesA.base, seriesA.target);
    const segmentsB1 = computeFillSegments(seriesB.base, seriesB.target);
    const segmentsA2 = computeFillSegments(seriesA.base, seriesA.target);
    const segmentsB2 = computeFillSegments(seriesB.base, seriesB.target);

    expect(segmentsA2).toEqual(segmentsA1);
    expect(segmentsB2).toEqual(segmentsB1);
  });

  it("adversarial: near-equal (but not exactly equal) values just barely on either side still resolve a direction", () => {
    const base = [
      { time: 0, value: 100 },
      { time: 1, value: 100 + 1e-9 },
    ];
    const target = [
      { time: 0, value: 100 },
      { time: 1, value: 100 - 1e-9 },
    ];
    const segments = computeFillSegments(base, target);
    expect(segments.every((s) => s.direction !== undefined)).toBe(true);
  });
});
