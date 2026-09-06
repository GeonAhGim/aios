import { describe, expect, it } from "vitest";
import type { CandleRecord, SeriesKey } from "@aios/shared-types";
import { AlignError, alignSeries } from "../align";
import { NormalizeError, normalizeToBase100 } from "../normalize";
import { SpreadError, spread } from "../spread";
import type { AlignedSeries } from "../align";

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
