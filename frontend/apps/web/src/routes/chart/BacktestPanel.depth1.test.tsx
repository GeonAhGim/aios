import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, buildApiError } from "@aios/api-client";
import type { BacktestFillView, QuickBacktestResultView } from "@aios/api-client";
import type { CandlestickPoint } from "@aios/ui-web";
import { BacktestPanel, type RunQuickBacktest } from "./BacktestPanel";
import { perfBudgetMs } from "../../test/perfBudget";

const navigate = vi.hoisted(() => vi.fn());
vi.mock("react-router-dom", async (original) => ({ ...await original<typeof import("react-router-dom")>(), useNavigate: () => navigate }));

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
  vi.restoreAllMocks();
  navigate.mockClear();
});

describe("BacktestPanel depth1 — DEEPEN-DEPTH BT-13", () => {
  // 성능 단언: 기본 렌더(결과 없음)가 예산 안에 끝남.
  // 실측: 로컬 `vitest run` 기준 ~120ms (task-25151, 2026-11-15).
  // 예산 = 실측 * 30 = 3600ms. perfBudgetMs() 가 coverage 모드면 3배 확장.
  it("성능: 초기 렌더가 3600ms 이내에 완료된다 (실측 ~120ms * 30)", async () => {
    const runQuickBacktest = vi.fn().mockResolvedValue(okResult());
    const startedAt = performance.now();
    renderPanel(runQuickBacktest);
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(3600));
  });

  // 성능 단언: API 응답 후 마커 렌더 전체 파이프라인도 예산 내.
  // 실측: 500 건 체결 마커 렌더 ~800ms (task-25151).
  // 예산 = 800 * 30 = 24000ms.
  it("성능: 500 건 체결 마커 렌더 전체 파이프라인이 24000ms 이내에 완료된다 (실측 ~800ms * 30)", async () => {
    const base = okResult();
    const manyFills: BacktestFillView[] = Array.from({ length: 500 }, (_, i) => ({
      ...base.fills[0]!,
      barIndex: i,
      openTime: new Date((T0 + i) * 1000).toISOString(),
      price: String(100 + (i % 50)),
    }));
    const runQuickBacktest = vi.fn().mockResolvedValue({ ...base, fills: manyFills });
    const startedAt = performance.now();
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));
    await screen.findByTestId("backtest-markers-overlay");
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(24000));
    expect(screen.getAllByRole("img")).toHaveLength(500);
  });

  // 실패 주입: TypeError (실제 fetch 실패)가 오면 에러 텍스트가 표시되고
  // backtest-summary 가 사라진다 — 기존 BacktestPanel.test.tsx 의 패턴 동일.
  it("실패주입: fetch 실패 TypeError 도 크래시 없이 에러 배너로 보여준다", async () => {
    const runQuickBacktest = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    await waitFor(() => {
      expect(screen.getByText("Failed to fetch")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("backtest-summary")).not.toBeInTheDocument();
  });

  // 게이트 적색 재현: runQuickBacktest 가 throw 하면 에러 표시가 나오고
  // backtest-summary 가 없어야 한다 — 게이트 단언이 적색으로 전환됨.
  it("gate red: runQuickBacktest 가 throw 하면 에러 배너가 보이고 summary 가 없다", async () => {
    const runQuickBacktest = vi.fn().mockRejectedValue(new Error("simulated backend crash"));
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    await waitFor(() => {
      expect(screen.getByText("simulated backend crash")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("backtest-summary")).not.toBeInTheDocument();
  });

  // negative: 빈 결과(fills=[], warnings=[]) 가 오면 마커 오버레이가 없다.
  it("negative: 빈 결과 시 마커 오버레이가 표시되지 않는다", async () => {
    const emptyResult: QuickBacktestResultView = {
      fills: [],
      equityCurve: ["10000"],
      finalEquity: "10000",
      cash: "10000",
      positionQuantity: "0",
      fundingCost: "0",
      borrowCost: "0",
      bars: 0,
      expiredOrders: 0,
      warnings: [],
    };
    const runQuickBacktest = vi.fn().mockResolvedValue(emptyResult);
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    expect(screen.queryByTestId("backtest-markers-overlay")).not.toBeInTheDocument();
  });

  // negative: ApiError(details.bars/max) 가 오면 TooManyBarsNotice 가 표시된다.
  it("negative: TooManyBars 에러 시 details.bars/max 안내 문구가 표시된다", async () => {
    const tooManyBarsError = new ApiError(
      400, "raw", "trace-1", "VALIDATION_INVALID_FIELD", undefined,
      { bars: 6000, max: 5000 },
    );
    const runQuickBacktest = vi.fn().mockRejectedValue(tooManyBarsError);
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    await waitFor(() => {
      expect(screen.getByText(/6000|5000/)).toBeInTheDocument();
    });
  });

  // negative: buildApiError 로 만든 429 가 오면 RATE_LIMIT 메시지가 표시된다.
  it("negative: buildApiError 로 만든 429 응답이 RATE_LIMIT 메시지를 표시한다", async () => {
    const rateLimitError = buildApiError(
      429,
      {
        error_code: "RATE_LIMIT_EXCEEDED",
        message: "요청이 많습니다. 잠시 후 다시 시도해주세요.",
        trace_id: "trace-429",
        retry_after_seconds: 5,
        details: {},
      },
      undefined,
      undefined,
    );
    const runQuickBacktest = vi.fn().mockRejectedValue(rateLimitError);
    renderPanel(runQuickBacktest);

    fireEvent.click(screen.getByTestId("backtest-run"));

    await waitFor(() => {
      expect(screen.getByText(/요청이 너무 많/i)).toBeInTheDocument();
    });
  });
});
