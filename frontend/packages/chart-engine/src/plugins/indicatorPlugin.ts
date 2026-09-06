/**
 * CH-11 — indicator plugin API: registers a server-catalog indicator
 * (IND-12, `GET /v1/indicators` 3-tier CORE/OSS/SCRIPT catalog) onto a chart
 * instance with an explicit overlay/pane placement and a per-output style.
 *
 * Thin layer, not a new registry (decision): placement for CORE-tier
 * indicators is read from CH-3 `indicators/overlayRegistry.ts`
 * (task-1401, df1fd5f) — the mirror of backend `TALIB_SPECS` — and is
 * authoritative (a caller-supplied placement that disagrees is rejected,
 * never silently overridden). OSS/SCRIPT-tier entries have no overlay
 * registry mirror, so the caller must supply `placement` explicitly.
 * Sub-pane allocation reuses CH-14 `panes/paneModel.ts` (task-1710,
 * 64b6fd9) verbatim — this module never computes height ratios itself.
 *
 * Unlike `OverlayRegistry` (session-long, no unregister), plugin instances
 * are added/removed per chart lifecycle (DoD: register/unregister), so this
 * module keeps its own ordered, immutable `IndicatorPluginRegistry` rather
 * than mutating the overlay registry.
 *
 * Catalog fetch is dependency-injected (`IndicatorCatalogPort`, same shape
 * as CH-8 `layout/persistence.ts` `ChartingPort`) — this package has no
 * fetch/vendor dependency; the concrete port is `@aios/api-client`
 * `clients/indicators.ts`, which resolves the path via `apiPaths.ts`
 * `resolvePath("indicators.list")` (no hardcoded path). Any failure from
 * the port — network, HTTP, or the port's own response-parsing throw — is
 * surfaced through the single `routeApiError` entry point rather than
 * hidden: `loadIndicatorCatalog` never swallows an error, it classifies it.
 */

import { routeApiError, type RoutedApiError } from "@aios/shared-types";
import { type PaneModel, addPane, mainPane, removePane } from "../panes/paneModel";
import type { OverlayPlacement, OverlayRegistry } from "../indicators/overlayRegistry";

export type IndicatorTier = "core" | "oss" | "script";

/** Mirrors `IndicatorListItemView` (`src/api/schemas/indicators.py`, IND-12). */
export interface IndicatorCatalogEntry {
  readonly name: string;
  readonly tier: IndicatorTier;
  readonly category: string;
  readonly version: string;
  readonly hash: string;
  readonly inputs: readonly string[];
  readonly outputs: readonly string[];
}

export interface IndicatorCatalogQuery {
  readonly q?: string;
  readonly category?: string;
  readonly cursor?: string;
  readonly limit?: number;
}

export interface IndicatorCatalogPage {
  readonly items: readonly IndicatorCatalogEntry[];
  readonly nextCursor: string | null;
}

/** Injected port — matches `@aios/api-client` `clients/indicators.ts` 1:1. */
export interface IndicatorCatalogPort {
  listIndicators(query?: IndicatorCatalogQuery): Promise<IndicatorCatalogPage>;
}

export type IndicatorCatalogLoadResult =
  | { readonly kind: "ok"; readonly page: IndicatorCatalogPage }
  | { readonly kind: "error"; readonly routed: RoutedApiError };

// A list endpoint has no optimistic-locking states (unlike persistence.ts's
// conflict/not_found) — every failure, network or parsing, collapses to one
// classification via routeApiError. routeApiError never throws (falls back
// to "unknown"), so this never re-hides a failure the caller didn't ask for.
export async function loadIndicatorCatalog(
  port: IndicatorCatalogPort,
  query: IndicatorCatalogQuery = {},
): Promise<IndicatorCatalogLoadResult> {
  try {
    return { kind: "ok", page: await port.listIndicators(query) };
  } catch (err) {
    return { kind: "error", routed: routeApiError(err) };
  }
}

export interface IndicatorStyleOutput {
  /** Must match one of `IndicatorCatalogEntry.outputs`. */
  readonly output: string;
  readonly color: string;
  readonly lineWidth: number;
  readonly visible: boolean;
}

