/**
 * CH-4 — lossless JSON serialization of a `DrawingCollection`.
 *
 * Document shape: `{ "schema_version": 1, "drawings": [...] }`.
 * Decoding is strict and fail-closed:
 *   - unsupported/missing `schema_version` → CHART_DRAWING_SCHEMA_UNSUPPORTED
 *   - missing required field               → CHART_DRAWING_FIELD_MISSING
 *   - unknown field (would be dropped)     → CHART_DRAWING_FIELD_UNKNOWN
 *   - wrong type / non-finite number       → CHART_DRAWING_FIELD_INVALID
 * A drawing that survives decoding is a full-fidelity copy of the encoded one,
 * so `decode(encode(x))` deep-equals `x` and `encode(decode(s))` equals `s`
 * for canonical input. Keys are emitted in a fixed order for determinism.
 */

import {
  DrawingError,
  assertValidDrawing,
  isDrawingKind,
  type Drawing,
  type DrawingCollection,
  type DrawingKind,
  type DrawingPoint,
  type DrawingStyle,
} from "./model";

export const DRAWINGS_SCHEMA_VERSION = 1;

export interface DrawingsDocument {
  readonly schema_version: typeof DRAWINGS_SCHEMA_VERSION;
  readonly drawings: readonly Drawing[];
}

type Json = Record<string, unknown>;

// ── encode ──────────────────────────────────────────────────────────────────

function encodePoint(p: DrawingPoint): Json {
  return { time: p.time, price: p.price };
}

function encodeStyle(style: DrawingStyle): Json {
  const out: Json = {};
  if (style.color !== undefined) out.color = style.color;
  if (style.lineWidth !== undefined) out.lineWidth = style.lineWidth;
  return out;
}

function encodeDrawing(drawing: Drawing): Json {
  assertValidDrawing(drawing);
  const out: Json = { id: drawing.id, kind: drawing.kind };
  switch (drawing.kind) {
    case "trendline":
    case "rectangle":
      out.points = drawing.points.map(encodePoint);
      break;
    case "fibonacci":
      out.points = drawing.points.map(encodePoint);
      out.levels = [...drawing.levels];
      break;
    case "horizontal-line":
      out.price = drawing.price;
      break;
    case "vertical-line":
      out.time = drawing.time;
      break;
  }
  if (drawing.locked !== undefined) out.locked = drawing.locked;
  if (drawing.style !== undefined) out.style = encodeStyle(drawing.style);
  return out;
}

/** Plain-object form (already JSON-safe). */
export function toDrawingsDocument(collection: DrawingCollection): DrawingsDocument {
  const seen = new Set<string>();
  const drawings = collection.map((d) => {
    const encoded = encodeDrawing(d);
    if (seen.has(d.id)) throw new DrawingError("CHART_DRAWING_DUPLICATE", d.id, "duplicate id in collection");
    seen.add(d.id);
    return encoded as unknown as Drawing;
  });
  return { schema_version: DRAWINGS_SCHEMA_VERSION, drawings };
}

export function serializeDrawings(collection: DrawingCollection): string {
  return JSON.stringify(toDrawingsDocument(collection));
}

// ── decode ──────────────────────────────────────────────────────────────────

const COMMON_FIELDS: readonly string[] = ["id", "kind", "locked", "style"];
const KIND_FIELDS: Readonly<Record<DrawingKind, readonly string[]>> = {
  trendline: ["points"],
  rectangle: ["points"],
  fibonacci: ["points", "levels"],
  "horizontal-line": ["price"],
  "vertical-line": ["time"],
};
const STYLE_FIELDS: readonly string[] = ["color", "lineWidth"];
const DOC_FIELDS: readonly string[] = ["schema_version", "drawings"];

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fieldError(code: DrawingError["code"], id: string, field: string, detail: string): DrawingError {
  return new DrawingError(code, id, `${field}: ${detail}`);
}

function assertKnownFields(id: string, obj: Json, allowed: readonly string[], scope: string): void {
  for (const key of Object.keys(obj)) {
    if (!allowed.includes(key)) {
      throw fieldError("CHART_DRAWING_FIELD_UNKNOWN", id, `${scope}${key}`, "unknown field (refusing to drop)");
    }
  }
}

function requireField(id: string, obj: Json, field: string): unknown {
  if (!Object.hasOwn(obj, field)) throw fieldError("CHART_DRAWING_FIELD_MISSING", id, field, "required");
  return obj[field];
}

function decodeNumber(id: string, field: string, value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw fieldError("CHART_DRAWING_FIELD_INVALID", id, field, "must be a finite number");
  }
  return value;
}

function decodePoint(id: string, field: string, value: unknown): DrawingPoint {
  if (!isObject(value)) throw fieldError("CHART_DRAWING_FIELD_INVALID", id, field, "must be an object");
  assertKnownFields(id, value, ["time", "price"], `${field}.`);
  return {
    time: decodeNumber(id, `${field}.time`, requireField(id, value, "time")),
    price: decodeNumber(id, `${field}.price`, requireField(id, value, "price")),
  };
}

