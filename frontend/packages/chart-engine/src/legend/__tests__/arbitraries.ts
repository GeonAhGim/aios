/**
 * Deterministic pseudo-random generators for the legend/{statusLine,dataWindow,
 * objectTree} property and fuzz tests (no fast-check dependency). Seeded
 * mulberry32, same construction as compare/__tests__/arbitraries.ts, so a
 * failing case is reproducible by seed.
 */

import type { StatusLineCandle } from "../statusLine";
import type { IndicatorSeriesSnapshot } from "../dataWindow";
import type { IndicatorSource, OverlaySource } from "../objectTree";

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

/** A structurally valid candle with a random-but-finite OHLCV. */
export function genCandle(rng: Rng, timestamp: number): StatusLineCandle {
  const low = 1 + rng.next() * 100_000;
  const high = low + rng.next() * 1000;
  return {
    timestamp,
    open: low + rng.next() * (high - low),
    high,
    low,
    close: low + rng.next() * (high - low),
    volume: rng.next() * 1_000_000,
  };
}

export type NonFiniteInjection = "nan" | "positive_infinity" | "negative_infinity" | "undefined";

const NON_FINITE_INJECTIONS: readonly NonFiniteInjection[] = ["nan", "positive_infinity", "negative_infinity", "undefined"];

function injectedValue(kind: NonFiniteInjection): number | undefined {
  switch (kind) {
    case "nan":
      return NaN;
    case "positive_infinity":
      return Infinity;
    case "negative_infinity":
      return -Infinity;
    case "undefined":
      return undefined;
  }
}

const CANDLE_NUMERIC_FIELDS = ["open", "high", "low", "close", "volume"] as const;

/**
 * Corrupts one numeric field of an otherwise-valid candle with a non-finite
 * value — the shape a malformed vendor decode (bad JSON, dropped websocket
 * frame) could hand this module. Failure-injection analogue for a pure
 * formatting function with no I/O to fault-inject against.
 */
export function corruptCandleField(
  rng: Rng,
  candle: StatusLineCandle,
): { readonly candle: StatusLineCandle; readonly field: (typeof CANDLE_NUMERIC_FIELDS)[number]; readonly injection: NonFiniteInjection } {
  const field = rng.pick(CANDLE_NUMERIC_FIELDS);
  const injection = rng.pick(NON_FINITE_INJECTIONS);
  return { candle: { ...candle, [field]: injectedValue(injection) }, field, injection };
}

/** A single-figure indicator snapshot (SMA-shaped) over `values`. */
export function genIndicatorSnapshot(id: string, values: readonly (number | undefined)[]): IndicatorSeriesSnapshot {
  return {
    id,
    figures: [{ key: "value", title: id, color: "#1677FF" }],
    result: values.map((v) => (v === undefined ? undefined : { value: v })),
  };
}

/**
 * Corrupts one figure value in an indicator's result at `index` with a
 * non-finite number — simulates a malformed backend calculation payload
 * reaching the panel without going through JSON.parse's NaN/Infinity ban.
 */
export function corruptIndicatorPoint(
  rng: Rng,
  indicator: IndicatorSeriesSnapshot,
  index: number,
): { readonly indicator: IndicatorSeriesSnapshot; readonly injection: NonFiniteInjection } {
  const injection = rng.pick(NON_FINITE_INJECTIONS);
  const value = injectedValue(injection);
  const result = indicator.result.slice();
  const point = result[index];
  result[index] = value === undefined ? undefined : { ...point, value };
  return { indicator: { ...indicator, result }, injection };
}

export function genIndicatorSource(rng: Rng, id: string): IndicatorSource {
  return { id, paneId: rng.pick(["candle_pane", "pane_1", "pane_2"]), name: id, visible: rng.bool(), zLevel: rng.int(-1000, 1000) };
}

export function genOverlaySource(rng: Rng, id: string): OverlaySource {
  return {
    id,
    paneId: rng.pick(["candle_pane", "pane_1", "pane_2"]),
    name: id,
    visible: rng.bool(),
    zLevel: rng.int(-1000, 1000),
    lock: rng.bool(),
  };
}
