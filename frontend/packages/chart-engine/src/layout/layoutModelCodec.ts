/**
 * CH-8 — `encodeLayoutModel`/`decodeLayoutModel` and their private field
 * codecs, moved out of `layoutModel.ts` (P6 300-line split, pure move). The
 * model shape and `LayoutModelError` stay in `layoutModel.ts`; this module
 * only owns the JSON <-> `ChartLayoutModel` boundary and is re-exported from
 * there so the public import path (`@aios/chart-engine/src/layout/layoutModel`,
 * consumed deep by apps/web) is unchanged.
 */

import {
  LAYOUT_MODEL_SCHEMA_VERSION,
  LayoutModelError,
  type ChartLayoutModel,
  type ChartPanel,
  type IndicatorRef,
  type InstrumentRef,
  type Watchlist,
} from "./layoutModel";

type Json = Record<string, unknown>;

function invalid(field: string, detail: string): LayoutModelError {
  return new LayoutModelError("CHART_LAYOUT_FIELD_INVALID", `${field}: ${detail}`);
}

function missing(field: string): LayoutModelError {
  return new LayoutModelError("CHART_LAYOUT_FIELD_MISSING", `${field}: required`);
}

function unknownField(scope: string, field: string): LayoutModelError {
  return new LayoutModelError("CHART_LAYOUT_FIELD_UNKNOWN", `${scope}${field}: unknown field (refusing to drop)`);
}

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertKnownFields(obj: Json, allowed: readonly string[], scope: string): void {
  for (const key of Object.keys(obj)) {
    if (!allowed.includes(key)) throw unknownField(scope, key);
  }
}

function requireField(obj: Json, field: string, scope: string): unknown {
  if (!Object.hasOwn(obj, field)) throw missing(`${scope}${field}`);
  return obj[field];
}

function decodeNonEmptyString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) throw invalid(field, "must be a non-empty string");
  return value;
}

function decodeStringArray(value: unknown, field: string): readonly string[] {
  if (!Array.isArray(value)) throw invalid(field, "must be an array");
  return value.map((v, i) => decodeNonEmptyString(v, `${field}[${i}]`));
}

const INSTRUMENT_FIELDS: readonly string[] = ["instrumentId", "venue", "symbol"];

function encodeInstrument(ref: InstrumentRef): Json {
  return { instrumentId: ref.instrumentId, venue: ref.venue, symbol: ref.symbol };
}

function decodeInstrument(value: unknown, scope: string): InstrumentRef {
  if (!isObject(value)) throw invalid(scope, "must be an object");
  assertKnownFields(value, INSTRUMENT_FIELDS, `${scope}.`);
  return {
    instrumentId: decodeNonEmptyString(requireField(value, "instrumentId", `${scope}.`), `${scope}.instrumentId`),
    venue: decodeNonEmptyString(requireField(value, "venue", `${scope}.`), `${scope}.venue`),
    symbol: decodeNonEmptyString(requireField(value, "symbol", `${scope}.`), `${scope}.symbol`),
  };
}

const INDICATOR_FIELDS: readonly string[] = ["id", "params"];

function encodeIndicator(ref: IndicatorRef): Json {
  const out: Json = { id: ref.id };
  if (ref.params !== undefined) out.params = { ...ref.params };
  return out;
}

function decodeIndicator(value: unknown, scope: string): IndicatorRef {
  if (!isObject(value)) throw invalid(scope, "must be an object");
  assertKnownFields(value, INDICATOR_FIELDS, `${scope}.`);
  const id = decodeNonEmptyString(requireField(value, "id", `${scope}.`), `${scope}.id`);
  if (!Object.hasOwn(value, "params")) return { id };
  const rawParams = value.params;
  if (!isObject(rawParams)) throw invalid(`${scope}.params`, "must be an object");
  const params: Record<string, number> = {};
  for (const [key, v] of Object.entries(rawParams)) {
    if (typeof v !== "number" || !Number.isFinite(v)) {
      throw invalid(`${scope}.params.${key}`, "must be a finite number");
    }
    params[key] = v;
  }
  return { id, params };
}

const PANEL_FIELDS: readonly string[] = [
  "id",
  "instrument",
  "timeframe",
  "indicators",
  "drawingSetId",
  "objectTreeOrder",
  "lockedIndicatorIds",
];

function encodePanel(panel: ChartPanel): Json {
  return {
    id: panel.id,
    instrument: encodeInstrument(panel.instrument),
    timeframe: panel.timeframe,
    indicators: panel.indicators.map(encodeIndicator),
    drawingSetId: panel.drawingSetId,
    objectTreeOrder: panel.objectTreeOrder ? [...panel.objectTreeOrder] : [],
    lockedIndicatorIds: panel.lockedIndicatorIds ? [...panel.lockedIndicatorIds] : [],
  };
}

