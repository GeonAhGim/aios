import { describe, expect, it } from "vitest";
import type { CandleRecord, SeriesKey } from "@aios/shared-types";
import { AlignError, alignSeries } from "../align";
import { NormalizeError, normalizeToBase100 } from "../normalize";
import { SpreadError, spread } from "../spread";
import type { AlignedSeries } from "../align";
import { ADVERSARIAL_FINITE_CLOSE_VALUES, corruptSeries, createRng, genSeries } from "./arbitraries";

const BASE_KEY: SeriesKey = { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1d" };
const OTHER_KEY: SeriesKey = { venue: "BITGET", instrument_id: "ETHUSDT", timeframe: "1d" };
const KRW_KEY: SeriesKey = { venue: "KIS_KRX", instrument_id: "005930", timeframe: "1d" };

const DAY1 = "2024-01-01T00:00:00.000Z";
const DAY2 = "2024-01-02T00:00:00.000Z";
const DAY3 = "2024-01-03T00:00:00.000Z";
const DAY4 = "2024-01-04T00:00:00.000Z";
const DAY5 = "2024-01-05T00:00:00.000Z";

function candle(key: SeriesKey, openTime: string, closeTime: string, close: string): CandleRecord {
  return { key, open_time: openTime, close_time: closeTime, open: close, high: close, low: close, close, volume: "1", quote_volume: null };
}

// base: day1..day4, other (same venue): day1,day3,day4,day5 — other has no day2 candle.
const base: CandleRecord[] = [
  candle(BASE_KEY, DAY1, DAY2, "100"),
  candle(BASE_KEY, DAY2, DAY3, "105"),
  candle(BASE_KEY, DAY3, DAY4, "110"),
  candle(BASE_KEY, DAY4, DAY5, "115"),
];
const other: CandleRecord[] = [
  candle(OTHER_KEY, DAY1, DAY2, "50"),
  candle(OTHER_KEY, DAY3, DAY4, "55"),
  candle(OTHER_KEY, DAY4, DAY5, "60"),
  candle(OTHER_KEY, DAY5, "2024-01-06T00:00:00.000Z", "65"),
];

describe("alignSeries", () => {
  it("aligns on open_time within the overlap and leaves missing candles null (no forward-fill)", () => {
    const aligned = alignSeries(base, other);
    expect(aligned.points.map((p) => p.time)).toEqual([DAY1, DAY2, DAY3, DAY4]);
    expect(aligned.points[1]!.base?.close).toBe("105");
    expect(aligned.points[1]!.other).toBeNull(); // day2 missing from `other` — must stay null, not 50/55-filled.
    expect(aligned.points[3]!.base?.close).toBe("115");
    expect(aligned.points[3]!.other?.close).toBe("60");
  });

  it("rejects an empty base series", () => {
    expect(() => alignSeries([], other)).toThrow(AlignError);
    try {
      alignSeries([], other);
    } catch (e) {
      expect((e as AlignError).code).toBe("empty_base");
    }
  });

  it("rejects an empty other series", () => {
    expect(() => alignSeries(base, [])).toThrow(AlignError);
    try {
      alignSeries(base, []);
    } catch (e) {
      expect((e as AlignError).code).toBe("empty_other");
    }
  });

  it("rejects two series whose time ranges never overlap (zero candles in the intersection)", () => {
    const farFuture = [candle(OTHER_KEY, "2030-01-01T00:00:00.000Z", "2030-01-02T00:00:00.000Z", "1")];
    expect(() => alignSeries(base, farFuture)).toThrow(AlignError);
    try {
      alignSeries(base, farFuture);
    } catch (e) {
      expect((e as AlignError).code).toBe("no_overlap");
    }
  });
});

describe("normalizeToBase100", () => {
  it("rebases the series so the anchor candle reads 100", () => {
    const anchorMs = Date.parse(DAY1);
    const points = normalizeToBase100(base, anchorMs);
    expect(points[0]!.value).toBe(100);
    expect(points[1]!.value).toBeCloseTo(105);
    expect(points[3]!.value).toBeCloseTo(115);
  });

  it("leaves a single bad-close candle as null without discarding the rest of the series", () => {
    const dirty = [...base.slice(0, 2), candle(BASE_KEY, DAY3, DAY4, "not-a-number"), base[3]!];
    const points = normalizeToBase100(dirty, Date.parse(DAY1));
    expect(points[2]!.value).toBeNull();
    expect(points[3]!.value).toBeCloseTo(115);
  });

  it("rejects an empty series", () => {
    expect(() => normalizeToBase100([], Date.parse(DAY1))).toThrow(NormalizeError);
  });

  it("rejects an anchor time with no matching candle", () => {
    expect(() => normalizeToBase100(base, Date.parse("2020-01-01T00:00:00.000Z"))).toThrow(NormalizeError);
    try {
      normalizeToBase100(base, Date.parse("2020-01-01T00:00:00.000Z"));
    } catch (e) {
      expect((e as NormalizeError).code).toBe("anchor_not_found");
    }
  });

  it("rejects an anchor candle with a zero or unparseable close (fail-closed, not a bogus value)", () => {
    const zeroAnchor = [candle(BASE_KEY, DAY1, DAY2, "0"), base[1]!];
    expect(() => normalizeToBase100(zeroAnchor, Date.parse(DAY1))).toThrow(NormalizeError);
    try {
      normalizeToBase100(zeroAnchor, Date.parse(DAY1));
    } catch (e) {
      expect((e as NormalizeError).code).toBe("anchor_close_invalid");
    }
  });
});

describe("spread", () => {
  it("computes ratio and diff only over aligned points, nulling where either side is missing", () => {
    const aligned = alignSeries(base, other);

    const ratios = spread(aligned, "ratio");
    expect(ratios[0]!.value).toBeCloseTo(50 / 100);
    expect(ratios[1]!.value).toBeNull(); // day2: other missing.
    expect(ratios[3]!.value).toBeCloseTo(60 / 115);

    const diffs = spread(aligned, "diff");
    expect(diffs[0]!.value).toBeCloseTo(-50);
    expect(diffs[1]!.value).toBeNull();
  });

  it("rejects an empty aligned series", () => {
    const empty: AlignedSeries = { points: [] };
    expect(() => spread(empty, "diff")).toThrow(SpreadError);
    try {
      spread(empty, "diff");
    } catch (e) {
      expect((e as SpreadError).code).toBe("empty_aligned");
    }
  });

  it("rejects a hand-built series whose points are not strictly time-ordered", () => {
    const misaligned: AlignedSeries = {
      points: [
        { timeMs: Date.parse(DAY2), time: DAY2, base: base[1]!, other: other[0]! },
        { timeMs: Date.parse(DAY1), time: DAY1, base: base[0]!, other: other[0]! },
      ],
    };
    expect(() => spread(misaligned, "ratio")).toThrow(SpreadError);
    try {
      spread(misaligned, "ratio");
    } catch (e) {
      expect((e as SpreadError).code).toBe("misaligned");
    }
  });

  it("rejects raw-price comparison between two symbols with different quote scale/currency", () => {
    const krw = [candle(KRW_KEY, DAY1, DAY2, "70000"), candle(KRW_KEY, DAY2, DAY3, "71000")];
    const aligned = alignSeries(base, krw);
    expect(() => spread(aligned, "ratio")).toThrow(SpreadError);
    expect(() => spread(aligned, "diff")).toThrow(SpreadError);
    try {
      spread(aligned, "diff");
    } catch (e) {
      expect((e as SpreadError).code).toBe("quote_scale_mismatch");
    }
  });
});

describe("failure injection: randomized malformed-candle fuzz", () => {
  // True network/DB failure injection is structurally impossible here (these
  // three functions take in-memory arrays, not I/O) — the DEPTH_CH audit
  // (task-2729) accepted this for the sibling chart-engine pure-domain leaves
  // (1710, 1711, 1732, 1809). This is the closest analogue: a seeded fuzzer
  // that corrupts the malformed-input domain a real feed could hand these
  // functions, proving they fail closed (null, never a silent NaN/Infinity
  // leak, never an uncaught crash) rather than just documenting one example
  // of each corruption by hand.
  it("fail-closed for 200 seeded structural corruptions (never a silent NaN/Infinity leak)", () => {
    const rng = createRng(0xfa17);
    const exercised = new Set<string>();
    let cases = 0;
    for (let i = 0; i < 200; i++) {
      const size = rng.int(4, 12);
      const startMs = Date.parse(DAY1) + rng.int(0, 5) * 86_400_000;
      const baseSeries = genSeries(rng, BASE_KEY, startMs, size);
      const otherSeries = genSeries(rng, OTHER_KEY, startMs, size);
      const { series: corruptedBase, corruption } = corruptSeries(rng, baseSeries, 0);
      cases++;
      exercised.add(corruption);

      // normalizeToBase100 anchored on the untouched anchor candle (index 0
      // is protected from corruption) must never throw for a non-anchor
      // corruption, and must never let a malformed close slip through as a
      // non-finite number.
      const anchorMs = Date.parse(corruptedBase[0]!.open_time);
      const normalized = normalizeToBase100(corruptedBase, anchorMs);
      for (const p of normalized) {
        if (p.value !== null) expect(Number.isFinite(p.value), `corruption=${corruption} case ${i}`).toBe(true);
      }

      // alignSeries only ever reads open_time, never close — it must be
      // indifferent to every close-value corruption and never throw for one.
      expect(() => alignSeries(corruptedBase, otherSeries), `corruption=${corruption} case ${i}`).not.toThrow();

      const aligned = alignSeries(corruptedBase, otherSeries);
      const ratios = spread(aligned, "ratio");
      const diffs = spread(aligned, "diff");
      for (const r of [...ratios, ...diffs]) {
        if (r.value !== null) expect(Number.isFinite(r.value), `corruption=${corruption} case ${i}`).toBe(true);
      }
    }
    // Every corruption kind must have fired at least once across the seeded
    // run, and every fired case must have satisfied the assertions above — a
    // single silent NaN/Infinity leak or uncaught throw anywhere in the loop
    // would already have failed the test.
    expect(cases).toBe(200);
    expect(exercised.size).toBe(7);
  });
});

describe("performance: numeric ms budget for large series", () => {
  it("aligns, normalizes and spreads 20,000-candle series within a fixed ms budget", () => {
    const rng = createRng(0x51de5);
    const size = 20_000;
    const startMs = Date.parse(DAY1);
    const baseLarge = genSeries(rng, BASE_KEY, startMs, size);
    const otherLarge = genSeries(rng, OTHER_KEY, startMs, size);

    const start = performance.now();
    const aligned = alignSeries(baseLarge, otherLarge);
    const normalized = normalizeToBase100(baseLarge, startMs);
    const ratios = spread(aligned, "ratio");
    const elapsedMs = performance.now() - start;

    expect(aligned.points.length).toBe(size);
    expect(normalized.length).toBe(size);
    expect(ratios.length).toBe(size);
    // Generous fixed budget (not a relative ratchet): a regression that made
    // any of these O(n^2) — e.g. an accidental .find()/.indexOf() scan per
    // candle instead of the Map-based lookup alignSeries uses — would blow
    // well past this on any machine, seeded/noise-free inputs aside.
    expect(elapsedMs).toBeLessThan(3000);
  });
});

describe("gate red reproduction: compare-core invariants", () => {
  describe("align: no forward-fill for missing candles", () => {
    /** Mimics a naive aligner that forward-fills the last known candle instead
     * of leaving a gap null. A mutant of `alignSeries`, not part of the
     * shipped module. */
    function legacyForwardFillAlign(base: readonly CandleRecord[], other: readonly CandleRecord[]): AlignedSeries {
      const byTime = (a: CandleRecord, b: CandleRecord) => Date.parse(a.open_time) - Date.parse(b.open_time);
      const baseSorted = [...base].sort(byTime);
      const otherSorted = [...other].sort(byTime);
      const times = [...new Set([...baseSorted, ...otherSorted].map((r) => Date.parse(r.open_time)))].sort(
        (a, b) => a - b,
      );
      let lastBase: CandleRecord | null = null;
      let lastOther: CandleRecord | null = null;
      return {
        points: times.map((t) => {
          const b = baseSorted.find((r) => Date.parse(r.open_time) === t) ?? null;
          const o = otherSorted.find((r) => Date.parse(r.open_time) === t) ?? null;
          if (b) lastBase = b;
          if (o) lastOther = o;
          return { timeMs: t, time: new Date(t).toISOString(), base: lastBase, other: lastOther };
        }),
      };
    }

    it("red: a naive forward-fill aligner fabricates a candle for the missing day", () => {
      const legacy = legacyForwardFillAlign(base, other);
      // day2 (index 1) has no `other` candle in the fixture — the legacy
      // aligner backfills it with day1's candle instead of leaving a gap.
      expect(legacy.points[1]!.other).not.toBeNull();
      expect(legacy.points[1]!.other?.close).toBe("50");
    });

    it("green: the shipped aligner leaves the missing day null instead of fabricating data", () => {
      const aligned = alignSeries(base, other);
      expect(aligned.points[1]!.other).toBeNull();
    });
  });

  describe("normalize: fail-closed on a zero/invalid anchor close", () => {
    /** Mimics a pre-hardening normalizer with no anchor validation — divides
     * by whatever the anchor close parses to, silently. A mutant of
     * `normalizeToBase100`, not part of the shipped module. */
    function legacyNormalizeAllowingZeroAnchor(
      series: readonly CandleRecord[],
      anchorTimeMs: number,
    ): readonly number[] {
      const sorted = [...series].sort((a, b) => Date.parse(a.open_time) - Date.parse(b.open_time));
      const anchor = sorted.find((r) => Date.parse(r.open_time) === anchorTimeMs)!;
      const anchorClose = Number(anchor.close);
      return sorted.map((r) => (Number(r.close) / anchorClose) * 100);
    }

    const zeroAnchorSeries = [{ ...base[0]!, close: "0", open: "0", high: "0", low: "0" }, base[1]!];

    it("red: a naive normalizer silently emits Infinity/NaN for a zero anchor", () => {
      const legacy = legacyNormalizeAllowingZeroAnchor(zeroAnchorSeries, Date.parse(DAY1));
      expect(legacy.some((v) => !Number.isFinite(v))).toBe(true);
    });

    it("green: the shipped normalizer throws instead of emitting a bogus value", () => {
      expect(() => normalizeToBase100(zeroAnchorSeries, Date.parse(DAY1))).toThrow(NormalizeError);
    });
  });

  describe("spread: reject cross-currency/scale comparison before computing", () => {
    /** Mimics a pre-hardening spread calculator with no quote-scale guard —
     * computes ratio/diff across mismatched venues as if the prices were
     * comparable. A mutant of `spread`, not part of the shipped module. */
    function legacyComputeSpreadIgnoringScale(aligned: AlignedSeries): readonly (number | null)[] {
      return aligned.points.map((p) => {
        if (!p.base || !p.other) return null;
        const b = Number(p.base.close);
        const o = Number(p.other.close);
        return b === 0 ? null : o / b;
      });
    }

    it("red: a naive spread calculator produces a bogus numeric ratio across currencies", () => {
      const krw = [candle(KRW_KEY, DAY1, DAY2, "70000"), candle(KRW_KEY, DAY2, DAY3, "71000")];
      const aligned = alignSeries(base, krw);
      const legacy = legacyComputeSpreadIgnoringScale(aligned);
      expect(legacy.some((v) => v !== null && Number.isFinite(v))).toBe(true);
    });

    it("green: the shipped spread throws instead of comparing mismatched quote scales", () => {
      const krw = [candle(KRW_KEY, DAY1, DAY2, "70000"), candle(KRW_KEY, DAY2, DAY3, "71000")];
      const aligned = alignSeries(base, krw);
      expect(() => spread(aligned, "ratio")).toThrow(SpreadError);
    });
  });
});

describe("D3 — property-based invariants & adversarial values", () => {
  it("property: align/normalize/spread invariants hold across 300 seeded random series pairs", () => {
    const rng = createRng(0xd3d3);
    for (let i = 0; i < 300; i++) {
      const size = rng.int(2, 30);
      const startMs = Date.parse(DAY1) + rng.int(0, 10) * 86_400_000;
      const baseSeries = genSeries(rng, BASE_KEY, startMs, size);
      const otherSeries = genSeries(rng, OTHER_KEY, startMs, size);

      const aligned = alignSeries(baseSeries, otherSeries);
      // Identical start/count/step => fully overlapping, gapless series:
      // every point must have both sides present, in strictly increasing
      // time order.
      expect(aligned.points.length, `case ${i}`).toBe(size);
      for (let j = 0; j < aligned.points.length; j++) {
        const p = aligned.points[j]!;
        expect(p.base, `case ${i} point ${j}`).not.toBeNull();
        expect(p.other, `case ${i} point ${j}`).not.toBeNull();
        if (j > 0) expect(p.timeMs).toBeGreaterThan(aligned.points[j - 1]!.timeMs);
      }

      const normalized = normalizeToBase100(baseSeries, startMs);
      expect(normalized[0]!.value).toBe(100);

      const ratios = spread(aligned, "ratio");
      const diffs = spread(aligned, "diff");
      for (let j = 0; j < aligned.points.length; j++) {
        const b = Number(aligned.points[j]!.base!.close);
        const o = Number(aligned.points[j]!.other!.close);
        expect(ratios[j]!.value).toBeCloseTo(o / b, 6);
        expect(diffs[j]!.value).toBeCloseTo(o - b, 6);
        // Round-trip reconstruction: ratio * base recovers other.
        expect(ratios[j]!.value! * b).toBeCloseTo(o, 3);
      }
    }
  }, 20_000);

  it("multi-instance isolation (D3): interleaved calls on independent series never leak state", () => {
    const rngA = createRng(1);
    const rngB = createRng(2);
    const seriesA1 = genSeries(rngA, BASE_KEY, Date.parse(DAY1), 5);
    const seriesA2 = genSeries(rngA, OTHER_KEY, Date.parse(DAY1), 5);
    const seriesB1 = genSeries(rngB, BASE_KEY, Date.parse(DAY1), 5);
    const seriesB2 = genSeries(rngB, OTHER_KEY, Date.parse(DAY1), 5);

    // Interleave calls against both fixtures: a naive shared-cache
    // implementation (e.g. memoizing the last aligned/sorted series) would
    // mix A and B up here.
    const alignedA1 = alignSeries(seriesA1, seriesA2);
    const alignedB1 = alignSeries(seriesB1, seriesB2);
    const alignedA2 = alignSeries(seriesA1, seriesA2);
    const alignedB2 = alignSeries(seriesB1, seriesB2);

    expect(alignedA1).toEqual(alignedA2);
    expect(alignedB1).toEqual(alignedB2);
    expect(alignedA1).not.toEqual(alignedB1);

    const ratioA1 = spread(alignedA1, "ratio");
    const ratioB1 = spread(alignedB1, "ratio");
    const ratioA2 = spread(alignedA1, "ratio");
    expect(ratioA1).toEqual(ratioA2);
    expect(ratioA1).not.toEqual(ratioB1);
  });

  it("adversarial: extreme-but-finite close values never crash and stay finite where defined", () => {
    for (const value of ADVERSARIAL_FINITE_CLOSE_VALUES) {
      const series = [candle(BASE_KEY, DAY1, DAY2, "100"), candle(BASE_KEY, DAY2, DAY3, value)];
      const points = normalizeToBase100(series, Date.parse(DAY1));
      expect(points[0]!.value, `value=${value}`).toBe(100);
      if (points[1]!.value !== null) expect(Number.isFinite(points[1]!.value), `value=${value}`).toBe(true);
    }
  });
});
