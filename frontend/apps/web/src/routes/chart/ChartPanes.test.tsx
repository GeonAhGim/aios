import "@testing-library/jest-dom/vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { ChartPanes } from "./ChartPanes";

afterEach(cleanup);

function overlay(id: string, placement: OverlayEntry["placement"] = "sub-pane"): OverlayEntry {
  return { id, placement, params: [], outputs: [{ name: "value", series: "line" }], paneIndex: 1 };
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
function Harness({ initialSub, restoredHeightRatios }: { initialSub: OverlayEntry[]; restoredHeightRatios?: Record<string, number> }) {
  const [sub, setSub] = useState(initialSub);
  return (
    <ChartPanes
      candles={CANDLES}
      mainOverlays={[]}
      subOverlays={sub}
      drawings={[]}
      onRemoveSubOverlay={(id) => setSub((prev) => prev.filter((o) => o.id !== id))}
      restoredHeightRatios={restoredHeightRatios}
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