function decodePanel(value: unknown, index: number): ChartPanel {
  const scope = `panels[${index}]`;
  if (!isObject(value)) throw invalid(scope, "must be an object");
  assertKnownFields(value, PANEL_FIELDS, `${scope}.`);
  const id = decodeNonEmptyString(requireField(value, "id", `${scope}.`), `${scope}.id`);
  const instrument = decodeInstrument(requireField(value, "instrument", `${scope}.`), `${scope}.instrument`);
  const timeframe = decodeNonEmptyString(requireField(value, "timeframe", `${scope}.`), `${scope}.timeframe`);
  const rawIndicators = requireField(value, "indicators", `${scope}.`);
  if (!Array.isArray(rawIndicators)) throw invalid(`${scope}.indicators`, "must be an array");
  const indicators = rawIndicators.map((v, i) => decodeIndicator(v, `${scope}.indicators[${i}]`));
  const drawingSetId = decodeNonEmptyString(
    requireField(value, "drawingSetId", `${scope}.`),
    `${scope}.drawingSetId`,
  );
  // CH-16b: additive fields — a layout saved before this leaf lacks them entirely; that
  // means "no persisted order/locks yet", not a malformed document, so default to [].
  const objectTreeOrder = Object.hasOwn(value, "objectTreeOrder")
    ? decodeStringArray(value.objectTreeOrder, `${scope}.objectTreeOrder`)
    : [];
  const lockedIndicatorIds = Object.hasOwn(value, "lockedIndicatorIds")
    ? decodeStringArray(value.lockedIndicatorIds, `${scope}.lockedIndicatorIds`)
    : [];
  return { id, instrument, timeframe, indicators, drawingSetId, objectTreeOrder, lockedIndicatorIds };
}

const WATCHLIST_FIELDS: readonly string[] = ["id", "name", "entries"];

function encodeWatchlist(list: Watchlist): Json {
  return { id: list.id, name: list.name, entries: list.entries.map(encodeInstrument) };
}

function decodeWatchlist(value: unknown, index: number): Watchlist {
  const scope = `watchlists[${index}]`;
  if (!isObject(value)) throw invalid(scope, "must be an object");
  assertKnownFields(value, WATCHLIST_FIELDS, `${scope}.`);
  const id = decodeNonEmptyString(requireField(value, "id", `${scope}.`), `${scope}.id`);
  const name = decodeNonEmptyString(requireField(value, "name", `${scope}.`), `${scope}.name`);
  const rawEntries = requireField(value, "entries", `${scope}.`);
  if (!Array.isArray(rawEntries)) throw invalid(`${scope}.entries`, "must be an array");
  const entries = rawEntries.map((v, i) => decodeInstrument(v, `${scope}.entries[${i}]`));
  return { id, name, entries };
}

function assertUniqueIds(ids: readonly string[], kind: string): void {
  const seen = new Set<string>();
  for (const id of ids) {
    if (seen.has(id)) throw new LayoutModelError("CHART_LAYOUT_DUPLICATE_ID", `duplicate ${kind} id "${id}"`);
    seen.add(id);
  }
}

const DOC_FIELDS: readonly string[] = ["schemaVersion", "panels", "activePanelId", "watchlists"];

/** Plain-object, JSON-safe form — this is what goes into `ChartLayoutView.layout_state`. */
export function encodeLayoutModel(model: ChartLayoutModel): Json {
  assertUniqueIds(model.panels.map((p) => p.id), "panel");
  assertUniqueIds(model.watchlists.map((w) => w.id), "watchlist");
  if (model.activePanelId !== null && !model.panels.some((p) => p.id === model.activePanelId)) {
    throw invalid("activePanelId", `references unknown panel id "${model.activePanelId}"`);
  }
  return {
    schemaVersion: model.schemaVersion,
    panels: model.panels.map(encodePanel),
    activePanelId: model.activePanelId,
    watchlists: model.watchlists.map(encodeWatchlist),
  };
}

/** Decodes an already-parsed value (e.g. `ChartLayoutView.layoutState`). Strict, fail-closed. */
export function decodeLayoutModel(value: unknown): ChartLayoutModel {
  if (!isObject(value)) throw invalid("<layout>", "must be an object");
  if (!Object.hasOwn(value, "schemaVersion")) {
    throw new LayoutModelError("CHART_LAYOUT_SCHEMA_UNSUPPORTED", "schemaVersion is missing");
  }
  if (value.schemaVersion !== LAYOUT_MODEL_SCHEMA_VERSION) {
    throw new LayoutModelError(
      "CHART_LAYOUT_SCHEMA_UNSUPPORTED",
      `schemaVersion ${JSON.stringify(value.schemaVersion)} is not supported (expected ${LAYOUT_MODEL_SCHEMA_VERSION})`,
    );
  }
  assertKnownFields(value, DOC_FIELDS, "");
  const rawPanels = requireField(value, "panels", "");
  if (!Array.isArray(rawPanels)) throw invalid("panels", "must be an array");
  const panels = rawPanels.map((v, i) => decodePanel(v, i));
  assertUniqueIds(
    panels.map((p) => p.id),
    "panel",
  );

  const rawActivePanelId = requireField(value, "activePanelId", "");
  if (rawActivePanelId !== null && typeof rawActivePanelId !== "string") {
    throw invalid("activePanelId", "must be a string or null");
  }
  if (rawActivePanelId !== null && !panels.some((p) => p.id === rawActivePanelId)) {
    throw invalid("activePanelId", `references unknown panel id "${rawActivePanelId}"`);
  }

  const rawWatchlists = requireField(value, "watchlists", "");
  if (!Array.isArray(rawWatchlists)) throw invalid("watchlists", "must be an array");
  const watchlists = rawWatchlists.map((v, i) => decodeWatchlist(v, i));
  assertUniqueIds(
    watchlists.map((w) => w.id),
    "watchlist",
  );

  return {
    schemaVersion: LAYOUT_MODEL_SCHEMA_VERSION,
    panels,
    activePanelId: rawActivePanelId,
    watchlists,
  };
}
