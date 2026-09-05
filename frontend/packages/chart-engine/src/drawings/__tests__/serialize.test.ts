import { describe, expect, it } from "vitest";
import type { Drawing } from "../model";
import {
  DRAWINGS_SCHEMA_VERSION,
  deserializeDrawings,
  fromDrawingsDocument,
  serializeDrawings,
  toDrawingsDocument,
} from "../serialize";
import { createFibonacci, createHorizontalLine, createTrendLine, createVerticalLine } from "../tools";
import { createRng, genCollection } from "./arbitraries";
import { expectDrawingError } from "./helpers";

const sample: readonly Drawing[] = [
  createTrendLine("t", { time: 1, price: 2 }, { time: 3, price: 4 }, { style: { color: "#f00" } }),
  createHorizontalLine("h", 100.5, { locked: true }),
  createVerticalLine("v", 1_700_000_000_000),
  createFibonacci("f", { time: 0, price: 0 }, { time: 10, price: 10 }, [0, 0.5, 1], { style: { lineWidth: 2 } }),
];

describe("document shape", () => {
  it("emits schema_version and a fixed key order", () => {
    const doc = toDrawingsDocument(sample);
    expect(doc.schema_version).toBe(DRAWINGS_SCHEMA_VERSION);
    expect(Object.keys(doc)).toEqual(["schema_version", "drawings"]);
    expect(Object.keys(doc.drawings[0]!)).toEqual(["id", "kind", "points", "style"]);
    expect(Object.keys(doc.drawings[3]!)).toEqual(["id", "kind", "points", "levels", "style"]);
    expect(serializeDrawings([])).toBe('{"schema_version":1,"drawings":[]}');
  });

  it("is deterministic: same collection → identical text", () => {
    expect(serializeDrawings(sample)).toBe(serializeDrawings(sample.map((d) => ({ ...d }))));
  });

  it("rejects invalid or duplicate drawings at encode time", () => {
    expectDrawingError(() => serializeDrawings([createHorizontalLine("h", 1), createVerticalLine("h", 1)]), "CHART_DRAWING_DUPLICATE", "h");
    const nan = { id: "n", kind: "horizontal-line", price: Number.NaN } as Drawing;
    expectDrawingError(() => serializeDrawings([nan]), "CHART_DRAWING_INVALID", "n");
  });
});

describe("round trip", () => {
  it("decode(encode(x)) deep-equals x for the hand-written sample", () => {
    const back = deserializeDrawings(serializeDrawings(sample));
    expect(back).toEqual(sample);
    expect(back).not.toBe(sample);
  });

  it("encode(decode(s)) === s (canonical text is a fixed point)", () => {
    const text = serializeDrawings(sample);
    expect(serializeDrawings(deserializeDrawings(text))).toBe(text);
  });

  it("property: lossless for 300 seeded random collections", () => {
    const rng = createRng(0x5eed);
    for (let i = 0; i < 300; i++) {
      const collection = genCollection(rng);
      const text = serializeDrawings(collection);
      const back = deserializeDrawings(text);
      expect(back, `case ${i}`).toStrictEqual(collection);
      expect(serializeDrawings(back), `case ${i} fixed point`).toBe(text);
      expect(fromDrawingsDocument(JSON.parse(text)), `case ${i} object form`).toStrictEqual(collection);
    }
  });
});

function docWith(drawing: Record<string, unknown>): unknown {
  return { schema_version: DRAWINGS_SCHEMA_VERSION, drawings: [drawing] };
}

describe("negative: schema version", () => {
  it.each<[string, unknown]>([
    ["missing", { drawings: [] }],
    ["future version", { schema_version: 2, drawings: [] }],
    ["zero", { schema_version: 0, drawings: [] }],
    ["string version", { schema_version: "1", drawings: [] }],
    ["null version", { schema_version: null, drawings: [] }],
  ])("rejects %s with CHART_DRAWING_SCHEMA_UNSUPPORTED", (_label, doc) => {
    const err = expectDrawingError(() => fromDrawingsDocument(doc), "CHART_DRAWING_SCHEMA_UNSUPPORTED");
    expect(err.message).toContain("schema_version");
  });
});

