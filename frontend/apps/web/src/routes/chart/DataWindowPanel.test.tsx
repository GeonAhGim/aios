import "@testing-library/jest-dom/vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { PlotSeriesPoint } from "@aios/chart-engine/src/render/plotRenderers";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { OverlaySeriesByOutput } from "./ChartPlotLayer";
import { DataWindowPanel } from "./DataWindowPanel";

afterEach(cleanup);

function candle(hourOffset: number): StreamCandle {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  const close = new Date(Date.UTC(2026, 8, 3, hourOffset + 1, 0, 0));
  return {
    openTimeMs: open.getTime(),
    confirmed: true,
    record: {
      key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" },
      open_time: open.toISOString(),
      close_time: close.toISOString(),
      open: "50000.00",
      high: "50500.00",
      low: "49800.00",
      close: "50200.00",
      volume: "12.5",
      quote_volume: "628500.00",
    },
  };
}

function overlay(id: string): OverlayEntry {
  return { id, placement: "sub-pane", params: [], outputs: [{ name: "value", series: "line" }], paneIndex: 1 };
}

function pointsAt(candles: readonly StreamCandle[], value: number): readonly PlotSeriesPoint[] {
  return candles.map((c) => ({ time: c.openTimeMs, value }));
}

describe("DataWindowPanel — CH-16d 지표 30종 값 동시 표시", () => {
  it("renders one row per figure at the crosshair-resolved bar", () => {
    const candles = [candle(0), candle(1)];
    const overlays = [overlay("SMA")];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([["SMA", new Map([["value", pointsAt(candles, 101.5)]])]]);

    render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={candles[0]!.openTimeMs} />);

    const rows = screen.getAllByTestId("data-window-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("101.5000");
  });

  it("DoD: 30 indicators render 30 rows simultaneously in the DOM (no truncation, no virtualization)", () => {
    const candles = [candle(0)];
    const overlays = Array.from({ length: 30 }, (_, i) => overlay(`IND_${i}`));
    const overlaySeries = new Map<string, OverlaySeriesByOutput>(
      overlays.map((o, i) => [o.id, new Map([["value", pointsAt(candles, i + 0.5)]])] as const),
    );

    render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={null} />);

    expect(screen.getAllByTestId("data-window-row")).toHaveLength(30);
  });

  it("negative: duplicate indicator id is surfaced as an explicit error, not an empty panel or a stale last value", () => {
    const candles = [candle(0)];
    const overlays = [overlay("SMA"), overlay("SMA")];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([["SMA", new Map([["value", pointsAt(candles, 1)]])]]);

    render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={null} />);

    expect(screen.getByTestId("data-window-error")).toHaveTextContent("같은 지표 id가 중복되어");
    expect(screen.queryByTestId("data-window-row")).not.toBeInTheDocument();
  });
});
