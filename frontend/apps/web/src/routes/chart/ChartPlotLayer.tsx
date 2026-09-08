// CH-15b — the one place this screen turns a pane's `OverlayEntry` list into
// actual indicator draw calls, by dispatching through CH-15
// `render/plotRenderers.ts`/`scaleBinding.ts`/`fillBetween.ts` (task-1732)
// instead of ChartPanes.tsx hand-drawing shapes. `renderPlot`'s
// `PlotRenderTarget` is canvas-agnostic by design (its own docstring) — vendor
// klinecharts owns the *candle* pane's real drawing (CandlestickChart.tsx,
// lightweight-charts) and mounting a second full chart engine per sub-pane
// just to reuse its figure API is out of scope here (ChartPage.tsx/
// ChartLegend.tsx/ChartToolbar.tsx already document that importing anything
// resolving "../core/klinecharts" breaks apps/web's tsc -b — task-1940 QA
// reproduced 79 vendor type errors doing exactly that). This module is the
// one small supplement vendor doesn't provide inline here: a plain SVG
// surface that only turns already-computed pixel coordinates from
// `renderPlot` into shapes (ADR-2026-09-06-F D3's "벤더가 못 하는 부분만
// 보강" carve-out) — it never computes scale, layout, or indicator values
// itself.
//
// `overlaySeries`/`overlayPlotSpecs` are server-value hooks, same pattern as
// ChartPanes.tsx's `restoredHeightRatios`: no live indicator-value
// computation pipeline exists yet (IND-12 `IndicatorListItemView` has no
// `plots` field — src/api/schemas/indicators.py), so both default to empty
// maps and this layer draws nothing extra, matching today's screen exactly.
// Once a future leaf feeds real series in, this dispatch path runs unchanged
// — that is the DoD's "adding an indicator needs zero screen-code changes"
// property, proven by __tests__/ChartPlotLayer.test.tsx registering two
// brand-new PlotSpec kinds against this same function.
//
// CH-15c (task-2127) — `render/fillBetween.ts`'s `FillBetweenError` is caught
// here directly (deep import below), not just left as an internal
// `plotRenderers.ts` detail: without this catch, a `fill_between` PlotSpec
// whose partner series has a different length/timestamps than declared would
// throw past this function's try/catch and blank the whole plot layer,
// instead of surfacing one `PlotLayerIssue` for the offending output the way
// every other malformed-spec case already does.
import type { ReactNode } from "react";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorStyleOutput } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import type { PriceRange, PriceScale } from "@aios/chart-engine/src/core/priceScale";
import type { TimeRange, TimeScale } from "@aios/chart-engine/src/core/timeScale";
import {
  PlotRenderError,
  type HistogramBar,
  type Point2D,
  type PlotRenderErrorCode,
  type PlotRenderTarget,
  type PlotSeriesPoint,
  decodePlotSpec,
  deriveOverlayPlotSpec,
  renderPlot,
} from "@aios/chart-engine/src/render/plotRenderers";
import { ScaleBindingError, bindScale, type ScaleBindingErrorCode } from "@aios/chart-engine/src/render/scaleBinding";
import { FillBetweenError, type FillBetweenErrorCode } from "@aios/chart-engine/src/render/fillBetween";

/** No server `plots` field exists yet (IND-12) — a raw, undecoded override lets a caller (or a test) supply one anyway; `decodePlotSpec` still fail-closes on it. */
export type OverlaySeriesByOutput = ReadonlyMap<string, readonly PlotSeriesPoint[]>;
export type OverlayPlotSpecOverrides = ReadonlyMap<string, unknown>;

/**
 * No raw `.message` here on purpose — this repo's error-surfacing convention
 * (task-1048 `errorSurface.guard.test.ts`) is: never render a caught error's
 * `.message` text directly, only its machine-readable `code`, mapped through
 * `PLOT_ERROR_REASONS` below (same pattern as ChartPanes.tsx's own
 * `PANE_ERROR_REASONS`).
 */
