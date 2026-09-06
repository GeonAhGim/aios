/**
 * CH-17a — indicator template pure model ("template-v1").
 *
 * A template is a snapshot of one chart panel's indicator set (id + params
 * per indicator, in authoring order) plus its pane layout (CH-14
 * `panes/paneModel.ts` `PaneSpec` shape, verbatim). It does not embed
 * anything renderer/registry-owned: which pane an indicator sits on is read
 * from CH-16 `legend/objectTree.ts`'s inventory (`ObjectTreeEntry.paneId`) at
 * capture time, not re-derived here.
 *
 * `encodeTemplate`/`decodeTemplate` follow the CH-8 `layout/layoutModel.ts`
 * fail-closed contract: unknown/missing/wrong-typed fields are a typed
 * `TemplateError`, never silently dropped or coerced.
 *
 * No renderer/registry/network dependency: pure TypeScript, consumed by
 * `applyTemplate.ts` and by future screen wiring (not part of this leaf).
 */

import type { PaneKind, PaneModel } from "../panes/paneModel";
import type { ChartLayoutModel } from "../layout/layoutModel";
import type { ObjectTreeEntry } from "../legend/objectTree";

export const TEMPLATE_SCHEMA_VERSION = 1;

export interface TemplatePane {
  readonly id: string;
  readonly kind: PaneKind;
  readonly heightRatio: number;
}

export interface TemplateIndicator {
  readonly id: string;
  /** The `PaneSpec.id` (from `TemplatePane`) this indicator is placed on. */
  readonly paneId: string;
  readonly params?: Readonly<Record<string, number>>;
}

export interface Template {
  readonly schemaVersion: typeof TEMPLATE_SCHEMA_VERSION;
  readonly panes: readonly TemplatePane[];
  /** Authoring order — preserved through capture/encode/decode/apply. */
  readonly indicators: readonly TemplateIndicator[];
}

export type TemplateErrorCode =
  | "schema_version"
  | "field_missing"
  | "field_unknown"
  | "field_invalid"
  | "duplicate_pane_id"
  | "unknown_indicator"
  | "inventory_missing_indicator"
  | "no_active_panel"
  | "panel_not_found";

export class TemplateError extends Error {
  readonly code: TemplateErrorCode;

  constructor(code: TemplateErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "TemplateError";
    this.code = code;
  }
}

/**
 * Builds a `Template` from an active chart panel: `inventory` (CH-16
 * `buildObjectTree()` output) supplies each indicator's pane placement,
 * `layoutModel.activePanelId`'s panel supplies indicator ids/params (CH-8
 * `IndicatorRef`), and `paneModel` supplies the pane layout verbatim.
 */
export function capture(
  inventory: readonly ObjectTreeEntry[],
  paneModel: PaneModel,
  layoutModel: ChartLayoutModel,
): Template {
  if (layoutModel.activePanelId === null) {
    throw new TemplateError("no_active_panel", "layoutModel.activePanelId is null");
  }
  const panel = layoutModel.panels.find((p) => p.id === layoutModel.activePanelId);
  if (!panel) {
    throw new TemplateError("panel_not_found", `no panel with id "${layoutModel.activePanelId}"`);
  }

  const paneIdByIndicatorId = new Map(
    inventory.filter((entry) => entry.kind === "indicator").map((entry) => [entry.id, entry.paneId] as const),
  );

  const indicators: TemplateIndicator[] = panel.indicators.map((ref) => {
    const paneId = paneIdByIndicatorId.get(ref.id);
    if (paneId === undefined) {
      throw new TemplateError("inventory_missing_indicator", `indicator "${ref.id}" is not present in inventory`);
    }
    return ref.params === undefined ? { id: ref.id, paneId } : { id: ref.id, paneId, params: ref.params };
  });

  const panes: TemplatePane[] = paneModel.panes.map((pane) => ({
    id: pane.id,
    kind: pane.kind,
    heightRatio: pane.heightRatio,
  }));

  return { schemaVersion: TEMPLATE_SCHEMA_VERSION, panes, indicators };
}

type Json = Record<string, unknown>;

function invalid(field: string, detail: string): TemplateError {
  return new TemplateError("field_invalid", `${field}: ${detail}`);
}

function missing(field: string): TemplateError {
  return new TemplateError("field_missing", `${field}: required`);
}

