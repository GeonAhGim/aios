/**
 * Deterministic pseudo-random generators for the templates/{templateModel,
 * applyTemplate} property, fuzz and performance tests (no fast-check
 * dependency). Seeded mulberry32 so a failing case is reproducible by seed —
 * mirrors panes/__tests__/arbitraries.ts's pattern for this sibling
 * chart-engine pure-domain leaf.
 */

import { TEMPLATE_SCHEMA_VERSION } from "./templateModel";
import type { Template, TemplateIndicator, TemplatePane } from "./templateModel";
import type { PaneModel } from "../panes/paneModel";
import type { ChartLayoutModel } from "../layout/layoutModel";

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

/** Random valid `Template` — panes' heightRatio sums to exactly 1, `paneCount` >= 2 so duplicate-pane-id corruption is always reachable. */
export function genTemplate(rng: Rng, paneCount: number, indicatorCount: number): Template {
  const count = Math.max(paneCount, 2);
  const rawRatios = Array.from({ length: count }, () => 0.1 + rng.next());
  const total = rawRatios.reduce((sum, r) => sum + r, 0);
  const panes: TemplatePane[] = rawRatios.map((r, i) => ({
    id: i === 0 ? "main" : `sub-${i}`,
    kind: i === 0 ? "main" : "sub",
    heightRatio: r / total,
  }));

  const indicators: TemplateIndicator[] = Array.from({ length: Math.max(indicatorCount, 1) }, (_, i) => {
    const paneId = rng.pick(panes).id;
    return rng.bool()
      ? { id: `ind.${i}`, paneId, params: { period: rng.int(2, 200) } }
      : { id: `ind.${i}`, paneId };
  });

  return { schemaVersion: TEMPLATE_SCHEMA_VERSION, panes, indicators };
}

export type TemplateDecodeCorruption =
  | "unknown_top_field"
  | "missing_schema_version"
  | "wrong_schema_version"
  | "root_not_object"
  | "panes_not_array"
  | "pane_not_object"
  | "pane_unknown_field"
  | "pane_missing_id"
  | "pane_empty_id"
  | "pane_invalid_kind"
  | "pane_height_not_finite"
  | "duplicate_pane_id"
  | "indicators_not_array"
  | "indicator_not_object"
  | "indicator_unknown_field"
  | "indicator_missing_id"
  | "indicator_missing_paneId"
  | "indicator_params_not_object"
  | "indicator_param_non_finite";

export const TEMPLATE_DECODE_CORRUPTIONS: readonly TemplateDecodeCorruption[] = [
  "unknown_top_field",
  "missing_schema_version",
  "wrong_schema_version",
  "root_not_object",
  "panes_not_array",
  "pane_not_object",
  "pane_unknown_field",
  "pane_missing_id",
  "pane_empty_id",
  "pane_invalid_kind",
  "pane_height_not_finite",
  "duplicate_pane_id",
  "indicators_not_array",
  "indicator_not_object",
  "indicator_unknown_field",
  "indicator_missing_id",
  "indicator_missing_paneId",
  "indicator_params_not_object",
  "indicator_param_non_finite",
];

/**
 * Picks one structural corruption of a valid encoded template document — a
 * fault-injection fuzzer for the malformed-input domain a persisted/
 * server-round-tripped template (localStorage, template share link) could
 * hand `decodeTemplate`, standing in for the mocked network/DB failure
 * injection that is structurally impossible here (no I/O, pure functions).
 * Requires a `validJson` produced from a template with >=2 panes and >=1
 * indicator carrying `params` (guaranteed by `genTemplate`), so every
 * corruption below is always reachable.
 */
