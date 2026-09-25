/**
 * Deterministic pseudo-random generators for the indicatorPlugin fuzz/property
 * tests (no fast-check dependency). Seeded mulberry32 so a failing case is
 * reproducible by seed — mirrors `panes/__tests__/arbitraries.ts`.
 */

import { createDefaultOverlayRegistry } from "../../indicators/overlayRegistry";
import { createPaneModel } from "../../panes/paneModel";
import type { PaneModel } from "../../panes/paneModel";
import {
  createIndicatorPluginRegistry,
  registerIndicatorPlugin,
  setIndicatorPluginStyle,
  unregisterIndicatorPlugin,
  type IndicatorCatalogEntry,
  type IndicatorPluginRegistry,
  type IndicatorStyle,
} from "../indicatorPlugin";

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

/** CORE tier, main-overlay per `DEFAULT_OVERLAY_DEFINITIONS`. */
export const SMA_ENTRY: IndicatorCatalogEntry = {
  name: "SMA",
  tier: "core",
  category: "overlap",
  version: "ind-v1",
  hash: "sma-hash",
  inputs: ["close"],
  outputs: ["value"],
};

/** CORE tier, sub-pane per `DEFAULT_OVERLAY_DEFINITIONS`. */
export const RSI_ENTRY: IndicatorCatalogEntry = {
  name: "RSI",
  tier: "core",
  category: "momentum",
  version: "ind-v1",
  hash: "rsi-hash",
  inputs: ["close"],
  outputs: ["value"],
};

/** CORE tier, sub-pane, multi-output per `DEFAULT_OVERLAY_DEFINITIONS`. */
export const MACD_ENTRY: IndicatorCatalogEntry = {
  name: "MACD",
  tier: "core",
  category: "momentum",
  version: "ind-v1",
  hash: "macd-hash",
  inputs: ["close"],
  outputs: ["macd", "signal", "hist"],
};

/** OSS tier, no overlay registry mirror — placement must always be explicit. */
export const OSS_ENTRY: IndicatorCatalogEntry = {
  name: "COMMUNITY_OSC",
  tier: "oss",
  category: "oss",
  version: "1.0.0",
  hash: "oss-hash",
  inputs: ["close"],
  outputs: ["value"],
};

export function goodStyle(entry: IndicatorCatalogEntry, overrides: Partial<IndicatorStyle["outputs"][number]> = {}): IndicatorStyle {
  return { outputs: entry.outputs.map((o) => ({ output: o, color: "#26a69a", lineWidth: 2, visible: true, ...overrides })) };
}

export type PluginCorruption =
  | "duplicate_instance_id"
  | "unregister_unknown"
  | "set_style_unknown_instance"
  | "style_unknown_output"
  | "style_duplicate_output"
  | "style_zero_line_width"
  | "style_negative_line_width"
  | "style_nan_line_width"
  | "placement_conflict"
  | "oss_missing_placement";

export const PLUGIN_CORRUPTIONS: readonly PluginCorruption[] = [
  "duplicate_instance_id",
  "unregister_unknown",
  "set_style_unknown_instance",
  "style_unknown_output",
  "style_duplicate_output",
  "style_zero_line_width",
  "style_negative_line_width",
  "style_nan_line_width",
  "placement_conflict",
  "oss_missing_placement",
];

export interface CorruptedPluginOp {
  readonly corruption: PluginCorruption;
  /** Always throws `IndicatorPluginError` when applied. */
  apply(): unknown;
  /** Snapshot the op is applied on top of — must be untouched after `apply` throws. */
  readonly registryBefore: IndicatorPluginRegistry;
  readonly paneModelBefore: PaneModel;
}

/**
 * Picks one corruption targeted at a fresh registry/paneModel seeded with one
 * valid SMA registration — a fault-injection fuzzer for the malformed-input
 * domain a UI layer (indicator picker, persisted layoutState round-trip)
 * could hand this pure registry, standing in for the mocked network/DB
 * failure injection that's structurally impossible for the register/style
 * paths (no I/O; only `loadIndicatorCatalog` talks to an injected port,
 * already covered by the "network down" case in indicatorPlugin.test.ts).
 */
