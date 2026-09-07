/**
 * CH-15 — PlotSpec-driven renderer dispatch: turns one indicator output's
 * `PlotSpec` (backend `src/core/indicators/spec.py`, IND-15) plus its value
 * series into draw calls on a canvas-agnostic `PlotRenderTarget`.
 *
 * `PlotSpec` here is a straight field-for-field mirror of the backend
 * dataclass (snake_case kept verbatim, same convention as
 * `@aios/shared-types` `positionView.ts`) — this module never redefines the
 * contract, only parses/validates it (decision, task-1732). The 6 `kind`
 * values and 5 `scale` values are the full backend `PlotKind`/`ScaleHint`
 * literal unions; `scaleBinding.ts` imports `ScaleHint` from here rather than
 * re-declaring it.
 *
 * DoD: adding a new indicator whose `PlotSpec.kind` is one of the existing 6
 * needs zero changes to this file or to screen code — `renderPlot` dispatches
 * purely off `spec.kind`, never off indicator identity.
 */

import type { OverlayOutput, OverlayPlacement } from "../indicators/overlayRegistry";
import type { IndicatorStyleOutput } from "../plugins/indicatorPlugin";
import { type FillSegment, computeFillSegments } from "./fillBetween";
import type { PlotProjection } from "./scaleBinding";

export type PlotKind = "line" | "histogram" | "area" | "band" | "cloud" | "marker";
export type ScaleHint = "own" | "overlay" | "percent" | "log" | "inverted";
export type DefaultPane = "price" | "separate";

const PLOT_KINDS: ReadonlySet<string> = new Set<PlotKind>(["line", "histogram", "area", "band", "cloud", "marker"]);
const SCALE_HINTS: ReadonlySet<string> = new Set<ScaleHint>(["own", "overlay", "percent", "log", "inverted"]);
const DEFAULT_PANES: ReadonlySet<string> = new Set<DefaultPane>(["price", "separate"]);
/** Only value backend `specs_talib.py`/`generate_specs.py` actually emit today — the frontend never invents a new rule. */
const COLOR_RULES: ReadonlySet<string> = new Set<string>(["sign"]);

/** Mirrors backend `PlotSpec` (`src/core/indicators/spec.py`) field-for-field. */
export interface PlotSpec {
  readonly kind: PlotKind;
  readonly scale: ScaleHint;
  readonly default_pane: DefaultPane;
  readonly fill_between: string | null;
  readonly color_rule: string | null;
  readonly precision: number | null;
  readonly legend_format: string | null;
}

export type PlotRenderErrorCode =
  | "PLOT_RENDER_UNKNOWN_KIND"
  | "PLOT_RENDER_UNKNOWN_SCALE"
  | "PLOT_RENDER_INVALID_SPEC"
  | "PLOT_RENDER_SERIES_MISSING"
  | "PLOT_RENDER_FILL_TARGET_MISSING";

export class PlotRenderError extends Error {
  readonly code: PlotRenderErrorCode;

  constructor(code: PlotRenderErrorCode, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "PlotRenderError";
    this.code = code;
  }
}

const PLOT_SPEC_FIELDS: readonly string[] = [
  "kind",
  "scale",
  "default_pane",
  "fill_between",
  "color_rule",
  "precision",
  "legend_format",
];

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function decodeNullableString(value: unknown, field: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") throw new PlotRenderError("PLOT_RENDER_INVALID_SPEC", `${field} must be a string or null`);
  return value;
}

