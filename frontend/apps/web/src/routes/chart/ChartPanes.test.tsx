import "@testing-library/jest-dom/vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { PlotSeriesPoint } from "@aios/chart-engine/src/render/plotRenderers";
import { CandlestickChart } from "@aios/ui-web";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartPanes } from "./ChartPanes";
import type { OverlayPlotSpecOverrides, OverlaySeriesByOutput } from "./ChartPlotLayer";

// CandlesPage.test.tsx/ChartPage.test.tsx의 관용과 동일 — lightweight-charts는
// jsdom에서 canvas를 요구하므로 실 렌더러 대신 전달받은 data.length만 노출하는
// 스텁으로 바꾼다. CH-19c 배선 증명 테스트가 이 스텁이 실제로 받는 데이터를 읽는다.
vi.mock("@aios/ui-web", async () => {
  const actual = await vi.importActual<typeof import("@aios/ui-web")>("@aios/ui-web");
  return {
    ...actual,
    CandlestickChart: ({ data }: { data: unknown[] }) => <div data-testid="candlestick-chart">캔들 {data.length}개</div>,
  };
});

afterEach(cleanup);

function overlay(id: string, placement: OverlayEntry["placement"] = "sub-pane", outputs: OverlayEntry["outputs"] = [{ name: "value", series: "line" }]): OverlayEntry {
  return { id, placement, params: [], outputs, paneIndex: 1 };
}

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

const CANDLES = [candle(0), candle(1), candle(2), candle(3)];

function ratiosOf(paneIds: readonly string[]): number[] {
  return paneIds.map((id) => Number(screen.getByTestId(`chart-pane-ratio-${id}`).textContent));
}

// 부모(ChartPage)가 selectedIndicatorIds를 소유하는 실제 관용을 그대로 흉내낸다 —
// ChartPanes는 subOverlays를 controlled prop으로만 받는다(decision: 새 상태 신설 금지).
function Harness({
  initialSub,
  mainOverlays = [],
  restoredHeightRatios,
  overlaySeries,
  overlayPlotSpecs,
}: {
  initialSub: OverlayEntry[];
  mainOverlays?: OverlayEntry[];
  restoredHeightRatios?: Record<string, number>;
  overlaySeries?: ReadonlyMap<string, OverlaySeriesByOutput>;
  overlayPlotSpecs?: ReadonlyMap<string, OverlayPlotSpecOverrides>;
}) {
  const [sub, setSub] = useState(initialSub);
  return (
    <ChartPanes
      candles={CANDLES}
      mainOverlays={mainOverlays}
      subOverlays={sub}
      drawings={[]}
      onRemoveSubOverlay={(id) => setSub((prev) => prev.filter((o) => o.id !== id))}
      restoredHeightRatios={restoredHeightRatios}
      overlaySeries={overlaySeries}
      overlayPlotSpecs={overlayPlotSpecs}
    >
      <div data-testid="main-content">캔들</div>
    </ChartPanes>
  );
}

describe("ChartPanes — CH-14 서브패널 CRUD 불변식", () => {
  it("서브페인 3개 중 가운데를 삭제해도 남은 페인 heightRatio 합이 1로 유지된다", () => {
    render(<Harness initialSub={[overlay("RSI"), overlay("MACD"), overlay("OBV")]} />);

    const before = ratiosOf(["main", "sub-RSI", "sub-MACD", "sub-OBV"]);
    expect(before.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 4);

    fireEvent.click(screen.getByRole("button", { name: "서브패널 제거 MACD" }));

    expect(screen.queryByTestId("chart-pane-sub-MACD")).not.toBeInTheDocument();
    const after = ratiosOf(["main", "sub-RSI", "sub-OBV"]);
    expect(after).toHaveLength(3);
    expect(after.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 4);
  });
});

describe("ChartPanes — CH-14 crosshairSync", () => {
  it("한 페인에서 크로스헤어를 이동시키면 모든 페인의 statusLine이 같은 시각을 표시한다", () => {
    render(<Harness initialSub={[overlay("RSI")]} />);

    expect(screen.getByTestId("chart-pane-statusline-main")).toHaveTextContent("--");
    expect(screen.getByTestId("chart-pane-statusline-sub-RSI")).toHaveTextContent("--");

    fireEvent.mouseMove(screen.getByTestId("chart-pane-surface-main"), { clientX: 300 });

    const mainTime = screen.getByTestId("chart-pane-statusline-main").textContent;
    const subTime = screen.getByTestId("chart-pane-statusline-sub-RSI").textContent;
    expect(mainTime).not.toBe("--");
    expect(subTime).toBe(mainTime);

    // 다른 페인에서 움직여도 마찬가지로 전 페인이 같은 값으로 동기화된다.
    fireEvent.mouseMove(screen.getByTestId("chart-pane-surface-sub-RSI"), { clientX: 100 });
    const mainTime2 = screen.getByTestId("chart-pane-statusline-main").textContent;
    const subTime2 = screen.getByTestId("chart-pane-statusline-sub-RSI").textContent;
    expect(subTime2).toBe(mainTime2);
    expect(subTime2).not.toBe(subTime);
  });
});

