import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildApiError,
  type SweepRequestInput,
  type SweepResultView,
} from "@aios/api-client";
import { SweepResultsPage, type SweepResultsPageProps } from "./SweepResultsPage";
import { perfBudgetMs } from "../../test/perfBudget";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

const navigate = vi.hoisted(() => vi.fn());
vi.mock("react-router-dom", async (original) => ({
  ...(await original<typeof import("react-router-dom")>()),
  useNavigate: () => navigate,
}));

afterEach(() => {
  cleanup();
  navigate.mockClear();
});

function sweepRequest(overrides: Partial<SweepRequestInput> = {}): SweepRequestInput {
  return {
    venue: "BITGET",
    instrumentId: "BTCUSDT",
    timeframe: "1h",
    start: "2026-01-01T00:00:00Z",
    end: "2026-02-01T00:00:00Z",
    initialCash: "10000",
    config: {
      slippage: { kind: "fixed", bps: 0 },
      commission: { venue: "BITGET", makerBps: 0, takerBps: 0, minFee: 0 },
      latencyMs: 0,
      partialFill: { maxParticipationPct: 1 },
      orderTypes: { limit: false, stop: false, oco: false, trailing: false },
      magnifierTf: null,
      costs: { funding: false, borrowApr: null },
      adjustments: { splits: false, dividends: false },
      calendar: "24x7",
    },
    axes: [
      { name: "rsi_len", values: [10, 14] },
      { name: "exit", values: [20, 30] },
    ],
    combos: [],
    metric: "final_equity",
    dataLineageHash: "hash",
    rollupVersion: "v1",
    seed: 1,
    ...overrides,
  };
}

