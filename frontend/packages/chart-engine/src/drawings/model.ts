/**
 * CH-4 — immutable drawing model and chart-space coordinate system.
 *
 * Every drawing is anchored in *chart space* (time, price), never in pixels:
 * a drawing survives zoom/pan/resize untouched and the CH-1 wrapper projects
 * it through `TimeScale`/`PriceScale` at render time via `toPixel`.
 * All model values are deep-readonly; tools.ts returns new objects.
 *
 * No renderer dependency: this module is pure TypeScript and is consumed by
 * the CH-1 wrapper, tools.ts and serialize.ts.
 */

export type DrawingKind =
  | "trendline"
  | "horizontal-line"
  | "vertical-line"
  | "rectangle"
  | "fibonacci";

export const DRAWING_KINDS: readonly DrawingKind[] = [
  "trendline",
  "horizontal-line",
  "vertical-line",
  "rectangle",
  "fibonacci",
];

const KIND_SET: ReadonlySet<string> = new Set<string>(DRAWING_KINDS);

/** Default Fibonacci retracement levels (ratios of the p1→p2 price span). */
export const DEFAULT_FIBONACCI_LEVELS: readonly number[] = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

/** Chart-space anchor. `time` uses the same unit as `CandlePoint.time`. */
export interface DrawingPoint {
  readonly time: number;
  readonly price: number;
}

/** Pixel-space point produced by `toPixel`. */
export interface PixelPoint {
  readonly x: number;
  readonly y: number;
}

/** Chart-space translation applied by `moveDrawing`. */
export interface DrawingDelta {
  readonly time: number;
  readonly price: number;
}

export interface DrawingStyle {
  readonly color?: string;
  readonly lineWidth?: number;
}

interface DrawingBase<K extends DrawingKind> {
  readonly id: string;
  readonly kind: K;
  readonly locked?: boolean;
  readonly style?: DrawingStyle;
}

/** Two anchors; the line extends through both. */
export interface TrendLineDrawing extends DrawingBase<"trendline"> {
  readonly points: readonly [DrawingPoint, DrawingPoint];
}

export interface HorizontalLineDrawing extends DrawingBase<"horizontal-line"> {
  readonly price: number;
}

export interface VerticalLineDrawing extends DrawingBase<"vertical-line"> {
  readonly time: number;
}

/** Two opposite corners in any order; see `rectangleBounds` in tools.ts. */
export interface RectangleDrawing extends DrawingBase<"rectangle"> {
  readonly points: readonly [DrawingPoint, DrawingPoint];
}

/** p1 = 0% level, p2 = 100% level; `levels` are ratios (may exceed [0, 1]). */
export interface FibonacciDrawing extends DrawingBase<"fibonacci"> {
  readonly points: readonly [DrawingPoint, DrawingPoint];
  readonly levels: readonly number[];
}

export type Drawing =
  | TrendLineDrawing
  | HorizontalLineDrawing
  | VerticalLineDrawing
  | RectangleDrawing
  | FibonacciDrawing;

/** Drawings that carry a two-anchor `points` tuple. */
export type TwoPointDrawing = TrendLineDrawing | RectangleDrawing | FibonacciDrawing;

/** Ordered, immutable collection. Ids are unique within a collection. */
export type DrawingCollection = readonly Drawing[];

/**
 * Chart-space ⇄ pixel projection. Structurally satisfied by the CH-1
 * `TimeScale` + `PriceScale` pair, so the wrapper passes them straight in.
 */
export interface DrawingCoordinateSystem {
  readonly timeToX: (time: number) => number;
  readonly xToTime: (x: number) => number;
  readonly priceToY: (price: number) => number;
  readonly yToPrice: (y: number) => number;
}

export function toPixel(point: DrawingPoint, cs: DrawingCoordinateSystem): PixelPoint {
  return { x: cs.timeToX(point.time), y: cs.priceToY(point.price) };
}

export function fromPixel(pixel: PixelPoint, cs: DrawingCoordinateSystem): DrawingPoint {
  return { time: cs.xToTime(pixel.x), price: cs.yToPrice(pixel.y) };
}

