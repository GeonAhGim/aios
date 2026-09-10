/**
 * Deterministic pseudo-random generators for the render/{plotRenderers,
 * scaleBinding,fillBetween} property and fuzz tests (no fast-check
 * dependency, same seeded-mulberry32 pattern as panes/__tests__/arbitraries.ts
 * and legend/__tests__/arbitraries.ts). Seeded so a failing case is
 * reproducible by seed.
 */

import type { PlotKind, PlotSpec, ScaleHint } from "../plotRenderers";

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

const PLOT_KINDS: readonly PlotKind[] = ["line", "histogram", "area", "band", "cloud", "marker"];
const SCALE_HINTS: readonly ScaleHint[] = ["own", "overlay", "percent", "log", "inverted"];

/** A random but always-valid `PlotSpec` (decodes cleanly through `decodePlotSpec`). */
export function genValidPlotSpec(rng: Rng): PlotSpec {
  const kind = rng.pick(PLOT_KINDS);
  return {
    kind,
    scale: rng.pick(SCALE_HINTS),
    default_pane: rng.pick(["price", "separate"] as const),
    fill_between: kind === "band" || kind === "cloud" ? (rng.bool() ? "partner" : null) : null,
    color_rule: kind === "histogram" && rng.bool() ? "sign" : null,
    precision: rng.bool() ? rng.int(0, 8) : null,
    legend_format: null,
  };
}

/** Random monotonically increasing value-space series of `length` points. */
export function genSeries(rng: Rng, length: number, startValue = 100): { time: number; value: number }[] {
  const points: { time: number; value: number }[] = [];
  let value = startValue;
  for (let i = 0; i < length; i++) {
    value += (rng.next() - 0.5) * 4;
    points.push({ time: i, value });
  }
  return points;
}

export type PlotSpecCorruption =
  | "unknown_kind"
  | "unknown_scale"
  | "unknown_default_pane"
  | "unknown_field"
  | "negative_precision"
  | "non_string_precision"
  | "unknown_color_rule"
  | "non_object";

export const PLOT_SPEC_CORRUPTIONS: readonly PlotSpecCorruption[] = [
  "unknown_kind",
  "unknown_scale",
  "unknown_default_pane",
  "unknown_field",
  "negative_precision",
  "non_string_precision",
  "unknown_color_rule",
  "non_object",
];

/**
 * Picks one malformed raw `PlotSpec` payload — the fault-injection domain a
 * backend-catalog fetch or a hand-edited layout blob could hand
 * `decodePlotSpec`, standing in for the mocked network/DB failure injection
 * that is structurally impossible for this pure parser (no I/O).
 */
export function pickCorruptedPlotSpec(rng: Rng): { corruption: PlotSpecCorruption; raw: unknown } {
  const corruption = rng.pick(PLOT_SPEC_CORRUPTIONS);
  const base = { kind: "line", scale: "own", default_pane: "separate", fill_between: null, color_rule: null, precision: null, legend_format: null };
  switch (corruption) {
    case "unknown_kind":
      return { corruption, raw: { ...base, kind: `bogus_${rng.int(0, 1_000_000)}` } };
    case "unknown_scale":
      return { corruption, raw: { ...base, scale: `bogus_${rng.int(0, 1_000_000)}` } };
    case "unknown_default_pane":
      return { corruption, raw: { ...base, default_pane: `bogus_${rng.int(0, 1_000_000)}` } };
    case "unknown_field":
      return { corruption, raw: { ...base, [`ghost_${rng.int(0, 1_000_000)}`]: 1 } };
    case "negative_precision":
      return { corruption, raw: { ...base, precision: -1 - rng.int(0, 10) } };
    case "non_string_precision":
      return { corruption, raw: { ...base, precision: `${rng.int(0, 9)}` } };
    case "unknown_color_rule":
      return { corruption, raw: { ...base, kind: "histogram", color_rule: `bogus_${rng.int(0, 1_000_000)}` } };
    case "non_object":
      return { corruption, raw: rng.pick([null, undefined, 42, "spec", [1, 2, 3]]) };
  }
}

export type ScaleCorruption = "unknown_scale" | "log_zero" | "log_negative" | "log_non_finite";

export const SCALE_CORRUPTIONS: readonly ScaleCorruption[] = ["unknown_scale", "log_zero", "log_negative", "log_non_finite"];

export function pickScaleCorruption(rng: Rng): { corruption: ScaleCorruption; scale: string; value: number } {
  const corruption = rng.pick(SCALE_CORRUPTIONS);
  switch (corruption) {
    case "unknown_scale":
      return { corruption, scale: `bogus_${rng.int(0, 1_000_000)}`, value: rng.next() * 100 };
    case "log_zero":
      return { corruption, scale: "log", value: 0 };
    case "log_negative":
      return { corruption, scale: "log", value: -1 - rng.next() * 1000 };
    case "log_non_finite":
      // Only NaN and -Infinity are picked here: both fail the shipped `value
      // > 0` guard. +Infinity technically passes that guard (Infinity > 0 is
      // true) and is out of scope for this corruption — it is not a
      // non-positive value.
      return { corruption, scale: "log", value: rng.bool() ? Number.NaN : -Infinity };
  }
}

export type FillCorruption = "length_mismatch" | "time_mismatch";

export const FILL_CORRUPTIONS: readonly FillCorruption[] = ["length_mismatch", "time_mismatch"];

/** Random malformed `(base, target)` series pair for `computeFillSegments` fuzzing. */
export function pickCorruptedFillSeries(
  rng: Rng,
): { corruption: FillCorruption; base: { time: number; value: number }[]; target: { time: number; value: number }[] } {
  const corruption = rng.pick(FILL_CORRUPTIONS);
  const length = rng.int(1, 20);
  const base = genSeries(rng, length);
  if (corruption === "length_mismatch") {
    const target = genSeries(rng, length + 1 + rng.int(0, 5));
    return { corruption, base, target };
  }
  // time_mismatch: same length, but shift every target timestamp so pairwise times never line up.
  const target = base.map((p) => ({ time: p.time + 1000 + rng.int(1, 100), value: p.value + (rng.next() - 0.5) * 4 }));
  return { corruption, base, target };
}