function decodePoints(id: string, value: unknown): [DrawingPoint, DrawingPoint] {
  if (!Array.isArray(value) || value.length !== 2) {
    throw fieldError("CHART_DRAWING_FIELD_INVALID", id, "points", "must be an array of exactly two points");
  }
  return [decodePoint(id, "points[0]", value[0]), decodePoint(id, "points[1]", value[1])];
}

function decodeLevels(id: string, value: unknown): number[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw fieldError("CHART_DRAWING_FIELD_INVALID", id, "levels", "must be a non-empty array");
  }
  return value.map((v, i) => decodeNumber(id, `levels[${i}]`, v));
}

function decodeStyle(id: string, value: unknown): DrawingStyle {
  if (!isObject(value)) throw fieldError("CHART_DRAWING_FIELD_INVALID", id, "style", "must be an object");
  assertKnownFields(id, value, STYLE_FIELDS, "style.");
  const style: { color?: string; lineWidth?: number } = {};
  if (Object.hasOwn(value, "color")) {
    if (typeof value.color !== "string" || value.color.length === 0) {
      throw fieldError("CHART_DRAWING_FIELD_INVALID", id, "style.color", "must be a non-empty string");
    }
    style.color = value.color;
  }
  if (Object.hasOwn(value, "lineWidth")) {
    const width = decodeNumber(id, "style.lineWidth", value.lineWidth);
    if (width <= 0) throw fieldError("CHART_DRAWING_FIELD_INVALID", id, "style.lineWidth", "must be > 0");
    style.lineWidth = width;
  }
  return style;
}

function decodeDrawing(value: unknown, index: number): Drawing {
  const slot = `drawings[${index}]`;
  if (!isObject(value)) throw fieldError("CHART_DRAWING_FIELD_INVALID", slot, slot, "must be an object");
  const rawId = requireField(slot, value, "id");
  if (typeof rawId !== "string" || rawId.length === 0) {
    throw fieldError("CHART_DRAWING_FIELD_INVALID", slot, "id", "must be a non-empty string");
  }
  const id = rawId;
  const kind = requireField(id, value, "kind");
  if (!isDrawingKind(kind)) {
    throw fieldError("CHART_DRAWING_FIELD_INVALID", id, "kind", `unknown kind "${String(kind)}"`);
  }
  assertKnownFields(id, value, [...COMMON_FIELDS, ...KIND_FIELDS[kind]], "");

  let drawing: Drawing;
  switch (kind) {
    case "trendline":
    case "rectangle":
      drawing = { id, kind, points: decodePoints(id, requireField(id, value, "points")) };
      break;
    case "fibonacci":
      drawing = {
        id,
        kind,
        points: decodePoints(id, requireField(id, value, "points")),
        levels: decodeLevels(id, requireField(id, value, "levels")),
      };
      break;
    case "horizontal-line":
      drawing = { id, kind, price: decodeNumber(id, "price", requireField(id, value, "price")) };
      break;
    case "vertical-line":
      drawing = { id, kind, time: decodeNumber(id, "time", requireField(id, value, "time")) };
      break;
  }
  if (Object.hasOwn(value, "locked")) {
    if (typeof value.locked !== "boolean") {
      throw fieldError("CHART_DRAWING_FIELD_INVALID", id, "locked", "must be a boolean");
    }
    drawing = { ...drawing, locked: value.locked };
  }
  if (Object.hasOwn(value, "style")) drawing = { ...drawing, style: decodeStyle(id, value.style) };
  assertValidDrawing(drawing);
  return drawing;
}

/** Decodes an already-parsed value (e.g. from an API envelope `data`). */
export function fromDrawingsDocument(value: unknown): DrawingCollection {
  const doc = "<document>";
  if (!isObject(value)) throw fieldError("CHART_DRAWING_FIELD_INVALID", doc, "document", "must be an object");
  if (!Object.hasOwn(value, "schema_version")) {
    throw new DrawingError("CHART_DRAWING_SCHEMA_UNSUPPORTED", doc, "schema_version is missing");
  }
  if (value.schema_version !== DRAWINGS_SCHEMA_VERSION) {
    throw new DrawingError(
      "CHART_DRAWING_SCHEMA_UNSUPPORTED",
      doc,
      `schema_version ${JSON.stringify(value.schema_version)} is not supported (expected ${DRAWINGS_SCHEMA_VERSION})`,
    );
  }
  assertKnownFields(doc, value, DOC_FIELDS, "");
  const drawings = requireField(doc, value, "drawings");
  if (!Array.isArray(drawings)) throw fieldError("CHART_DRAWING_FIELD_INVALID", doc, "drawings", "must be an array");

  const seen = new Set<string>();
  return drawings.map((raw, i) => {
    const drawing = decodeDrawing(raw, i);
    if (seen.has(drawing.id)) {
      throw new DrawingError("CHART_DRAWING_DUPLICATE", drawing.id, "duplicate id in document");
    }
    seen.add(drawing.id);
    return drawing;
  });
}

/** Parses JSON text; malformed JSON surfaces as CHART_DRAWING_FIELD_INVALID. */
export function deserializeDrawings(json: string): DrawingCollection {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new DrawingError("CHART_DRAWING_FIELD_INVALID", "<document>", `malformed JSON: ${reason}`);
  }
  return fromDrawingsDocument(parsed);
}