export interface IndicatorStyle {
  readonly outputs: readonly IndicatorStyleOutput[];
}

export type IndicatorPluginErrorCode =
  | "INDICATOR_PLUGIN_DUPLICATE"
  | "INDICATOR_PLUGIN_UNKNOWN"
  | "INDICATOR_PLUGIN_INVALID"
  | "INDICATOR_PLUGIN_PLACEMENT_REQUIRED";

export class IndicatorPluginError extends Error {
  readonly code: IndicatorPluginErrorCode;
  readonly instanceId: string;

  constructor(code: IndicatorPluginErrorCode, instanceId: string, detail: string) {
    super(`${code}: ${detail} (instance "${instanceId}")`);
    this.name = "IndicatorPluginError";
    this.code = code;
    this.instanceId = instanceId;
  }
}

export interface IndicatorPluginParams {
  readonly [name: string]: number | string | boolean;
}

export interface IndicatorPluginDefinition {
  /** Unique per chart instance — distinct from `catalogEntry.name` (the same indicator can be added twice). */
  readonly instanceId: string;
  readonly catalogEntry: IndicatorCatalogEntry;
  /** Required only when `catalogEntry.name` has no CORE-tier overlay registry entry. */
  readonly placement?: OverlayPlacement;
  readonly params: IndicatorPluginParams;
  readonly style: IndicatorStyle;
}

export interface IndicatorPluginEntry {
  readonly instanceId: string;
  readonly catalogEntry: IndicatorCatalogEntry;
  readonly placement: OverlayPlacement;
  /** paneModel pane id this instance renders into (main pane id for overlays, `instanceId` for sub-panes). */
  readonly paneId: string;
  readonly params: IndicatorPluginParams;
  readonly style: IndicatorStyle;
}

export interface IndicatorPluginRegistry {
  /** Snapshot in registration order. */
  readonly entries: readonly IndicatorPluginEntry[];
}

export function createIndicatorPluginRegistry(): IndicatorPluginRegistry {
  return { entries: [] };
}

function resolvePlacement(
  overlayRegistry: OverlayRegistry,
  catalogEntry: IndicatorCatalogEntry,
  instanceId: string,
  explicit: OverlayPlacement | undefined,
): OverlayPlacement {
  if (overlayRegistry.has(catalogEntry.name)) {
    const overlayPlacement = overlayRegistry.resolve(catalogEntry.name).placement;
    if (explicit !== undefined && explicit !== overlayPlacement) {
      throw new IndicatorPluginError(
        "INDICATOR_PLUGIN_INVALID",
        instanceId,
        `placement "${explicit}" conflicts with overlay registry placement "${overlayPlacement}" for "${catalogEntry.name}"`,
      );
    }
    return overlayPlacement;
  }
  if (explicit === undefined) {
    throw new IndicatorPluginError(
      "INDICATOR_PLUGIN_PLACEMENT_REQUIRED",
      instanceId,
      `"${catalogEntry.name}" (tier="${catalogEntry.tier}") has no overlay registry entry — placement must be given explicitly`,
    );
  }
  return explicit;
}

function validateStyle(style: IndicatorStyle, catalogEntry: IndicatorCatalogEntry, instanceId: string): void {
  const seen = new Set<string>();
  for (const entry of style.outputs) {
    if (!catalogEntry.outputs.includes(entry.output)) {
      throw new IndicatorPluginError(
        "INDICATOR_PLUGIN_INVALID",
        instanceId,
        `style output "${entry.output}" is not one of "${catalogEntry.name}"'s outputs`,
      );
    }
    if (seen.has(entry.output)) {
      throw new IndicatorPluginError("INDICATOR_PLUGIN_INVALID", instanceId, `duplicate style output "${entry.output}"`);
    }
    seen.add(entry.output);
    if (!(entry.lineWidth > 0)) {
      throw new IndicatorPluginError(
        "INDICATOR_PLUGIN_INVALID",
        instanceId,
        `lineWidth must be > 0 for output "${entry.output}", got ${entry.lineWidth}`,
      );
    }
  }
}