export interface PlotLayerIssue {
  readonly overlayId: string;
  readonly output: string;
  readonly code: PlotRenderErrorCode | ScaleBindingErrorCode | FillBetweenErrorCode;
}

export const PLOT_ERROR_REASONS: Record<PlotLayerIssue["code"], string> = {
  PLOT_RENDER_UNKNOWN_KIND: "알 수 없는 지표 표시 종류(kind)입니다.",
  PLOT_RENDER_UNKNOWN_SCALE: "알 수 없는 스케일(scale)입니다.",
  PLOT_RENDER_INVALID_SPEC: "지표 표시 계약(PlotSpec)이 올바르지 않습니다.",
  PLOT_RENDER_SERIES_MISSING: "지표 시리즈 데이터가 없습니다.",
  PLOT_RENDER_FILL_TARGET_MISSING: "채움 대상 시리즈를 찾을 수 없습니다.",
  SCALE_BINDING_UNKNOWN_SCALE: "알 수 없는 스케일(scale)입니다.",
  SCALE_BINDING_NON_POSITIVE_FOR_LOG: "로그 스케일에는 0 이하 값을 표시할 수 없습니다.",
  FILL_BETWEEN_LENGTH_MISMATCH: "채움(fill_between) 두 시리즈의 길이가 서로 다릅니다.",
  FILL_BETWEEN_TIME_MISMATCH: "채움(fill_between) 두 시리즈의 시간축이 서로 다릅니다.",
};

/** Candle high/low across the whole series — the "overlay" scale's domain (main pane). */
export function priceRangeFromCandles(candles: readonly StreamCandle[]): PriceRange {
  if (candles.length === 0) return { min: 0, max: 1 };
  let min = Infinity;
  let max = -Infinity;
  for (const c of candles) {
    min = Math.min(min, Number(c.record.low));
    max = Math.max(max, Number(c.record.high));
  }
  return max > min ? { min, max } : { min: min - 1, max: min + 1 };
}

/** One sub-pane overlay's own value range across all of its outputs — the "own" scale's domain. */
export function priceRangeFromSeries(seriesByOutput: OverlaySeriesByOutput | undefined): PriceRange {
  if (!seriesByOutput) return { min: 0, max: 1 };
  let min = Infinity;
  let max = -Infinity;
  for (const points of seriesByOutput.values()) {
    for (const p of points) {
      min = Math.min(min, p.value);
      max = Math.max(max, p.value);
    }
  }
  return Number.isFinite(min) && Number.isFinite(max) && max > min ? { min, max } : { min: 0, max: 1 };
}

export function timeRangeFromCandles(candles: readonly StreamCandle[]): TimeRange {
  if (candles.length === 0) return { from: 0, to: 1 };
  const first = candles[0]!.openTimeMs;
  const last = candles[candles.length - 1]!.openTimeMs;
  return last > first ? { from: first, to: last } : { from: first, to: first + 1 };
}

export interface PlotLayerInput {
  readonly overlays: readonly OverlayEntry[];
  /** overlay id -> output name -> computed series. Missing entries render nothing (no data yet, not an error). */
  readonly overlaySeries: ReadonlyMap<string, OverlaySeriesByOutput>;
  /** overlay id -> output name -> raw PlotSpec override. Absent falls back to `deriveOverlayPlotSpec`. */
  readonly overlayPlotSpecs: ReadonlyMap<string, OverlayPlotSpecOverrides>;
  readonly mainScale: PriceScale;
  readonly ownScale: PriceScale;
  readonly timeScale: TimeScale;
}

export interface PlotLayerResult {
  readonly nodes: readonly ReactNode[];
  /** Non-empty means at least one PlotSpec/scale was rejected — the caller must surface these, never drop them (DoD: no silent skip of an unknown kind). */
  readonly issues: readonly PlotLayerIssue[];
}