/** Fail-closed decode of a raw (already-JSON-parsed) `PlotSpec`. Never silently drops unknown fields. */
export function decodePlotSpec(raw: unknown): PlotSpec {
  if (!isPlainObject(raw)) throw new PlotRenderError("PLOT_RENDER_INVALID_SPEC", "PlotSpec must be an object");
  for (const key of Object.keys(raw)) {
    if (!PLOT_SPEC_FIELDS.includes(key)) {
      throw new PlotRenderError("PLOT_RENDER_INVALID_SPEC", `unknown field "${key}"`);
    }
  }
  const kind = raw.kind;
  if (typeof kind !== "string" || !PLOT_KINDS.has(kind)) {
    throw new PlotRenderError("PLOT_RENDER_UNKNOWN_KIND", `unknown PlotSpec.kind: ${JSON.stringify(kind)}`);
  }
  const scale = raw.scale;
  if (typeof scale !== "string" || !SCALE_HINTS.has(scale)) {
    throw new PlotRenderError("PLOT_RENDER_UNKNOWN_SCALE", `unknown PlotSpec.scale: ${JSON.stringify(scale)}`);
  }
  const defaultPane = raw.default_pane;
  if (typeof defaultPane !== "string" || !DEFAULT_PANES.has(defaultPane)) {
    throw new PlotRenderError("PLOT_RENDER_INVALID_SPEC", `unknown PlotSpec.default_pane: ${JSON.stringify(defaultPane)}`);
  }
  const precisionRaw = raw.precision;
  if (precisionRaw !== null && precisionRaw !== undefined && (typeof precisionRaw !== "number" || precisionRaw < 0)) {
    throw new PlotRenderError("PLOT_RENDER_INVALID_SPEC", "precision must be a non-negative number or null");
  }
  const colorRule = decodeNullableString(raw.color_rule, "color_rule");
  if (colorRule !== null && !COLOR_RULES.has(colorRule)) {
    throw new PlotRenderError("PLOT_RENDER_INVALID_SPEC", `unknown PlotSpec.color_rule: ${JSON.stringify(colorRule)}`);
  }
  return {
    kind: kind as PlotKind,
    scale: scale as ScaleHint,
    default_pane: defaultPane as DefaultPane,
    fill_between: decodeNullableString(raw.fill_between, "fill_between"),
    color_rule: colorRule,
    precision: precisionRaw === undefined ? null : (precisionRaw as number | null),
    legend_format: decodeNullableString(raw.legend_format, "legend_format"),
  };
}

/**
 * Derives a default `PlotSpec` for one `OverlayEntry` output when the
 * backend catalog (IND-12, `IndicatorListItemView`) does not carry a `plots`
 * field yet — mirrors backend `specs_talib.py`'s output_flags-derived
 * defaults (ADR-2026-09-06-F D1: plain output -> line, histogram output ->
 * histogram, an upperband/lowerband pair -> band + fill_between) so
 * screen/registry code never special-cases an indicator by name. A new
 * `DEFAULT_OVERLAY_DEFINITIONS` entry (indicators/overlayRegistry.ts) picks
 * up a correct `PlotSpec` from this function with zero changes anywhere
 * else — this is the DoD-required "add an indicator, screen code doesn't
 * change" path for the still-server-value-less CORE tier.
 */
export function deriveOverlayPlotSpec(placement: OverlayPlacement, outputs: readonly OverlayOutput[], output: OverlayOutput): PlotSpec {
  const scale: ScaleHint = placement === "main-overlay" ? "overlay" : "own";
  const default_pane: DefaultPane = placement === "main-overlay" ? "price" : "separate";
  const base = { scale, default_pane, color_rule: null, precision: null, legend_format: null };

  if (output.series === "histogram") {
    return { ...base, kind: "histogram", fill_between: null };
  }
  if (output.name === "upperband" && outputs.some((o) => o.name === "lowerband")) {
    return { ...base, kind: "band", fill_between: "lowerband" };
  }
  if (output.name === "lowerband" && outputs.some((o) => o.name === "upperband")) {
    return { ...base, kind: "band", fill_between: null };
  }
  return { ...base, kind: "line", fill_between: null };
}

export interface PlotSeriesPoint {
  readonly time: number;
  readonly value: number;
}

export interface Point2D {
  readonly x: number;
  readonly y: number;
}

export interface HistogramBar {
  readonly x: number;
  readonly y: number;
  readonly baselineY: number;
}

/** Canvas-agnostic draw surface — same delegation-seam shape as `RendererBackend`. */
export interface PlotRenderTarget {
  drawLine(points: readonly Point2D[], style: IndicatorStyleOutput): void;
  drawHistogram(bars: readonly HistogramBar[], style: IndicatorStyleOutput): void;
  drawArea(points: readonly Point2D[], baselineY: number, style: IndicatorStyleOutput): void;
  drawPolygon(points: readonly Point2D[], style: IndicatorStyleOutput): void;
  drawMarker(points: readonly Point2D[], style: IndicatorStyleOutput): void;
}