describe("negative: document structure", () => {
  it.each<[string, unknown, string]>([
    ["non-object document", 42, "CHART_DRAWING_FIELD_INVALID"],
    ["array document", [], "CHART_DRAWING_FIELD_INVALID"],
    ["null document", null, "CHART_DRAWING_FIELD_INVALID"],
    ["missing drawings", { schema_version: 1 }, "CHART_DRAWING_FIELD_MISSING"],
    ["drawings not array", { schema_version: 1, drawings: {} }, "CHART_DRAWING_FIELD_INVALID"],
    ["unknown top-level field", { schema_version: 1, drawings: [], extra: 1 }, "CHART_DRAWING_FIELD_UNKNOWN"],
    ["non-object drawing", docWith(1 as unknown as Record<string, unknown>), "CHART_DRAWING_FIELD_INVALID"],
  ])("rejects %s", (_label, doc, code) => {
    expectDrawingError(() => fromDrawingsDocument(doc), code as never);
  });

  it("rejects malformed JSON text with CHART_DRAWING_FIELD_INVALID", () => {
    const err = expectDrawingError(() => deserializeDrawings("{not json"), "CHART_DRAWING_FIELD_INVALID");
    expect(err.message).toContain("malformed JSON");
  });

  it("rejects duplicate ids across drawings", () => {
    const doc = { schema_version: 1, drawings: [{ id: "a", kind: "horizontal-line", price: 1 }, { id: "a", kind: "vertical-line", time: 1 }] };
    expectDrawingError(() => fromDrawingsDocument(doc), "CHART_DRAWING_DUPLICATE", "a");
  });
});

describe("negative: per-drawing fields (no silent drop, no coercion)", () => {
  const trend = { id: "t", kind: "trendline", points: [{ time: 1, price: 2 }, { time: 3, price: 4 }] };

  it.each<[string, Record<string, unknown>, string]>([
    ["missing id", { kind: "horizontal-line", price: 1 }, "CHART_DRAWING_FIELD_MISSING"],
    ["empty id", { id: "", kind: "horizontal-line", price: 1 }, "CHART_DRAWING_FIELD_INVALID"],
    ["missing kind", { id: "x", price: 1 }, "CHART_DRAWING_FIELD_MISSING"],
    ["unknown kind", { id: "x", kind: "ellipse", points: [] }, "CHART_DRAWING_FIELD_INVALID"],
    ["missing price", { id: "h", kind: "horizontal-line" }, "CHART_DRAWING_FIELD_MISSING"],
    ["string price (no coercion)", { id: "h", kind: "horizontal-line", price: "1" }, "CHART_DRAWING_FIELD_INVALID"],
    ["null time", { id: "v", kind: "vertical-line", time: null }, "CHART_DRAWING_FIELD_INVALID"],
    ["missing points", { id: "t", kind: "trendline" }, "CHART_DRAWING_FIELD_MISSING"],
    ["one point", { ...trend, points: [trend.points[0]] }, "CHART_DRAWING_FIELD_INVALID"],
    ["point missing price", { ...trend, points: [{ time: 1 }, trend.points[1]] }, "CHART_DRAWING_FIELD_MISSING"],
    ["point with extra field", { ...trend, points: [{ time: 1, price: 2, z: 3 }, trend.points[1]] }, "CHART_DRAWING_FIELD_UNKNOWN"],
    ["unknown drawing field", { ...trend, colour: "red" }, "CHART_DRAWING_FIELD_UNKNOWN"],
    ["field from another kind", { ...trend, price: 1 }, "CHART_DRAWING_FIELD_UNKNOWN"],
    ["fibonacci missing levels", { ...trend, kind: "fibonacci" }, "CHART_DRAWING_FIELD_MISSING"],
    ["fibonacci empty levels", { ...trend, kind: "fibonacci", levels: [] }, "CHART_DRAWING_FIELD_INVALID"],
    ["fibonacci string level", { ...trend, kind: "fibonacci", levels: ["0.5"] }, "CHART_DRAWING_FIELD_INVALID"],
    ["locked as string", { ...trend, locked: "true" }, "CHART_DRAWING_FIELD_INVALID"],
    ["style not object", { ...trend, style: "red" }, "CHART_DRAWING_FIELD_INVALID"],
    ["style unknown key", { ...trend, style: { colour: "red" } }, "CHART_DRAWING_FIELD_UNKNOWN"],
    ["style empty color", { ...trend, style: { color: "" } }, "CHART_DRAWING_FIELD_INVALID"],
    ["style zero lineWidth", { ...trend, style: { lineWidth: 0 } }, "CHART_DRAWING_FIELD_INVALID"],
  ])("rejects %s", (_label, drawing, code) => {
    expectDrawingError(() => fromDrawingsDocument(docWith(drawing)), code as never);
  });

  it("points to the offending field and drawing in the message", () => {
    const err = expectDrawingError(
      () => fromDrawingsDocument(docWith({ ...trend, points: [{ time: 1, price: "2" }, trend.points[1]] })),
      "CHART_DRAWING_FIELD_INVALID",
      "t",
    );
    expect(err.message).toContain("points[0].price");
  });
});
