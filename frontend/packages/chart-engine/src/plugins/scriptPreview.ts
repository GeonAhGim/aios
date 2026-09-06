/**
 * CH-12 — script preview: turns a successful `POST /v1/scripts/compile`
 * (DSL-12, `src/api/schemas/scripts.py`) result into placement-only
 * chart-engine state so an editor screen's pane layout updates the moment a
 * script compiles.
 *
 * Deliberately thin (decision, task-1808): `CompileScriptView` carries only
 * an artifact hash and resource *counts* — never IR bodies or computed
 * series (`scripts.py` docstring: "IR 본문은 싣지 않는다") — so there is no
 * value to overlay yet, only the *count* of `plot()` calls the script
 * declared. This module reuses CH-11 `plugins/indicatorPlugin.ts`
 * register/unregister verbatim (no new registry) and never computes an
 * indicator value itself: client-side computation is CH-18's job, and that
 * leaf only ever runs reference-vector-verified CORE indicators — never
 * arbitrary user scripts — so there will never be a "script preview value"
 * to compute here, only a placement to reserve ahead of it.
 *
 * The placeholder plot shape is decoded through CH-15's `decodePlotSpec`
 * (`render/plotRenderers.ts`, IND-15 SSOT is backend `core/indicators/spec.py`)
 * rather than hand-typed, so this module can never silently drift from the
 * backend `PlotSpec` field set even though no render call happens here yet —
 * `plotSpecs` in the sync result is exactly the hook a future CH-15 wiring
 * needs once compile starts returning real per-plot specs and series.
 */

import type { OverlayRegistry } from "../indicators/overlayRegistry";
import type { PaneModel } from "../panes/paneModel";
import { decodePlotSpec, type PlotSpec } from "../render/plotRenderers";
import {
  registerIndicatorPlugin,
  unregisterIndicatorPlugin,
  type IndicatorCatalogEntry,
  type IndicatorPluginRegistry,
  type IndicatorStyle,
} from "./indicatorPlugin";

/** The subset of `CompileScriptView` this module consumes — see shared-types `script.ts`. */
export interface ScriptCompilePreviewResult {
  readonly scriptHash: string;
  readonly resources: { readonly plotCount: number };
}

export interface ScriptPreviewSyncResult {
  readonly registry: IndicatorPluginRegistry;
  readonly paneModel: PaneModel;
  /** Instance ids registered by this sync, in plot order — pass back in as `previousInstanceIds` on the next call. */
  readonly instanceIds: readonly string[];
  /** One entry per `instanceIds`, all identical today (`SCRIPT_PREVIEW_PLOT_SPEC`) — keyed for the future per-plot-spec compile response. */
  readonly plotSpecs: ReadonlyMap<string, PlotSpec>;
}

const INSTANCE_PREFIX = "script-preview:";
const OUTPUT_NAME = "value";

/**
 * The only shape a script `plot()` can produce today: `plot()`'s `style`
 * argument carries no semantics yet (`src/core/script/ir/ops.py`: "style는
 * 의미 미정의 → AST 원형 운반"), so every placeholder is a plain line on its
 * own scale in its own pane. Passing this literal through `decodePlotSpec`
 * (rather than asserting it as `PlotSpec` directly) means an IND-15 field
 * rename breaks this module loudly instead of silently.
 */
export const SCRIPT_PREVIEW_PLOT_SPEC: PlotSpec = decodePlotSpec({
  kind: "line",
  scale: "own",
  default_pane: "separate",
  fill_between: null,
  color_rule: null,
  precision: null,
  legend_format: null,
});

const DEFAULT_STYLE: IndicatorStyle = {
  outputs: [{ output: OUTPUT_NAME, color: "#2563eb", lineWidth: 1, visible: true }],
};

function instanceId(scriptHash: string, index: number): string {
  return `${INSTANCE_PREFIX}${scriptHash}:${index}`;
}

function scriptPreviewCatalogEntry(scriptHash: string, index: number): IndicatorCatalogEntry {
  return {
    name: `script:${scriptHash}:${index}`,
    tier: "script",
    category: "script",
    version: scriptHash,
    hash: scriptHash,
    inputs: [],
    outputs: [OUTPUT_NAME],
  };
}

function unregisterAll(
  registry: IndicatorPluginRegistry,
  paneModel: PaneModel,
  instanceIds: readonly string[],
): { registry: IndicatorPluginRegistry; paneModel: PaneModel } {
  let nextRegistry = registry;
  let nextPaneModel = paneModel;
  for (const id of instanceIds) {
    const result = unregisterIndicatorPlugin(nextRegistry, nextPaneModel, id);
    nextRegistry = result.registry;
    nextPaneModel = result.paneModel;
  }
  return { registry: nextRegistry, paneModel: nextPaneModel };
}

/**
 * Replaces every previously-registered script-preview entry (`previousInstanceIds`,
 * as returned by the prior call) with one sub-pane per `result.resources.plotCount`.
 * Always tears down and re-registers under the new `scriptHash` rather than
 * diffing — plot *count* is the only signal available, so there is no stable
 * per-plot identity to diff against across compiles.
 */
export function syncScriptPreview(
  registry: IndicatorPluginRegistry,
  paneModel: PaneModel,
  overlayRegistry: OverlayRegistry,
  previousInstanceIds: readonly string[],
  result: ScriptCompilePreviewResult,
): ScriptPreviewSyncResult {
  const cleared = unregisterAll(registry, paneModel, previousInstanceIds);
  let nextRegistry = cleared.registry;
  let nextPaneModel = cleared.paneModel;
  const instanceIds: string[] = [];
  const plotSpecs = new Map<string, PlotSpec>();

  for (let index = 0; index < result.resources.plotCount; index += 1) {
    const id = instanceId(result.scriptHash, index);
    const registration = registerIndicatorPlugin(nextRegistry, nextPaneModel, overlayRegistry, {
      instanceId: id,
      catalogEntry: scriptPreviewCatalogEntry(result.scriptHash, index),
      placement: "sub-pane",
      params: {},
      style: DEFAULT_STYLE,
    });
    nextRegistry = registration.registry;
    nextPaneModel = registration.paneModel;
    instanceIds.push(id);
    plotSpecs.set(id, SCRIPT_PREVIEW_PLOT_SPEC);
  }

  return { registry: nextRegistry, paneModel: nextPaneModel, instanceIds, plotSpecs };
}

/** Tears down every script-preview entry without registering replacements (compile failed, or the source was cleared/edited). */
export function clearScriptPreview(
  registry: IndicatorPluginRegistry,
  paneModel: PaneModel,
  previousInstanceIds: readonly string[],
): { registry: IndicatorPluginRegistry; paneModel: PaneModel } {
  return unregisterAll(registry, paneModel, previousInstanceIds);
}
