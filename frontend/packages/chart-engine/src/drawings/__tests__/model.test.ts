import { describe, expect, it } from "vitest";
import { createPriceScale } from "../../core/priceScale";
import { createTimeScale } from "../../core/timeScale";
import {
  DRAWING_KINDS,
  assertValidDrawing,
  fromPixel,
  hasPoints,
  isDrawingKind,
  toPixel,
  type Drawing,
  type DrawingCoordinateSystem,
} from "../model";
import { expectDrawingError } from "./helpers";

describe("drawing kinds", () => {
  it("exposes exactly the five CH-4 tools", () => {
    expect(DRAWING_KINDS).toEqual(["trendline", "horizontal-line", "vertical-line", "rectangle", "fibonacci"]);
    for (const kind of DRAWING_KINDS) expect(isDrawingKind(kind)).toBe(true);
    expect(isDrawingKind("ellipse")).toBe(false);
    expect(isDrawingKind(undefined)).toBe(false);
  });

  it("hasPoints narrows two-anchor drawings only", () => {
    const line: Drawing = { id: "h", kind: "horizontal-line", price: 1 };
    const trend: Drawing = { id: "t", kind: "trendline", points: [{ time: 0, price: 0 }, { time: 1, price: 1 }] };
    expect(hasPoints(line)).toBe(false);
    expect(hasPoints(trend)).toBe(true);
  });
});

describe("coordinate system", () => {
  it("projects chart-space anchors through the CH-1 scales and back", () => {
    const timeScale = createTimeScale({ range: { from: 0, to: 1000 }, width: 200 });
    const priceScale = createPriceScale({ range: { min: 100, max: 200 }, height: 100 });
    const cs: DrawingCoordinateSystem = { ...timeScale, ...priceScale };

    const pixel = toPixel({ time: 500, price: 150 }, cs);
    expect(pixel).toEqual({ x: 100, y: priceScale.priceToY(150) });
    expect(fromPixel(pixel, cs)).toEqual({ time: 500, price: 150 });
  });

  it("is a structural contract: any timeToX/priceToY pair works", () => {
    const cs: DrawingCoordinateSystem = {
      timeToX: (t) => t * 2,
      xToTime: (x) => x / 2,
      priceToY: (p) => -p,
      yToPrice: (y) => -y,
    };
    expect(toPixel({ time: 3, price: 4 }, cs)).toEqual({ x: 6, y: -4 });
    expect(fromPixel({ x: 6, y: -4 }, cs)).toEqual({ time: 3, price: 4 });
  });
});

describe("assertValidDrawing", () => {
  const good: Drawing = {
    id: "ok",
    kind: "fibonacci",
    points: [{ time: 1, price: 2 }, { time: 3, price: 4 }],
    levels: [0, 0.5, 1],
    locked: false,
    style: { color: "#fff", lineWidth: 1 },
  };

  it("accepts a well-formed drawing", () => {
    expect(() => assertValidDrawing(good)).not.toThrow();
  });

  it.each<[string, unknown]>([
    ["empty id", { ...good, id: "" }],
    ["non-string id", { ...good, id: 7 }],
    ["unknown kind", { ...good, kind: "ellipse" }],
    ["non-boolean locked", { ...good, locked: "yes" }],
    ["NaN time", { ...good, points: [{ time: Number.NaN, price: 2 }, good.points[1]] }],
    ["Infinity price", { ...good, points: [good.points[0], { time: 3, price: Number.POSITIVE_INFINITY }] }],
    ["one point only", { ...good, points: [good.points[0]] }],
    ["three points", { ...good, points: [...good.points, good.points[0]] }],
    ["empty levels", { ...good, levels: [] }],
    ["string level", { ...good, levels: ["0.5"] }],
    ["empty style.color", { ...good, style: { color: "" } }],
    ["zero lineWidth", { ...good, style: { lineWidth: 0 } }],
    ["horizontal without price", { id: "h", kind: "horizontal-line" }],
    ["vertical with NaN time", { id: "v", kind: "vertical-line", time: Number.NaN }],
  ])("rejects %s with CHART_DRAWING_INVALID", (_label, drawing) => {
    expectDrawingError(() => assertValidDrawing(drawing as Drawing), "CHART_DRAWING_INVALID");
  });

  it("names the offending drawing in the error", () => {
    const err = expectDrawingError(
      () => assertValidDrawing({ id: "bad", kind: "vertical-line", time: Number.NaN }),
      "CHART_DRAWING_INVALID",
      "bad",
    );
    expect(err.message).toContain('drawing "bad"');
    expect(err.message).toContain("time must be a finite number");
  });
});
