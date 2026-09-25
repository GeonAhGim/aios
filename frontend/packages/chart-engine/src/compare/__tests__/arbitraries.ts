/**
 * Deterministic pseudo-random generators for the compare/{align,normalize,spread}
 * property and fuzz tests (no fast-check dependency). Seeded mulberry32 so a
 * failing case is reproducible by seed.
 */

import type { CandleRecord, SeriesKey } from "@aios/shared-types";

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

const DAY_MS = 24 * 60 * 60 * 1000;

/** A finite, positive close price shaped like a real candle (never 0, never NaN). */
export function genClose(rng: Rng): number {
  const base = 1 + rng.next() * 100_000;
  return Number(base.toFixed(rng.int(2, 8)));
}

export function genCandle(key: SeriesKey, openMs: number, close: number): CandleRecord {
  const closeStr = String(close);
  return {
    key,
    open_time: new Date(openMs).toISOString(),
    close_time: new Date(openMs + DAY_MS).toISOString(),
    open: closeStr,
    high: closeStr,
    low: closeStr,
    close: closeStr,
    volume: "1",
    quote_volume: null,
  };
}

/** A chronologically sorted, gapless daily series of `count` candles starting at `startMs`. */
export function genSeries(rng: Rng, key: SeriesKey, startMs: number, count: number): CandleRecord[] {
  return Array.from({ length: count }, (_, i) => genCandle(key, startMs + i * DAY_MS, genClose(rng)));
}

/** Values `Number(x)` fails to turn into a finite, well-defined price. */
export const MALFORMED_CLOSE_VALUES: readonly string[] = [
  "not-a-number",
  "",
  "1e400", // Number(...) === Infinity
  "-1e400", // Number(...) === -Infinity
  "NaN",
  "1,234.5", // thousands separator — Number() rejects
  "undefined",
];

export type CandleCorruption =
  | "non_numeric_close"
  | "empty_close"
  | "infinite_close"
  | "nan_literal_close"
  | "comma_thousands_close"
  | "duplicate_timestamp"
  | "reordered_series";

const CANDLE_CORRUPTIONS: readonly CandleCorruption[] = [
  "non_numeric_close",
  "empty_close",
  "infinite_close",
  "nan_literal_close",
  "comma_thousands_close",
  "duplicate_timestamp",
  "reordered_series",
];

export interface CorruptedSeries {
  readonly series: readonly CandleRecord[];
  readonly corruption: CandleCorruption;
  /** open_time (ISO) of the candle that was corrupted, when applicable. */
  readonly targetTime?: string;
}

const CLOSE_CORRUPTION_VALUE: Readonly<Record<string, string>> = {
  non_numeric_close: "not-a-number",
  empty_close: "",
  infinite_close: "1e400",
  nan_literal_close: "NaN",
  comma_thousands_close: "1,234.5",
};

/**
 * Injects one structural corruption into a valid, sorted candle series, chosen
 * and targeted by `rng` — a fault-injection fuzzer for the malformed-input
 * domain a network/DB mock would otherwise cover. `series` must have at least
 * 2 candles and must exclude index 0 from close-value corruption when the
 * caller intends to use it as a normalize anchor.
 */
export function corruptSeries(rng: Rng, series: readonly CandleRecord[], protectedIndex = -1): CorruptedSeries {
  const corruption = rng.pick(CANDLE_CORRUPTIONS);
  const candidates = series.map((_, i) => i).filter((i) => i !== protectedIndex);
  const index = candidates[rng.int(0, candidates.length - 1)]!;
  const clone = series.map((c) => ({ ...c }));

  switch (corruption) {
    case "non_numeric_close":
    case "empty_close":
    case "infinite_close":
    case "nan_literal_close":
    case "comma_thousands_close":
      clone[index] = { ...clone[index]!, close: CLOSE_CORRUPTION_VALUE[corruption]! };
      return { series: clone, corruption, targetTime: clone[index]!.open_time };
    case "duplicate_timestamp": {
      const other = candidates.filter((i) => i !== index)[0] ?? index;
      clone[other] = { ...clone[other]!, open_time: clone[index]!.open_time };
      return { series: clone, corruption, targetTime: clone[index]!.open_time };
    }
    case "reordered_series": {
      // Swap two candles' positions in the array (they are still internally
      // sorted by open_time by every function under test, so this must be a
      // no-op on the result — proves the sort isn't accidentally relying on
      // input order).
      const other = candidates.filter((i) => i !== index)[0] ?? index;
      [clone[index], clone[other]] = [clone[other]!, clone[index]!];
      return { series: clone, corruption };
    }
  }
}

/** Extreme-but-technically-finite or edge-case numeric strings a real feed could emit. */
export const ADVERSARIAL_FINITE_CLOSE_VALUES: readonly string[] = [
  "1e300",
  "5e-300",
  "-0",
  "0.00000001",
  "999999999999.99",
];