export type DrawingErrorCode =
  | "CHART_DRAWING_INVALID"
  | "CHART_DRAWING_DUPLICATE"
  | "CHART_DRAWING_UNKNOWN"
  | "CHART_DRAWING_ANCHOR_OUT_OF_RANGE"
  | "CHART_DRAWING_SCHEMA_UNSUPPORTED"
  | "CHART_DRAWING_FIELD_MISSING"
  | "CHART_DRAWING_FIELD_UNKNOWN"
  | "CHART_DRAWING_FIELD_INVALID";

export class DrawingError extends Error {
  readonly code: DrawingErrorCode;
  readonly drawingId: string;

  constructor(code: DrawingErrorCode, drawingId: string, detail: string) {
    super(`${code}: ${detail} (drawing "${drawingId}")`);
    this.name = "DrawingError";
    this.code = code;
    this.drawingId = drawingId;
  }
}

export function isDrawingKind(value: unknown): value is DrawingKind {
  return typeof value === "string" && KIND_SET.has(value);
}

export function hasPoints(drawing: Drawing): drawing is TwoPointDrawing {
  return drawing.kind === "trendline" || drawing.kind === "rectangle" || drawing.kind === "fibonacci";
}

function invalid(id: string, detail: string): DrawingError {
  return new DrawingError("CHART_DRAWING_INVALID", id, detail);
}

function assertFinite(id: string, field: string, value: unknown): asserts value is number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw invalid(id, `${field} must be a finite number`);
  }
}

export function assertValidPoint(id: string, field: string, point: DrawingPoint): void {
  if (typeof point !== "object" || point === null) throw invalid(id, `${field} must be a point`);
  assertFinite(id, `${field}.time`, point.time);
  assertFinite(id, `${field}.price`, point.price);
}

function assertValidStyle(id: string, style: DrawingStyle): void {
  if (typeof style !== "object" || style === null) throw invalid(id, "style must be an object");
  if (style.color !== undefined && (typeof style.color !== "string" || style.color.length === 0)) {
    throw invalid(id, "style.color must be a non-empty string");
  }
  if (style.lineWidth !== undefined) {
    assertFinite(id, "style.lineWidth", style.lineWidth);
    if (style.lineWidth <= 0) throw invalid(id, "style.lineWidth must be > 0");
  }
}

function assertValidPoints(id: string, points: readonly DrawingPoint[]): void {
  if (!Array.isArray(points) || points.length !== 2) {
    throw invalid(id, "points must contain exactly two anchors");
  }
  assertValidPoint(id, "points[0]", points[0]);
  assertValidPoint(id, "points[1]", points[1]);
}

/**
 * Structural + numeric validation. Throws `DrawingError`
 * (`CHART_DRAWING_INVALID`) instead of coercing or dropping anything.
 */
export function assertValidDrawing(drawing: Drawing): void {
  const id = typeof drawing.id === "string" ? drawing.id : String(drawing.id);
  if (typeof drawing.id !== "string" || drawing.id.length === 0) {
    throw invalid(id, "id must be a non-empty string");
  }
  if (!isDrawingKind(drawing.kind)) throw invalid(id, `unknown kind "${String(drawing.kind)}"`);
  if (drawing.locked !== undefined && typeof drawing.locked !== "boolean") {
    throw invalid(id, "locked must be a boolean");
  }
  if (drawing.style !== undefined) assertValidStyle(id, drawing.style);

  switch (drawing.kind) {
    case "trendline":
    case "rectangle":
      assertValidPoints(id, drawing.points);
      return;
    case "horizontal-line":
      assertFinite(id, "price", drawing.price);
      return;
    case "vertical-line":
      assertFinite(id, "time", drawing.time);
      return;
    case "fibonacci": {
      assertValidPoints(id, drawing.points);
      if (!Array.isArray(drawing.levels) || drawing.levels.length === 0) {
        throw invalid(id, "levels must be a non-empty array");
      }
      drawing.levels.forEach((level, i) => assertFinite(id, `levels[${i}]`, level));
      return;
    }
  }
}
