/**
 * Deterministic pseudo-random generators for the panes/{paneModel,paneLayout,
 * crosshairSync} property and fuzz tests (no fast-check dependency). Seeded
 * mulberry32 so a failing case is reproducible by seed.
 */

import { addPane, mainPane, removePane, resizePane, setHeightRatios } from "../paneModel";
import type { PaneKind, PaneModel, PaneSpec } from "../paneModel";

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

/** Random valid `PaneSpec[]` (ratios sum to exactly 1) for `computePaneRects` property tests. */
export function genPaneSpecs(rng: Rng, count: number): PaneSpec[] {
  const raw = Array.from({ length: Math.max(count, 1) }, () => 0.1 + rng.next());
  const total = raw.reduce((sum, r) => sum + r, 0);
  return raw.map((r, i) => ({
    id: `pane${i}`,
    kind: (i === 0 ? "main" : "sub") as PaneKind,
    heightRatio: r / total,
  }));
}

export type PaneCorruption =
  | "duplicate_id"
  | "unknown_id_remove"
  | "unknown_id_resize"
  | "ratio_zero"
  | "ratio_one"
  | "ratio_negative"
  | "ratio_nan"
  | "sum_mismatch_set_ratios"
  | "incomplete_set_ratios"
  | "remove_main";

export const PANE_CORRUPTIONS: readonly PaneCorruption[] = [
  "duplicate_id",
  "unknown_id_remove",
  "unknown_id_resize",
  "ratio_zero",
  "ratio_one",
  "ratio_negative",
  "ratio_nan",
  "sum_mismatch_set_ratios",
  "incomplete_set_ratios",
  "remove_main",
];

export interface CorruptedPaneOp {
  readonly corruption: PaneCorruption;
  /** Always throws `PaneModelError` when applied to `model` — never returns normally. */
  apply(model: PaneModel): PaneModel;
}

/**
 * Picks one structural corruption targeted at `model`'s current pane set — a
 * fault-injection fuzzer for the malformed-input domain a UI layer (drag
 * handle, persisted-layout round-trip) could hand these pure model functions,
 * standing in for the mocked network/DB failure injection that is
 * structurally impossible here (no I/O).
 */
export function pickCorruptedOp(rng: Rng, model: PaneModel): CorruptedPaneOp {
  const corruption = rng.pick(PANE_CORRUPTIONS);
  const existingId = rng.pick(model.panes).id;
  const ghostId = `ghost-${rng.int(0, 1_000_000)}`;
  const newId = `new-${rng.int(0, 1_000_000)}`;

  switch (corruption) {
    case "duplicate_id":
      return { corruption, apply: (m) => addPane(m, existingId) };
    case "unknown_id_remove":
      return { corruption, apply: (m) => removePane(m, ghostId) };
    case "unknown_id_resize":
      return { corruption, apply: (m) => resizePane(m, ghostId, 0.5) };
    case "ratio_zero":
      return { corruption, apply: (m) => addPane(m, newId, { heightRatio: 0 }) };
    case "ratio_one":
      return { corruption, apply: (m) => addPane(m, newId, { heightRatio: 1 }) };
    case "ratio_negative":
      return { corruption, apply: (m) => resizePane(m, mainPane(m).id, -0.5) };
    case "ratio_nan":
      return { corruption, apply: (m) => resizePane(m, mainPane(m).id, Number.NaN) };
    case "sum_mismatch_set_ratios":
      return {
        corruption,
        apply: (m) => {
          // 0.9 + 0.03*(n-1) never lands on exactly 1 for any integer pane
          // count n, so this always mismatches (unlike e.g. 0.05, which
          // coincidentally sums to 1 at n=3).
          const ratios: Record<string, number> = {};
          m.panes.forEach((p, i) => {
            ratios[p.id] = i === 0 ? 0.9 : 0.03;
          });
          return setHeightRatios(m, ratios);
        },
      };
    case "incomplete_set_ratios":
      return {
        corruption,
        apply: (m) => {
          const ratios: Record<string, number> = {};
          m.panes.slice(0, Math.max(0, m.panes.length - 1)).forEach((p) => {
            ratios[p.id] = 1 / m.panes.length;
          });
          return setHeightRatios(m, ratios);
        },
      };
    case "remove_main":
      return { corruption, apply: (m) => removePane(m, mainPane(m).id) };
  }
}