function unknownField(scope: string, field: string): TemplateError {
  return new TemplateError("field_unknown", `${scope}${field}: unknown field (refusing to drop)`);
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

const PANE_KINDS: readonly PaneKind[] = ["main", "sub"];

function assertUniquePaneIds(panes: readonly TemplatePane[]): void {
  const seen = new Set<string>();
  for (const pane of panes) {
    if (seen.has(pane.id)) throw new TemplateError("duplicate_pane_id", `duplicate pane id "${pane.id}"`);
    seen.add(pane.id);
  }
}

const TEMPLATE_PANE_FIELDS: readonly string[] = ["id", "kind", "heightRatio"];

function encodeTemplatePane(pane: TemplatePane): Json {
  return { id: pane.id, kind: pane.kind, heightRatio: pane.heightRatio };
}

function decodeTemplatePane(value: unknown, index: number): TemplatePane {
  const scope = `panes[${index}]`;
  if (!isObject(value)) throw invalid(scope, "must be an object");
  assertKnownFields(value, TEMPLATE_PANE_FIELDS, `${scope}.`);
  const id = decodeNonEmptyString(requireField(value, "id", `${scope}.`), `${scope}.id`);
  const rawKind = requireField(value, "kind", `${scope}.`);
  if (typeof rawKind !== "string" || !PANE_KINDS.includes(rawKind as PaneKind)) {
    throw invalid(`${scope}.kind`, `must be one of ${PANE_KINDS.join(", ")}`);
  }
  const rawHeightRatio = requireField(value, "heightRatio", `${scope}.`);
  if (typeof rawHeightRatio !== "number" || !Number.isFinite(rawHeightRatio)) {
    throw invalid(`${scope}.heightRatio`, "must be a finite number");
  }
  return { id, kind: rawKind as PaneKind, heightRatio: rawHeightRatio };
}

const TEMPLATE_INDICATOR_FIELDS: readonly string[] = ["id", "paneId", "params"];

function encodeTemplateIndicator(indicator: TemplateIndicator): Json {
  const out: Json = { id: indicator.id, paneId: indicator.paneId };
  if (indicator.params !== undefined) out.params = { ...indicator.params };
  return out;
}

function decodeTemplateIndicator(value: unknown, index: number): TemplateIndicator {
  const scope = `indicators[${index}]`;
  if (!isObject(value)) throw invalid(scope, "must be an object");
  assertKnownFields(value, TEMPLATE_INDICATOR_FIELDS, `${scope}.`);
  const id = decodeNonEmptyString(requireField(value, "id", `${scope}.`), `${scope}.id`);
  const paneId = decodeNonEmptyString(requireField(value, "paneId", `${scope}.`), `${scope}.paneId`);
  if (!Object.hasOwn(value, "params")) return { id, paneId };
  const rawParams = value.params;
  if (!isObject(rawParams)) throw invalid(`${scope}.params`, "must be an object");
  const params: Record<string, number> = {};
  for (const [key, v] of Object.entries(rawParams)) {
    if (typeof v !== "number" || !Number.isFinite(v)) {
      throw invalid(`${scope}.params.${key}`, "must be a finite number");
    }
    params[key] = v;
  }
  return { id, paneId, params };
}

const DOC_FIELDS: readonly string[] = ["schemaVersion", "panes", "indicators"];

/** Plain-object, JSON-safe form. */
export function encodeTemplate(template: Template): Json {
  assertUniquePaneIds(template.panes);
  return {
    schemaVersion: template.schemaVersion,
    panes: template.panes.map(encodeTemplatePane),
    indicators: template.indicators.map(encodeTemplateIndicator),
  };
}

/** Decodes an already-parsed value. Strict, fail-closed. */
export function decodeTemplate(value: unknown): Template {
  if (!isObject(value)) throw invalid("<template>", "must be an object");
  if (!Object.hasOwn(value, "schemaVersion")) {
    throw new TemplateError("schema_version", "schemaVersion is missing");
  }
  if (value.schemaVersion !== TEMPLATE_SCHEMA_VERSION) {
    throw new TemplateError(
      "schema_version",
      `schemaVersion ${JSON.stringify(value.schemaVersion)} is not supported (expected ${TEMPLATE_SCHEMA_VERSION})`,
    );
  }
  assertKnownFields(value, DOC_FIELDS, "");

  const rawPanes = requireField(value, "panes", "");
  if (!Array.isArray(rawPanes)) throw invalid("panes", "must be an array");
  const panes = rawPanes.map((v, i) => decodeTemplatePane(v, i));
  assertUniquePaneIds(panes);

  const rawIndicators = requireField(value, "indicators", "");
  if (!Array.isArray(rawIndicators)) throw invalid("indicators", "must be an array");
  const indicators = rawIndicators.map((v, i) => decodeTemplateIndicator(v, i));

  return { schemaVersion: TEMPLATE_SCHEMA_VERSION, panes, indicators };
}