function project(points: readonly PlotSeriesPoint[], projection: PlotProjection): Point2D[] {
  return points.map((p) => ({ x: projection.timeToX(p.time), y: projection.priceToY(p.value) }));
}

function projectSegment(segment: FillSegment, projection: PlotProjection): Point2D[] {
  const upper = segment.points.map((p) => ({ x: projection.timeToX(p.time), y: projection.priceToY(p.base) }));
  const lower = segment.points
    .slice()
    .reverse()
    .map((p) => ({ x: projection.timeToX(p.time), y: projection.priceToY(p.target) }));
  return [...upper, ...lower];
}

function drawFill(
  spec: PlotSpec,
  output: string,
  points: readonly PlotSeriesPoint[],
  seriesByOutput: ReadonlyMap<string, readonly PlotSeriesPoint[]>,
  projection: PlotProjection,
  style: IndicatorStyleOutput,
  target: PlotRenderTarget,
): void {
  if (spec.fill_between === null) return;
  const targetSeries = seriesByOutput.get(spec.fill_between);
  if (targetSeries === undefined) {
    throw new PlotRenderError(
      "PLOT_RENDER_FILL_TARGET_MISSING",
      `output "${output}" fill_between references "${spec.fill_between}", which is not in seriesByOutput`,
    );
  }
  const segments = computeFillSegments(points, targetSeries);
  for (const segment of segments) {
    // An "equal" stretch is a zero-width crossing sliver, not a real fill region — skip it rather
    // than drawing a degenerate polygon (fixed by test, ADR-2026-09-07-A).
    if (segment.direction === "equal") continue;
    const color = segment.direction === "above" ? (style.aboveColor ?? style.color) : (style.belowColor ?? style.color);
    target.drawPolygon(projectSegment(segment, projection), { ...style, color });
  }
}

/**
 * Dispatches one output's `PlotSpec` to the matching draw calls. `output` is
 * the backend `IndicatorSpec.outputs` entry this spec belongs to (1:1 with
 * `spec`, per `IndicatorSpec.plots`); `seriesByOutput` carries every output's
 * computed values so `fill_between` partners can be looked up by name.
 * Skips drawing entirely (no-op) when `style.visible` is false.
 */
export function renderPlot(
  spec: PlotSpec,
  output: string,
  seriesByOutput: ReadonlyMap<string, readonly PlotSeriesPoint[]>,
  projection: PlotProjection,
  style: IndicatorStyleOutput,
  target: PlotRenderTarget,
): void {
  if (!style.visible) return;
  const points = seriesByOutput.get(output);
  if (points === undefined) {
    throw new PlotRenderError("PLOT_RENDER_SERIES_MISSING", `no series for output "${output}" in seriesByOutput`);
  }
  const pixelPoints = project(points, projection);

  switch (spec.kind) {
    case "line":
      target.drawLine(pixelPoints, style);
      return;
    case "area":
      target.drawArea(pixelPoints, projection.priceToY(0), style);
      return;
    case "histogram": {
      const bars = pixelPoints.map((p) => ({ x: p.x, y: p.y, baselineY: projection.priceToY(0) }));
      if (spec.color_rule === "sign") {
        const upBars: HistogramBar[] = [];
        const downBars: HistogramBar[] = [];
        points.forEach((point, i) => (point.value < 0 ? downBars : upBars).push(bars[i]!));
        if (upBars.length > 0) target.drawHistogram(upBars, { ...style, color: style.upColor ?? style.color });
        if (downBars.length > 0) target.drawHistogram(downBars, { ...style, color: style.downColor ?? style.color });
      } else {
        target.drawHistogram(bars, style);
      }
      return;
    }
    case "marker":
      target.drawMarker(pixelPoints, style);
      return;
    case "band":
    case "cloud":
      target.drawLine(pixelPoints, style);
      drawFill(spec, output, points, seriesByOutput, projection, style, target);
      return;
    default: {
      const exhaustive: never = spec.kind;
      throw new PlotRenderError("PLOT_RENDER_UNKNOWN_KIND", `unhandled PlotSpec.kind: ${String(exhaustive)}`);
    }
  }
}
