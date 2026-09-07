// CH-16e screen wiring for chart-engine's `legend/statusLine.ts`
// (`buildStatusLineLegends`) — the pure OHLCV-row builder designed to be
// handed to the vendor's `candle.tooltip.legend.template` callback (§9.11
// CH-16 table; see `statusLineStyle.ts`'s docstring for why that vendor-typed
// binding itself can never be imported here). Only `statusLine.ts`'s
// vendor-free exports are used — same type-boundary reuse task-2044 already
// established for `legend/dataWindow.ts` (`DataWindowPanel.tsx`), whose
// `resolveDataIndex` this file reuses as-is rather than re-deriving the
// crosshair-to-bar lookup a second time.
import { useMemo } from "react";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import {
  buildStatusLineLegends,
  type StatusLineCandle,
  type StatusLineLegend,
  type StatusLineNeighbor,
} from "@aios/chart-engine/src/legend/statusLine";
import { resolveDataIndex } from "./DataWindowPanel";

function toStatusLineCandle(c: StreamCandle): StatusLineCandle {
  return {
    timestamp: c.openTimeMs,
    open: Number(c.record.open),
    high: Number(c.record.high),
    low: Number(c.record.low),
    close: Number(c.record.close),
    volume: c.record.volume === null ? undefined : Number(c.record.volume),
  };
}

/** `null` for both "no candles yet" and "dataIndex outside the series" — matches the vendor `NeighborData` contract `buildStatusLineLegends` binds onto. */
export function resolveStatusLineNeighbor(
  candles: readonly StreamCandle[],
  dataIndex: number,
): StatusLineNeighbor<StatusLineCandle | null> {
  const at = (i: number): StatusLineCandle | null => (i >= 0 && i < candles.length ? toStatusLineCandle(candles[i]!) : null);
  return { prev: at(dataIndex - 1), current: at(dataIndex), next: at(dataIndex + 1) };
}

function legendValueText(legend: StatusLineLegend): { text: string; color?: string } {
  return typeof legend.value === "string" ? { text: legend.value } : legend.value;
}

export interface StatusLineProps {
  readonly candles: readonly StreamCandle[];
  readonly crosshairTimeMs: number | null;
}

/** Persistent OHLCV row (§9.11 "showRule: always") — a DOM status line, not a hover tooltip, so every field stays visible regardless of crosshair state. */
export function StatusLine({ candles, crosshairTimeMs }: StatusLineProps) {
  const dataIndex = useMemo(() => resolveDataIndex(candles, crosshairTimeMs), [candles, crosshairTimeMs]);
  const legends = useMemo(() => buildStatusLineLegends(resolveStatusLineNeighbor(candles, dataIndex)), [candles, dataIndex]);

  return (
    <section aria-label="OHLCV 상태줄" data-testid="chart-status-line" className="flex flex-wrap gap-3 rounded-lg border border-border px-3 py-1 text-xs">
      {legends.map((legend) => {
        const { text, color } = legendValueText(legend);
        return (
          <span key={legend.title} data-testid={`chart-status-line-${legend.title}`}>
            <span className="text-fg-muted">{legend.title}</span> <span style={{ color }}>{text}</span>
          </span>
        );
      })}
    </section>
  );
}