describe("ChartPanes — negative: 저장된 페인 레이아웃 높이 합 불일치", () => {
  it("heightRatios 합이 1이 아니면 적용을 거부하고 사유를 화면에 표시하며 무음 폴백하지 않는다", () => {
    render(<Harness initialSub={[overlay("MFI")]} restoredHeightRatios={{ main: 0.5, "sub-MFI": 0.4 }} />);

    const banner = screen.getByTestId("chart-panes-layout-error");
    expect(banner).toHaveTextContent("높이 비율 합이 1이 아닙니다");

    // 거부되었으므로 저장된 값([0.5, 0.4])이 그대로 적용되지 않고, 기본 분배(합=1)로 남는다.
    const ratios = ratiosOf(["main", "sub-MFI"]);
    expect(ratios).not.toEqual([0.5, 0.4]);
    expect(ratios.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 4);
  });
});

function overlaySeriesPoints(values: readonly number[]): readonly PlotSeriesPoint[] {
  return CANDLES.slice(0, values.length).map((c, i) => ({ time: c.openTimeMs, value: values[i]! }));
}

describe("ChartPanes — CH-15b: PlotSpec-driven dispatch, no screen-code change per new indicator", () => {
  it("DoD: two brand-new indicators with different PlotSpec kinds (line, band/fill_between) both render", () => {
    const newLine = overlay("BRAND_NEW_LINE", "sub-pane", [{ name: "value", series: "line" }]);
    const newBand = overlay("BRAND_NEW_BAND", "main-overlay", [
      { name: "upperband", series: "line" },
      { name: "lowerband", series: "line" },
    ]);
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([
      ["BRAND_NEW_LINE", new Map([["value", overlaySeriesPoints([1, 2, 3, 4])]])],
      [
        "BRAND_NEW_BAND",
        new Map([
          ["upperband", overlaySeriesPoints([55, 56, 57, 58])],
          ["lowerband", overlaySeriesPoints([45, 46, 47, 48])],
        ]),
      ],
    ]);

    render(<Harness initialSub={[newLine]} mainOverlays={[newBand]} overlaySeries={overlaySeries} />);

    expect(screen.getByTestId("chart-pane-plot-sub-BRAND_NEW_LINE").querySelector("polyline")).not.toBeNull();
    expect(screen.getByTestId("chart-pane-plot-main").querySelector("polygon")).not.toBeNull();
  });

  it("negative: an unknown PlotSpec.kind is rejected explicitly on screen, not silently skipped", () => {
    const weird = overlay("WEIRD", "sub-pane", [{ name: "value", series: "line" }]);
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([["WEIRD", new Map([["value", overlaySeriesPoints([1, 2])]])]]);
    const overlayPlotSpecs = new Map<string, OverlayPlotSpecOverrides>([
      [
        "WEIRD",
        new Map<string, unknown>([
          [
            "value",
            { kind: "__unknown__", scale: "own", default_pane: "separate", fill_between: null, color_rule: null, precision: null, legend_format: null },
          ],
        ]),
      ],
    ]);

    render(<Harness initialSub={[weird]} overlaySeries={overlaySeries} overlayPlotSpecs={overlayPlotSpecs} />);

    const banner = screen.getByTestId("chart-pane-plot-error-sub-WEIRD");
    expect(banner).toHaveTextContent("PLOT_RENDER_UNKNOWN_KIND");
    expect(screen.getByTestId("chart-pane-plot-sub-WEIRD").querySelector("polyline")).toBeNull();
  });
});

function denseCandles(count: number): StreamCandle[] {
  return Array.from({ length: count }, (_, i) => candle(i));
}

describe("ChartPanes — CH-19c render/lod·render/viewport wiring", () => {
  it("the mounted CandlestickChart receives a downsampled series, not the full 5,000-candle input", () => {
    const dense = denseCandles(5000);
    const placeholderData = new Array(5000).fill(0);

    render(
      <ChartPanes candles={dense} mainOverlays={[]} subOverlays={[]} drawings={[]} onRemoveSubOverlay={() => {}}>
        <CandlestickChart data={placeholderData as never} />
      </ChartPanes>,
    );

    const shown = Number(screen.getByTestId("candlestick-chart").textContent?.match(/\d+/)?.[0]);
    expect(shown).toBeGreaterThan(0);
    expect(shown).toBeLessThan(5000);
  });
});
