/**
 * CH-3 — maps server indicator results onto chart panes: a main-pane
 * overlay (drawn over the candles) or a dedicated sub-pane.
 *
 * Ownership boundary: indicator ids, parameter names and output names are
 * owned by the backend IndicatorRegistry (L01/L02,
 * `src/core/indicators/specs_talib.py`, REGISTRY_VERSION "ind-v1"). This
 * module only owns *placement* and the per-output series type. The
 * `DEFAULT_OVERLAY_DEFINITIONS` catalog mirrors the backend names; the drift
 * guard in `__tests__/overlayRegistry.test.ts` parses the Python source and
 * fails when the mirror diverges, so nothing here may be "redefined" silently.
 *
 * No silent fallback: resolving an unknown id throws `OverlayRegistryError`
 * with a machine-readable `code`, mirroring the backend `IndicatorError`.
 */

import type { SeriesType } from "../core/series";

/** Must equal `REGISTRY_VERSION` in backend `src/core/indicators/spec.py`. */
export const INDICATOR_REGISTRY_VERSION = "ind-v1";

/** Pane index shared by the candle series and every main-pane overlay. */
export const MAIN_PANE_INDEX = 0;

export type OverlayPlacement = "main-overlay" | "sub-pane";

const PLACEMENTS: ReadonlySet<string> = new Set<OverlayPlacement>(["main-overlay", "sub-pane"]);

export interface OverlayOutput {
  /** Backend `IndicatorSpec.outputs` entry, e.g. "value", "macd", "upperband". */
  readonly name: string;
  readonly series: SeriesType;
}

export interface OverlayDefinition {
  /** Backend `IndicatorSpec.name`, e.g. "SMA". */
  readonly id: string;
  readonly placement: OverlayPlacement;
  /** Backend `IndicatorSpec.params[].name`, in backend order. */
  readonly params: readonly string[];
  /** One entry per backend `IndicatorSpec.outputs` name, in backend order. */
  readonly outputs: readonly OverlayOutput[];
}

export interface OverlayEntry extends OverlayDefinition {
  /** `MAIN_PANE_INDEX` for overlays; a unique index >= 1 for each sub-pane. */
  readonly paneIndex: number;
}

export type OverlayRegistryErrorCode =
  | "CHART_OVERLAY_UNKNOWN"
  | "CHART_OVERLAY_DUPLICATE"
  | "CHART_OVERLAY_INVALID";

export class OverlayRegistryError extends Error {
  readonly code: OverlayRegistryErrorCode;
  readonly indicatorId: string;

  constructor(code: OverlayRegistryErrorCode, indicatorId: string, detail: string) {
    super(`${code}: ${detail} (indicator "${indicatorId}")`);
    this.name = "OverlayRegistryError";
    this.code = code;
    this.indicatorId = indicatorId;
  }
}

export interface OverlayRegistry {
  /** Registers a definition and assigns its pane. Throws on duplicate/invalid input. */
  register(definition: OverlayDefinition): OverlayEntry;
  /** Returns the entry for `id`. Throws `CHART_OVERLAY_UNKNOWN` — never returns undefined. */
  resolve(id: string): OverlayEntry;
  has(id: string): boolean;
  /** Snapshot in registration order. */
  list(): readonly OverlayEntry[];
  /** Total panes in use, including the main pane (>= 1). */
  readonly paneCount: number;
}

function assertUnique(kind: string, names: readonly string[], id: string): void {
  const seen = new Set<string>();
  for (const name of names) {
    if (name.length === 0) {
      throw new OverlayRegistryError("CHART_OVERLAY_INVALID", id, `empty ${kind} name`);
    }
    if (seen.has(name)) {
      throw new OverlayRegistryError("CHART_OVERLAY_INVALID", id, `duplicate ${kind} "${name}"`);
    }
    seen.add(name);
  }
}