const PALETTE = ["#eab308", "#38bdf8", "#f472b6", "#a78bfa", "#34d399", "#fb923c"];

function styleFor(output: string, index: number): IndicatorStyleOutput {
  return { output, color: PALETTE[index % PALETTE.length]!, lineWidth: 1.5, visible: true };
}

function toSvgPoints(points: readonly Point2D[]): string {
  return points.map((p) => `${p.x},${p.y}`).join(" ");
}

function createSvgPlotTarget(nodes: ReactNode[], nextKey: () => number): PlotRenderTarget {
  return {
    drawLine(points, style) {
      if (points.length < 2) return;
      nodes.push(
        <polyline key={nextKey()} points={toSvgPoints(points)} fill="none" stroke={style.color} strokeWidth={style.lineWidth} />,
      );
    },
    drawHistogram(bars: readonly HistogramBar[], style) {
      for (const bar of bars) {
        const top = Math.min(bar.y, bar.baselineY);
        const barHeight = Math.max(Math.abs(bar.baselineY - bar.y), 1);
        nodes.push(<rect key={nextKey()} x={bar.x - 2} y={top} width={4} height={barHeight} fill={style.color} />);
      }
    },
    drawArea(points, baselineY, style) {
      if (points.length === 0) return;
      const last = points[points.length - 1]!;
      const first = points[0]!;
      const filled = [...points, { x: last.x, y: baselineY }, { x: first.x, y: baselineY }];
      nodes.push(<polygon key={nextKey()} points={toSvgPoints(filled)} fill={style.color} opacity={0.25} />);
      if (points.length >= 2) {
        nodes.push(<polyline key={nextKey()} points={toSvgPoints(points)} fill="none" stroke={style.color} strokeWidth={style.lineWidth} />);
      }
    },
    drawPolygon(points, style) {
      if (points.length < 3) return;
      nodes.push(<polygon key={nextKey()} points={toSvgPoints(points)} fill={style.color} opacity={0.2} />);
    },
    drawMarker(points, style) {
      for (const p of points) {
        nodes.push(<circle key={nextKey()} cx={p.x} cy={p.y} r={3} fill={style.color} />);
      }
    },
  };
}

/**
 * Pure: no DOM, no state. Dispatches every overlay/output pair that has
 * series data through CH-15's `renderPlot`, collecting SVG nodes and any
 * `PlotRenderError`/`ScaleBindingError` as `issues` instead of throwing past
 * one bad indicator and blanking the whole pane.
 */
export function buildPlotLayer(input: PlotLayerInput): PlotLayerResult {
  const { overlays, overlaySeries, overlayPlotSpecs, mainScale, ownScale, timeScale } = input;
  const nodes: ReactNode[] = [];
  const issues: PlotLayerIssue[] = [];
  let keySeq = 0;
  const target = createSvgPlotTarget(nodes, () => keySeq++);

  for (const overlay of overlays) {
    const seriesByOutput = overlaySeries.get(overlay.id);
    if (!seriesByOutput) continue;
    const rawSpecs = overlayPlotSpecs.get(overlay.id);

    overlay.outputs.forEach((output, index) => {
      const points = seriesByOutput.get(output.name);
      if (!points) return;
      try {
        const raw = rawSpecs?.get(output.name);
        const spec = raw !== undefined ? decodePlotSpec(raw) : deriveOverlayPlotSpec(overlay.placement, overlay.outputs, output);
        const projection = bindScale(spec.scale, { mainScale, ownScale, timeScale });
        renderPlot(spec, output.name, seriesByOutput, projection, styleFor(output.name, index), target);
      } catch (err) {
        if (err instanceof PlotRenderError || err instanceof ScaleBindingError || err instanceof FillBetweenError) {
          issues.push({ overlayId: overlay.id, output: output.name, code: err.code });
        } else {
          throw err;
        }
      }
    });
  }

  return { nodes, issues };
}