export interface IndicatorPluginRegistration {
  readonly registry: IndicatorPluginRegistry;
  readonly paneModel: PaneModel;
}

export function registerIndicatorPlugin(
  registry: IndicatorPluginRegistry,
  paneModel: PaneModel,
  overlayRegistry: OverlayRegistry,
  definition: IndicatorPluginDefinition,
): IndicatorPluginRegistration {
  if (registry.entries.some((e) => e.instanceId === definition.instanceId)) {
    throw new IndicatorPluginError("INDICATOR_PLUGIN_DUPLICATE", definition.instanceId, "already registered");
  }
  const placement = resolvePlacement(overlayRegistry, definition.catalogEntry, definition.instanceId, definition.placement);
  validateStyle(definition.style, definition.catalogEntry, definition.instanceId);

  const nextPaneModel = placement === "sub-pane" ? addPane(paneModel, definition.instanceId) : paneModel;
  const paneId = placement === "sub-pane" ? definition.instanceId : mainPane(paneModel).id;

  const entry: IndicatorPluginEntry = {
    instanceId: definition.instanceId,
    catalogEntry: definition.catalogEntry,
    placement,
    paneId,
    params: definition.params,
    style: definition.style,
  };
  return { registry: { entries: [...registry.entries, entry] }, paneModel: nextPaneModel };
}

export function unregisterIndicatorPlugin(
  registry: IndicatorPluginRegistry,
  paneModel: PaneModel,
  instanceId: string,
): IndicatorPluginRegistration {
  const entry = registry.entries.find((e) => e.instanceId === instanceId);
  if (!entry) throw new IndicatorPluginError("INDICATOR_PLUGIN_UNKNOWN", instanceId, "not registered");
  const nextPaneModel = entry.placement === "sub-pane" ? removePane(paneModel, entry.paneId) : paneModel;
  return {
    registry: { entries: registry.entries.filter((e) => e.instanceId !== instanceId) },
    paneModel: nextPaneModel,
  };
}

export function setIndicatorPluginStyle(
  registry: IndicatorPluginRegistry,
  instanceId: string,
  style: IndicatorStyle,
): IndicatorPluginRegistry {
  const entry = registry.entries.find((e) => e.instanceId === instanceId);
  if (!entry) throw new IndicatorPluginError("INDICATOR_PLUGIN_UNKNOWN", instanceId, "not registered");
  validateStyle(style, entry.catalogEntry, instanceId);
  return { entries: registry.entries.map((e) => (e.instanceId === instanceId ? { ...e, style } : e)) };
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Plain-JSON-safe encoding for persistence (CH-8 `layout/persistence.ts` layoutState payloads). */
export function encodeIndicatorStyle(style: IndicatorStyle): Record<string, unknown> {
  return {
    outputs: style.outputs.map((o) => ({ output: o.output, color: o.color, lineWidth: o.lineWidth, visible: o.visible })),
  };
}

// Fail-closed, no silent fallback (matches OverlayRegistryError/PaneModelError
// convention) — a malformed persisted style throws rather than rendering with
// guessed defaults.
export function decodeIndicatorStyle(raw: unknown): IndicatorStyle {
  if (!isPlainObject(raw) || !Array.isArray(raw.outputs)) {
    throw new IndicatorPluginError("INDICATOR_PLUGIN_INVALID", "(style)", "malformed style document: missing outputs array");
  }
  const outputs = raw.outputs.map((item, index) => {
    if (
      !isPlainObject(item) ||
      typeof item.output !== "string" ||
      typeof item.color !== "string" ||
      typeof item.lineWidth !== "number" ||
      typeof item.visible !== "boolean"
    ) {
      throw new IndicatorPluginError("INDICATOR_PLUGIN_INVALID", "(style)", `malformed style output at index ${index}`);
    }
    return { output: item.output, color: item.color, lineWidth: item.lineWidth, visible: item.visible };
  });
  return { outputs };
}