function validateDefinition(definition: OverlayDefinition): void {
  const { id, placement, params, outputs } = definition;
  if (typeof id !== "string" || id.length === 0) {
    throw new OverlayRegistryError("CHART_OVERLAY_INVALID", String(id), "indicator id must be a non-empty string");
  }
  if (!PLACEMENTS.has(placement)) {
    throw new OverlayRegistryError("CHART_OVERLAY_INVALID", id, `unknown placement "${String(placement)}"`);
  }
  if (outputs.length === 0) {
    throw new OverlayRegistryError("CHART_OVERLAY_INVALID", id, "at least one output is required");
  }
  assertUnique("param", params, id);
  assertUnique(
    "output",
    outputs.map((o) => o.name),
    id,
  );
}

export function createOverlayRegistry(definitions: readonly OverlayDefinition[] = []): OverlayRegistry {
  const entries = new Map<string, OverlayEntry>();
  let nextSubPane = MAIN_PANE_INDEX + 1;

  function register(definition: OverlayDefinition): OverlayEntry {
    validateDefinition(definition);
    if (entries.has(definition.id)) {
      throw new OverlayRegistryError("CHART_OVERLAY_DUPLICATE", definition.id, "already registered");
    }
    // Validation is complete: only now consume a pane index so a rejected
    // registration never leaves a gap in the pane sequence.
    const paneIndex = definition.placement === "main-overlay" ? MAIN_PANE_INDEX : nextSubPane++;
    const entry: OverlayEntry = {
      id: definition.id,
      placement: definition.placement,
      params: [...definition.params],
      outputs: definition.outputs.map((o) => ({ name: o.name, series: o.series })),
      paneIndex,
    };
    entries.set(entry.id, entry);
    return entry;
  }

  function resolve(id: string): OverlayEntry {
    const entry = entries.get(id);
    if (!entry) {
      throw new OverlayRegistryError("CHART_OVERLAY_UNKNOWN", id, "not registered in overlay registry");
    }
    return entry;
  }

  const registry: OverlayRegistry = {
    register,
    resolve,
    has: (id) => entries.has(id),
    list: () => [...entries.values()],
    get paneCount() {
      return nextSubPane;
    },
  };
  for (const definition of definitions) register(definition);
  return registry;
}

const line = (name: string): OverlayOutput => ({ name, series: "line" });
const histogram = (name: string): OverlayOutput => ({ name, series: "histogram" });

/**
 * Mirror of backend `TALIB_SPECS` (ids / params / outputs) plus AIOS-owned
 * placement. Keep entry order aligned with the Python dict for readability;
 * the drift guard compares by id, so order itself is not a contract.
 */
export const DEFAULT_OVERLAY_DEFINITIONS: readonly OverlayDefinition[] = [
  { id: "SMA", placement: "main-overlay", params: ["timeperiod"], outputs: [line("value")] },
  { id: "EMA", placement: "main-overlay", params: ["timeperiod"], outputs: [line("value")] },
  { id: "RSI", placement: "sub-pane", params: ["timeperiod"], outputs: [line("value")] },
  { id: "ATR", placement: "sub-pane", params: ["timeperiod"], outputs: [line("value")] },
  { id: "CCI", placement: "sub-pane", params: ["timeperiod"], outputs: [line("value")] },
  { id: "WILLR", placement: "sub-pane", params: ["timeperiod"], outputs: [line("value")] },
  { id: "MFI", placement: "sub-pane", params: ["timeperiod"], outputs: [line("value")] },
  {
    id: "MACD",
    placement: "sub-pane",
    params: ["fastperiod", "slowperiod", "signalperiod"],
    outputs: [line("macd"), line("signal"), histogram("hist")],
  },
  {
    id: "BBANDS",
    placement: "main-overlay",
    params: ["timeperiod"],
    outputs: [line("upperband"), line("middleband"), line("lowerband")],
  },
  {
    id: "STOCH",
    placement: "sub-pane",
    params: ["fastk_period", "slowk_period", "slowd_period"],
    outputs: [line("slowk"), line("slowd")],
  },
  { id: "OBV", placement: "sub-pane", params: [], outputs: [line("value")] },
];

export function createDefaultOverlayRegistry(): OverlayRegistry {
  return createOverlayRegistry(DEFAULT_OVERLAY_DEFINITIONS);
}
