import { describe, expect, it } from "vitest";
import { DrawingError, type Drawing, type DrawingCollection } from "../model";
import {
  DRAWINGS_SCHEMA_VERSION,
  deserializeDrawings,
  fromDrawingsDocument,
  serializeDrawings,
  toDrawingsDocument,
} from "../serialize";
import { createFibonacci, createHorizontalLine, createTrendLine, createVerticalLine } from "../tools";
import { corruptDocument, createRng, genCollection, genDrawing } from "./arbitraries";
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

describe("failure injection: randomized wire-payload corruption", () => {
  it("fail-closed for 200 seeded structural corruptions (never silently accepted)", () => {
    const rng = createRng(0xfa17);
    const exercised = new Set<string>();
    let cases = 0;
    for (let i = 0; i < 200; i++) {
      const collection = genCollection(rng, 8);
      if (collection.length === 0) continue;
      const doc = toDrawingsDocument(collection) as unknown as {
        schema_version: number;
        drawings: readonly Record<string, unknown>[];
      };
      const injected = corruptDocument(rng, doc);
      if (injected === null) continue;
      cases++;
      exercised.add(injected.corruption);
      let caught: unknown;
      try {
        fromDrawingsDocument(injected.doc);
      } catch (error) {
        caught = error;
      }
      expect(caught, `corruption=${injected.corruption} case ${i}`).toBeInstanceOf(DrawingError);
    }
    // Every corruption kind must have fired at least once across the seeded run,
    // and every fired case must have been rejected above — a single silent
    // acceptance anywhere in the loop would already have failed the assertion.
    expect(cases).toBeGreaterThan(100);
    expect(exercised.size).toBe(7);
  });
});

describe("performance: numeric ms budget for large collections", () => {
  it("round-trips 5,000 drawings within a fixed ms budget", () => {
    const rng = createRng(0x9e3779b9);
    const collection: DrawingCollection = Array.from({ length: 5000 }, (_, i) => genDrawing(rng, `perf-${i}`));

    const start = performance.now();
    const text = serializeDrawings(collection);
    const back = deserializeDrawings(text);
    const elapsedMs = performance.now() - start;

    expect(back.length).toBe(5000);
    // Generous fixed budget (not a relative ratchet): a regression that makes
    // encode/decode super-linear (e.g. an O(n^2) duplicate-id scan) would blow
    // well past this on any machine, seeded/noise-free inputs aside.
    expect(elapsedMs).toBeLessThan(1000);
  });
});

describe("gate red reproduction: unknown-field drift guard", () => {
  /**
   * Mimics the pre-hardening decoder shape this module replaced: no
   * `assertKnownFields` call anywhere, so a stray field introduced by a
   * backend rename/typo is silently dropped instead of surfacing. This is a
   * mutant of `decodeDrawing`, not part of the shipped module.
   */
  function legacyDecodeIgnoringUnknownFields(value: unknown): DrawingCollection {
    const doc = value as { drawings?: readonly Record<string, unknown>[] };
    const drawings = Array.isArray(doc.drawings) ? doc.drawings : [];
    return drawings.map((raw) => {
      const kind = raw.kind as string;
      const out: Record<string, unknown> = { id: raw.id, kind };
      switch (kind) {
        case "trendline":
        case "rectangle":
          out.points = raw.points;
          break;
        case "fibonacci":
          out.points = raw.points;
          out.levels = raw.levels;
          break;
        case "horizontal-line":
          out.price = raw.price;
          break;
        case "vertical-line":
          out.time = raw.time;
          break;
      }
      if (raw.locked !== undefined) out.locked = raw.locked;
      if (raw.style !== undefined) out.style = raw.style;
      return out as unknown as Drawing;
    });
  }

  const driftedDocs: readonly unknown[] = [
    docWith({ id: "h", kind: "horizontal-line", price: 1, unexpected_backend_field: "drift" }),
    docWith({
      id: "t",
      kind: "trendline",
      points: [{ time: 1, price: 2 }, { time: 3, price: 4 }],
      renamed_field: true,
    }),
  ];

  it("red: a pre-hardening decoder silently drops the drifted field instead of failing", () => {
    for (const doc of driftedDocs) {
      expect(() => legacyDecodeIgnoringUnknownFields(doc)).not.toThrow();
    }
  });

  it("green: the shipped decoder rejects the same drift instead of silently dropping it", () => {
    for (const doc of driftedDocs) {
      expectDrawingError(() => fromDrawingsDocument(doc), "CHART_DRAWING_FIELD_UNKNOWN");
    }
  });
});
