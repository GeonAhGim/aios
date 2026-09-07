// CH-16d screen wiring for chart-engine's `legend/dataWindow.ts`
// (`computeDataWindowRows`) — the pure "all indicators, one bar" projection
// that CH-16's DoD ("지표 30종 값 동시 표시") needs beyond the vendor's own
// single-indicator on-chart tooltip. Only `dataWindow.ts`'s vendor-free
// exports are imported here; `dataWindowStyle.ts` (vendor-typed) must never
// be reached from apps/web — see both files' docstrings and
// ChartPanes.tsx's header comment.
import { useMemo } from "react";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import {
  DataWindowError,
  computeDataWindowRows,
  type DataWindowErrorCode,
  type DataWindowRow,
  type IndicatorSeriesSnapshot,
} from "@aios/chart-engine/src/legend/dataWindow";
import { Alert } from "@aios/ui-web";
import type { OverlaySeriesByOutput } from "./ChartPlotLayer";

const DATA_WINDOW_ERROR_REASONS: Record<DataWindowErrorCode, string> = {
  CHART_DATA_WINDOW_DUPLICATE_INDICATOR: "같은 지표 id가 중복되어 데이터 윈도우를 표시할 수 없습니다.",
};

/**
 * Turns registry overlays + their computed series into `IndicatorSeriesSnapshot[]`,
 * indexed identically to `candles` (`result[i]` <-> `candles[i]`) so
 * `computeDataWindowRows` can resolve any candle's row by index alone.
 */
export function buildIndicatorSnapshots(
  overlays: readonly OverlayEntry[],
  overlaySeries: ReadonlyMap<string, OverlaySeriesByOutput>,
  candles: readonly StreamCandle[],
): readonly IndicatorSeriesSnapshot[] {
  return overlays.map((overlay) => {
    const seriesByOutput = overlaySeries.get(overlay.id);
    const valueByTime = new Map<string, Map<number, number>>(
      overlay.outputs.map((output) => {
        const points = seriesByOutput?.get(output.name) ?? [];
        return [output.name, new Map(points.map((p) => [p.time, p.value]))] as const;
      }),
    );
    const figures = overlay.outputs.map((output) => ({ key: output.name, title: `${overlay.id}.${output.name}` }));
    const result = candles.map((candle) => {
      const row: Record<string, number | undefined> = {};
      let hasAny = false;
      for (const output of overlay.outputs) {
        const value = valueByTime.get(output.name)?.get(candle.openTimeMs);
        if (value !== undefined) {
          row[output.name] = value;
          hasAny = true;
        }
      }
      return hasAny ? row : undefined;
    });
    return { id: overlay.id, figures, result };
  });
}

/** Nearest candle to the crosshair's (interpolated) time; the last bar once the crosshair is hidden. */
export function resolveDataIndex(candles: readonly StreamCandle[], crosshairTimeMs: number | null): number {
  if (candles.length === 0) return -1;
  if (crosshairTimeMs === null) return candles.length - 1;
  let closestIndex = 0;
  let closestDelta = Infinity;
  candles.forEach((candle, i) => {
    const delta = Math.abs(candle.openTimeMs - crosshairTimeMs);
    if (delta < closestDelta) {
      closestDelta = delta;
      closestIndex = i;
    }
  });
  return closestIndex;
}

export interface DataWindowPanelProps {
  readonly overlays: readonly OverlayEntry[];
  readonly overlaySeries: ReadonlyMap<string, OverlaySeriesByOutput>;
  readonly candles: readonly StreamCandle[];
  readonly crosshairTimeMs: number | null;
}

export function DataWindowPanel({ overlays, overlaySeries, candles, crosshairTimeMs }: DataWindowPanelProps) {
  const snapshots = useMemo(() => buildIndicatorSnapshots(overlays, overlaySeries, candles), [overlays, overlaySeries, candles]);
  const dataIndex = useMemo(() => resolveDataIndex(candles, crosshairTimeMs), [candles, crosshairTimeMs]);

  let rows: readonly DataWindowRow[] = [];
  let errorCode: DataWindowErrorCode | null = null;
  try {
    rows = computeDataWindowRows(snapshots, dataIndex);
  } catch (caught) {
    if (!(caught instanceof DataWindowError)) throw caught;
    errorCode = caught.code;
  }

  return (
    <section aria-label="데이터 윈도우" data-testid="data-window-panel" className="rounded-lg border border-border p-3 text-xs">
      {errorCode ? (
        <div data-testid="data-window-error">
          <Alert tone="warning">
            <p>{DATA_WINDOW_ERROR_REASONS[errorCode]}</p>
          </Alert>
        </div>
      ) : (
        <table className="w-full">
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.indicatorId}:${row.outputKey}`} data-testid="data-window-row">
                <td style={{ color: row.color }}>{row.label}</td>
                <td className="text-right">{row.value}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td className="text-fg-muted">표시할 지표가 없습니다.</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </section>
  );
}
