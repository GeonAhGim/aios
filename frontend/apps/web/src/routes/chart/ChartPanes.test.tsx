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
import { buildInitialModel, MAIN_PANE_ID, subPaneId } from "./chartPanesModel";

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
  candles = CANDLES,
}: {
  initialSub: OverlayEntry[];
  mainOverlays?: OverlayEntry[];
  restoredHeightRatios?: Record<string, number>;
  overlaySeries?: ReadonlyMap<string, OverlaySeriesByOutput>;
  overlayPlotSpecs?: ReadonlyMap<string, OverlayPlotSpecOverrides>;
  candles?: readonly StreamCandle[];
}) {
  const [sub, setSub] = useState(initialSub);
  return (
    <ChartPanes
      candles={candles}
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

describe("ChartPanes — CH-16d dataWindow 배선 (task-2044)", () => {
  it("mounts DataWindowPanel fed by the same overlays/overlaySeries/candles props (wiring proof: revert the mount and this fails)", () => {
    const overlays = Array.from({ length: 30 }, (_, i) => overlay(`IND_${i}`));
    const overlaySeries = new Map<string, OverlaySeriesByOutput>(
      overlays.map((o, i) => [o.id, new Map([["value", overlaySeriesPoints([i + 0.5, i + 0.5, i + 0.5, i + 0.5])]])] as const),
    );

    render(<Harness initialSub={overlays} overlaySeries={overlaySeries} />);

    expect(screen.getByTestId("data-window-panel")).toBeInTheDocument();
    expect(screen.getAllByTestId("data-window-row")).toHaveLength(30);
  });

  // Duplicate-indicator-id rejection (DataWindowError, not a silent empty panel)
  // is covered directly in DataWindowPanel.test.tsx: at this integration level a
  // duplicate id across main/sub overlays is already rejected one layer up by
  // legend/objectTree.ts's own uniqueness invariant (CHART_OBJECT_TREE_DUPLICATE_ID),
  // so it never reaches computeDataWindowRows here.
});

function ohlcvCandle(hourOffset: number, v: { open: number; high: number; low: number; close: number; volume: number }): StreamCandle {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  const close = new Date(Date.UTC(2026, 8, 3, hourOffset + 1, 0, 0));
  return {
    openTimeMs: open.getTime(),
    confirmed: true,
    record: {
      key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" },
      open_time: open.toISOString(),
      close_time: close.toISOString(),
      open: v.open.toFixed(2),
      high: v.high.toFixed(2),
      low: v.low.toFixed(2),
      close: v.close.toFixed(2),
      volume: v.volume.toFixed(2),
      quote_volume: "0",
    },
  };
}

// Three bars with distinct OHLCV each (unlike CANDLES, which repeats the same
// values) so an off-by-one bar lookup is actually detectable.
const OHLCV_BARS = [
  ohlcvCandle(0, { open: 100, high: 110, low: 90, close: 105, volume: 50 }),
  ohlcvCandle(1, { open: 200, high: 220, low: 190, close: 210, volume: 75 }),
  ohlcvCandle(2, { open: 300, high: 330, low: 290, close: 310, volume: 60 }),
];

function statusLineValue(title: string): string | null {
  return screen.getByTestId(`chart-status-line-${title}`).textContent?.replace(title, "").trim() ?? null;
}

describe("ChartPanes — CH-16e statusLine 실배선 (task-2045)", () => {
  it("crosshair를 바 1(가운데)에 두면 상태줄 O/H/L/C/V가 그 바의 값과 정밀도까지 일치한다 (wiring proof: revert the <StatusLine> mount and this fails)", () => {
    render(<Harness initialSub={[]} candles={OHLCV_BARS} />);

    // Surface spans bar0..bar2 (2h); clientX at the midpoint lands exactly on bar1's timestamp.
    fireEvent.mouseMove(screen.getByTestId("chart-pane-surface-main"), { clientX: 300 });

    expect(statusLineValue("O")).toBe("200.00");
    expect(statusLineValue("H")).toBe("220.00");
    expect(statusLineValue("L")).toBe("190.00");
    expect(statusLineValue("C")).toBe("210.00");
    expect(statusLineValue("Vol")).toBe("75");
  });

  it("negative: 캔들이 없으면(크로스헤어가 가리킬 바가 없으면) 상태줄 전 필드가 defaultValue('--')로 렌더링된다", () => {
    render(<Harness initialSub={[]} candles={[]} />);

    for (const title of ["O", "H", "L", "C", "Vol", "Chg", "Chg%"]) {
      expect(statusLineValue(title)).toBe("--");
    }
  });
});

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

// DEPTH_CH(task-2729) 감사: task-1914(b19b7a4, CH-14·CH-16 화면 배선)에는 수치
// 성능 단언이 없었다 — jsdom 유닛테스트 환경 기준 느슨한 ms 예산이지만, 서브페인
// 수에 대해 렌더가 선형이 아니게(예: 페인마다 전체 candles 재스캔) 퇴행하면
// 확실히 이 상한을 넘어 실패한다(task-3077/1809 DEEPEN과 동일 관용).
describe("ChartPanes — 성능 단언(DEEPEN task-3092)", () => {
  it("서브페인 20개(각 4포인트 시리즈 포함) 멀티페인 렌더가 2초 안에 끝난다", () => {
    const many = Array.from({ length: 20 }, (_, i) => overlay(`IND_${i}`));
    const overlaySeries = new Map<string, OverlaySeriesByOutput>(
      many.map((o, i) => [o.id, new Map([["value", overlaySeriesPoints([i, i + 1, i + 2, i + 3])]])] as const),
    );

    const startedAt = performance.now();
    render(<Harness initialSub={many} overlaySeries={overlaySeries} />);
    const elapsedMs = performance.now() - startedAt;

    expect(screen.getAllByTestId(/^chart-pane-ratio-/).length).toBe(21);
    expect(elapsedMs).toBeLessThan(2000);
  });
});

// DEPTH_CH(task-2729) 감사: task-1914에 게이트 적색 재현이 없었다 — heightRatio
// 합 불일치를 fail-closed로 거부하는 계약(paneModel.setHeightRatios)이 실제로
// 이 화면에서 지켜지고 있음을, "검증 없이 그대로 받아들이는" legacy 목업과
// 대조해(red) 실 컴포넌트가 그 회귀를 내지 않는다는 것(green)으로 못박는다.
describe("ChartPanes — 게이트 적색 재현(DEEPEN task-3092): heightRatio 합 미검증", () => {
  /** setHeightRatios의 합=1 검증 이전 상태를 흉내낸 legacy 목업 — 실제 모듈이 아니다. */
  function naiveApplyHeightRatios(base: ReturnType<typeof buildInitialModel>, ratios: Record<string, number>) {
    return { ...base, panes: base.panes.map((p) => ({ ...p, heightRatio: ratios[p.id] ?? p.heightRatio })) };
  }

  it("red: 합 검증이 없는 legacy는 합계 0.9(main 0.5 + sub-MFI 0.4)를 그대로 받아들인다", () => {
    const base = buildInitialModel(["MFI"]);
    const naive = naiveApplyHeightRatios(base, { [MAIN_PANE_ID]: 0.5, [subPaneId("MFI")]: 0.4 });

    const sum = naive.panes.reduce((acc, p) => acc + p.heightRatio, 0);
    expect(sum).toBeCloseTo(0.9, 4);
  });

  it("green: 실 ChartPanes는 같은 입력을 거부하고 배너로 표면화하며 합=1로 남는다(legacy와 달리 무음 폴백하지 않는다)", () => {
    render(<Harness initialSub={[overlay("MFI")]} restoredHeightRatios={{ [MAIN_PANE_ID]: 0.5, [subPaneId("MFI")]: 0.4 }} />);

    expect(screen.getByTestId("chart-panes-layout-error")).toBeInTheDocument();
    const ratios = ratiosOf([MAIN_PANE_ID, subPaneId("MFI")]);
    expect(ratios.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 4);
  });
});
