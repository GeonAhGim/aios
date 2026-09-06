import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@aios/api-client";
import type { QuickBacktestResultView } from "@aios/api-client";
import type { CandlestickPoint } from "@aios/ui-web";
import { BacktestPanel, type RunQuickBacktest } from "./BacktestPanel";

const T0 = Math.floor(Date.parse("2026-09-06T00:00:00Z") / 1000);
const T1 = T0 + 3600;
const POINTS: CandlestickPoint[] = [
  { time: T0, open: 100, high: 110, low: 90, close: 100 },
  { time: T1, open: 100, high: 120, low: 95, close: 105 },
];

function renderPanel(runQuickBacktest: RunQuickBacktest, points: readonly CandlestickPoint[] = POINTS) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <BacktestPanel
        venue="BITGET"
        instrumentId="instr-1"
        timeframe="1h"
        start="2026-09-05T00:00:00Z"
        end="2026-09-06T01:00:00Z"
        points={points}
        runQuickBacktest={runQuickBacktest}
      />
    </QueryClientProvider>,
  );
}

function okResult(): QuickBacktestResultView {
  return {
    fills: [
      {
        barIndex: 0,
        openTime: "2026-09-06T00:10:00Z",
        side: "BUY",
        orderType: "market",
        quantity: "0.5",
        price: "100.00",
        commission: "0.01",
        remainingQuantity: "0",
      },
    ],
    equityCurve: ["10000", "10005"],
    finalEquity: "10005",
    cash: "9950",
    positionQuantity: "0.5",
    fundingCost: "0",
    borrowCost: "0",
    bars: 2,
    expiredOrders: 0,
    warnings: [],
  };
}

afterEach(() => {
  cleanup();
});

describe("BacktestPanel", () => {
  it("실행하면 결과 요약과 체결 마커 오버레이를 보여준다", async () => {
    const runQuickBacktest = vi.fn().mockResolvedValue(okResult());
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByTestId("backtest-summary")).toBeInTheDocument();
    expect(screen.getByText("10005")).toBeInTheDocument();
    expect(screen.getByLabelText("BUY 0.5 @ 100.00")).toBeInTheDocument();
    expect(runQuickBacktest.mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({ venue: "BITGET", instrumentId: "instr-1", scriptSource: expect.any(String) }),
    );
  });

  it("negative: 봉 상한 초과(TooManyBarsError, details.bars/max)는 BT-11 안내 문구를 보여준다", async () => {
    const runQuickBacktest = vi
      .fn()
      .mockRejectedValue(new ApiError(400, "raw", "trace-1", "VALIDATION_INVALID_FIELD", undefined, { bars: 6000, max: 5000 }));
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByText("봉 수 6000개가 즉시 백테스트 상한 5000개를 넘습니다. 전체 백테스트(BT-11)를 이용하세요.")).toBeInTheDocument();
  });

  it("negative: 스크립트 컴파일 오류(details.code/line/col)는 ScriptEditor 마커로 보여주고 일반 오류 배너는 숨긴다", async () => {
    const runQuickBacktest = vi
      .fn()
      .mockRejectedValue(
        new ApiError(400, "raw", "trace-2", "VALIDATION_INVALID_FIELD", undefined, {
          code: "SCRIPT_SYNTAX",
          line: 3,
          col: 7,
        }),
      );
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(await screen.findByTestId("script-editor-marker-0")).toHaveTextContent("3행 7열: SCRIPT_SYNTAX");
    expect(screen.queryByTestId("backtest-summary")).not.toBeInTheDocument();
  });

  it("negative: 표시할 캔들이 없으면 실행 버튼을 비활성화하고 안내를 보여준다", () => {
    const runQuickBacktest = vi.fn();
    renderPanel(runQuickBacktest, []);

    expect(screen.getByText("표시할 캔들이 없어 백테스트를 실행할 수 없습니다.")).toBeInTheDocument();
    expect(screen.getByTestId("backtest-run")).toBeDisabled();
    expect(runQuickBacktest).not.toHaveBeenCalled();
  });

  it("negative: 실행 중에는 재요청을 막고, 취소하면 다시 실행할 수 있다", async () => {
    let resolveRun: (value: QuickBacktestResultView) => void = () => {};
    const runQuickBacktest = vi.fn().mockImplementation(
      () =>
        new Promise<QuickBacktestResultView>((resolve) => {
          resolveRun = resolve;
        }),
    );
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    await waitFor(() => expect(screen.getByTestId("backtest-run")).toBeDisabled());
    expect(screen.getByTestId("backtest-cancel")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("backtest-cancel"));
    await waitFor(() => expect(screen.getByTestId("backtest-run")).not.toBeDisabled());

    fireEvent.click(screen.getByTestId("backtest-run"));
    await waitFor(() => expect(runQuickBacktest).toHaveBeenCalledTimes(2));
    resolveRun(okResult());
    expect(await screen.findByTestId("backtest-summary")).toBeInTheDocument();
  });
});