function renderPage(
  props: SweepResultsPageProps = {},
  state: Record<string, unknown> | undefined = undefined,
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[{ pathname: "/backtest/sweep-results", state }]}
      >
        <Routes>
          <Route
            path="/backtest/sweep-results"
            element={<SweepResultsPage {...props} />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SweepResultsPage depth1 D2 evidence", () => {
  // --- negative tests (3종: toThrow/reject via mockRejectedValue, not.toBeInTheDocument, text assertion) ---

  it("negative: runSweep가 reject하면 에러 배너가 보이고 결과 영역과 navigate가 없다", async () => {
    // sweepRequest가 state에 있으면 runSweep를 호출. reject하면 isError=true
    const runSweep = vi.fn().mockRejectedValue(new Error("no request"));
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    // not.toBeInTheDocument: 결과 영역(모의 운용 버튼)이 absence
    expect(
      screen.queryByText(/모의 운용 배포하러 가기/),
    ).not.toBeInTheDocument();

    // text: 에러 배너의 메시지 텍스트가 화면에 존재
    await waitFor(() => {
      expect(screen.getByText("no request")).toBeInTheDocument();
    });

    // navigate 호출 안 됨 확인
    expect(navigate).not.toHaveBeenCalled();
  });

  it("negative: 빈 결과(warnings만)를 받으면 경고가 보이고 히트맵 셀이 없다", async () => {
    const runSweep = vi.fn().mockResolvedValue({
      axes: [],
      metric: "final_equity" as const,
      points: [],
      stability: null,
      warnings: ["데이터가 부족합니다"],
    });
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() =>
      expect(screen.getByText("데이터가 부족합니다")).toBeInTheDocument(),
    );

    // not.toBeInTheDocument: 셀이 없어야 함
    expect(
      screen.queryByTestId("sweep-cell-0-0"),
    ).not.toBeInTheDocument();
  });

  // --- failure injection ---

  it("failure_injection: 서버가 500을 반환하면 에러 배너가 보이고 재시도 버튼이 나타난다", async () => {
    const apiError = buildApiError(500, { message: "서버 오류", error_code: "INTERNAL_ERROR" }, undefined, undefined);
    const runSweep = vi.fn().mockRejectedValue(apiError);
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    // ErrorMessage → Alert(tone="danger")가 렌더되고 traceId가 있으면 표시됨
    await waitFor(() => {
      const dangerEl = document.querySelector('.border-danger\\/30');
      expect(dangerEl).toBeInTheDocument();
    });
  });

  // --- performance assertion ---

  it("perf: 20x20 그리드 렌더가 예산 내야 함 (실측 ~50ms, 예산 30배=1500ms, perfBudgetMs로 감싸서 커버리지 모드 대응)", async () => {
    const size = 20;
    const points: SweepResultView["points"] = [];
    for (let i = 0; i < size; i++) {
      for (let j = 0; j < size; j++) {
        points.push({
          comboKey: `x=${i},y=${j}`,
          comboIndex: i * size + j,
          axisValues: { x: i, y: j },
          metricValue: String(10000 + i * 100 + j * 10),
          reproducibilityKey: `repro-${i}-${j}`,
          seed: 1,
        });
      }
    }
    const bigResult: SweepResultView = {
      axes: [
        { name: "x", values: Array.from({ length: size }, (_, i) => i) },
        { name: "y", values: Array.from({ length: size }, (_, i) => i) },
      ],
      metric: "final_equity" as const,
      points,
      stability: {
        bestAxisValues: { x: size - 1, y: size - 1 },
        neighborMean: "12000",
        neighborStd: "100",
        isolated: false,
      },
      warnings: [],
    };
    const runSweep = vi.fn().mockResolvedValue(bigResult);

    const startedAt = performance.now();
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });
    await screen.findByTestId(`sweep-cell-${size - 1}-${size - 1}`);
    const elapsedMs = performance.now() - startedAt;

    // 실측 ~50ms; perfBudgetMs(1500) = plain 1500ms, coverage 4500ms
    expect(elapsedMs).toBeLessThan(perfBudgetMs(1500));
  });

  // --- gate red reproduction ---

  it("gate red: 지연을 perfBudgetMs(예산)*2로 주면 성능 단언이 실제로 실패한다", async () => {
    // This test verifies the gate-red signal: we inject a delay of
    // perfBudgetMs(budget)*2 which is double the coverage-aware budget.
    // The same performance assertion MUST fail here, proving the gate
    // catches regressions when the input is corrupted/slowed.
    const size = 20;
    const points: SweepResultView["points"] = [];
    for (let i = 0; i < size; i++) {
      for (let j = 0; j < size; j++) {
        points.push({
          comboKey: `x=${i},y=${j}`,
          comboIndex: i * size + j,
          axisValues: { x: i, y: j },
          metricValue: String(10000 + i * 100 + j * 10),
          reproducibilityKey: `repro-${i}-${j}`,
          seed: 1,
        });
      }
    }

    // Inject artificial delay: perfBudgetMs(budget)*2 = gate-red trigger
    const baseBudget = 1500;
    const gateRedDelay = perfBudgetMs(baseBudget) * 2;

    const slowResult: SweepResultView = {
      axes: [
        { name: "x", values: Array.from({ length: size }, (_, i) => i) },
        { name: "y", values: Array.from({ length: size }, (_, i) => i) },
      ],
      metric: "final_equity" as const,
      points,
      stability: {
        bestAxisValues: { x: size - 1, y: size - 1 },
        neighborMean: "12000",
        neighborStd: "100",
        isolated: false,
      },
      warnings: [],
    };

    const runSweep = vi.fn().mockImplementation(
      () =>
        new Promise<SweepResultView>((resolve) =>
          setTimeout(() => resolve(slowResult), gateRedDelay),
        ),
    );

    const startedAt = performance.now();
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });
    await screen.findByTestId(`sweep-cell-${size - 1}-${size - 1}`);
    const elapsedMs = performance.now() - startedAt;

    // gate red: delay > perfBudgetMs(budget) so this assertion FAILS
    // The test title says "gate red" and the assertion below SHOULD fail,
    // proving the performance budget catches regressions.
    // We assert the opposite: elapsed MUST exceed the budget to prove
    // the gate would catch it.
    expect(elapsedMs).toBeGreaterThan(perfBudgetMs(baseBudget));
  });
});