export function pickCorruptedOp(rng: Rng, instanceSeed: number): CorruptedPluginOp {
  const overlay = createDefaultOverlayRegistry();
  const smaId = `sma-${instanceSeed}`;
  const base = registerIndicatorPlugin(createIndicatorPluginRegistry(), createPaneModel("main"), overlay, {
    instanceId: smaId,
    catalogEntry: SMA_ENTRY,
    params: {},
    style: goodStyle(SMA_ENTRY),
  });
  const corruption = rng.pick(PLUGIN_CORRUPTIONS);

  switch (corruption) {
    case "duplicate_instance_id":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () =>
          registerIndicatorPlugin(base.registry, base.paneModel, overlay, {
            instanceId: smaId,
            catalogEntry: SMA_ENTRY,
            params: {},
            style: goodStyle(SMA_ENTRY),
          }),
      };
    case "unregister_unknown":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () => unregisterIndicatorPlugin(base.registry, base.paneModel, `ghost-${instanceSeed}`),
      };
    case "set_style_unknown_instance":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () => setIndicatorPluginStyle(base.registry, `ghost-${instanceSeed}`, goodStyle(SMA_ENTRY)),
      };
    case "style_unknown_output":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () =>
          registerIndicatorPlugin(base.registry, base.paneModel, overlay, {
            instanceId: `extra-${instanceSeed}`,
            catalogEntry: RSI_ENTRY,
            params: {},
            style: { outputs: [{ output: "not-a-real-output", color: "#fff", lineWidth: 1, visible: true }] },
          }),
      };
    case "style_duplicate_output":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () =>
          registerIndicatorPlugin(base.registry, base.paneModel, overlay, {
            instanceId: `extra-${instanceSeed}`,
            catalogEntry: RSI_ENTRY,
            params: {},
            style: {
              outputs: [
                { output: "value", color: "#fff", lineWidth: 1, visible: true },
                { output: "value", color: "#000", lineWidth: 1, visible: true },
              ],
            },
          }),
      };
    case "style_zero_line_width":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () =>
          registerIndicatorPlugin(base.registry, base.paneModel, overlay, {
            instanceId: `extra-${instanceSeed}`,
            catalogEntry: RSI_ENTRY,
            params: {},
            style: goodStyle(RSI_ENTRY, { lineWidth: 0 }),
          }),
      };
    case "style_negative_line_width":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () =>
          registerIndicatorPlugin(base.registry, base.paneModel, overlay, {
            instanceId: `extra-${instanceSeed}`,
            catalogEntry: RSI_ENTRY,
            params: {},
            style: goodStyle(RSI_ENTRY, { lineWidth: -3 }),
          }),
      };
    case "style_nan_line_width":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () =>
          registerIndicatorPlugin(base.registry, base.paneModel, overlay, {
            instanceId: `extra-${instanceSeed}`,
            catalogEntry: RSI_ENTRY,
            params: {},
            style: goodStyle(RSI_ENTRY, { lineWidth: Number.NaN }),
          }),
      };
    case "placement_conflict":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () =>
          registerIndicatorPlugin(base.registry, base.paneModel, overlay, {
            instanceId: `extra-${instanceSeed}`,
            catalogEntry: RSI_ENTRY,
            placement: "main-overlay",
            params: {},
            style: goodStyle(RSI_ENTRY),
          }),
      };
    case "oss_missing_placement":
      return {
        corruption,
        registryBefore: base.registry,
        paneModelBefore: base.paneModel,
        apply: () =>
          registerIndicatorPlugin(base.registry, base.paneModel, overlay, {
            instanceId: `extra-${instanceSeed}`,
            catalogEntry: OSS_ENTRY,
            params: {},
            style: goodStyle(OSS_ENTRY),
          }),
      };
  }
}