export function pickCorruptedDecode(
  rng: Rng,
  validJson: Record<string, unknown>,
): { corruption: TemplateDecodeCorruption; json: unknown } {
  const corruption = rng.pick(TEMPLATE_DECODE_CORRUPTIONS);
  const json = JSON.parse(JSON.stringify(validJson)) as Record<string, unknown>;
  const panes = json.panes as Record<string, unknown>[];
  const indicators = json.indicators as Record<string, unknown>[];

  switch (corruption) {
    case "unknown_top_field":
      json.extra = true;
      return { corruption, json };
    case "missing_schema_version":
      delete json.schemaVersion;
      return { corruption, json };
    case "wrong_schema_version":
      json.schemaVersion = 999;
      return { corruption, json };
    case "root_not_object":
      return { corruption, json: [json] };
    case "panes_not_array":
      json.panes = {};
      return { corruption, json };
    case "pane_not_object":
      panes[0] = "not-an-object" as unknown as Record<string, unknown>;
      return { corruption, json };
    case "pane_unknown_field":
      panes[0]!.extra = 1;
      return { corruption, json };
    case "pane_missing_id":
      delete panes[0]!.id;
      return { corruption, json };
    case "pane_empty_id":
      panes[0]!.id = "";
      return { corruption, json };
    case "pane_invalid_kind":
      panes[0]!.kind = "weird";
      return { corruption, json };
    case "pane_height_not_finite":
      panes[0]!.heightRatio = Number.NaN;
      return { corruption, json };
    case "duplicate_pane_id":
      panes[1]!.id = panes[0]!.id;
      return { corruption, json };
    case "indicators_not_array":
      json.indicators = {};
      return { corruption, json };
    case "indicator_not_object":
      indicators[0] = "not-an-object" as unknown as Record<string, unknown>;
      return { corruption, json };
    case "indicator_unknown_field":
      indicators[0]!.extra = 1;
      return { corruption, json };
    case "indicator_missing_id":
      delete indicators[0]!.id;
      return { corruption, json };
    case "indicator_missing_paneId":
      delete indicators[0]!.paneId;
      return { corruption, json };
    case "indicator_params_not_object":
      indicators[0]!.params = "not-an-object";
      return { corruption, json };
    case "indicator_param_non_finite":
      indicators[0]!.params = { x: Number.POSITIVE_INFINITY };
      return { corruption, json };
  }
}

export type ApplyCorruption =
  | "unknown_indicator"
  | "no_active_panel"
  | "panel_not_found"
  | "no_main_pane"
  | "duplicate_pane_id"
  | "height_ratio_out_of_range";

export const APPLY_CORRUPTIONS: readonly ApplyCorruption[] = [
  "unknown_indicator",
  "no_active_panel",
  "panel_not_found",
  "no_main_pane",
  "duplicate_pane_id",
  "height_ratio_out_of_range",
];

export interface ApplyBaseInput {
  readonly paneModel: PaneModel;
  readonly layoutModel: ChartLayoutModel;
  readonly knownIndicatorIds: ReadonlySet<string>;
}

export interface CorruptedApplyCall {
  readonly corruption: ApplyCorruption;
  /** Always throws (`TemplateError` or, once delegated to CH-14 `paneModel.ts`, `PaneModelError`) when invoked. */
  run(applyFn: (template: Template, input: ApplyBaseInput) => unknown): void;
}

/**
 * Picks one structural corruption of a valid `(template, input)` pair for
 * `apply()` — the closest analogue to mocked network/DB failure injection
 * for this pure-function leaf (no I/O to fail): a corrupted template or
 * layout shape a caller (screen wiring, persisted-template round-trip)
 * could hand `apply()`. Mirrors `pickCorruptedDecode` for the apply side.
 */
export function pickCorruptedApply(rng: Rng, template: Template, base: ApplyBaseInput): CorruptedApplyCall {
  const corruption = rng.pick(APPLY_CORRUPTIONS);

  switch (corruption) {
    case "unknown_indicator":
      return {
        corruption,
        run: (applyFn) =>
          applyFn(
            {
              ...template,
              indicators: [...template.indicators, { id: `ghost-${rng.int(0, 1_000_000)}`, paneId: template.panes[0]!.id }],
            },
            base,
          ),
      };
    case "no_active_panel":
      return { corruption, run: (applyFn) => applyFn(template, { ...base, layoutModel: { ...base.layoutModel, activePanelId: null } }) };
    case "panel_not_found":
      return {
        corruption,
        run: (applyFn) => applyFn(template, { ...base, layoutModel: { ...base.layoutModel, activePanelId: `ghost-${rng.int(0, 1_000_000)}` } }),
      };
    case "no_main_pane":
      return {
        corruption,
        run: (applyFn) => applyFn({ ...template, panes: template.panes.map((pane) => ({ ...pane, kind: "sub" as const })) }, base),
      };
    case "duplicate_pane_id":
      // Duplicates a *sub*-pane id (index 1, guaranteed by genTemplate's
      // paneCount>=2): buildPaneModel's "skip if id === main's id" guard
      // only compares against the main pane, so a duplicate main id would
      // silently skip both instead of reaching addPane's own duplicate
      // check.
      return {
        corruption,
        run: (applyFn) => applyFn({ ...template, panes: [...template.panes, { ...template.panes[1]! }] }, base),
      };
    case "height_ratio_out_of_range":
      return {
        corruption,
        run: (applyFn) =>
          applyFn({ ...template, panes: template.panes.map((pane, i) => (i === 1 ? { ...pane, heightRatio: 0 } : pane)) }, base),
      };
  }
}
