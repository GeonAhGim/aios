import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SweepRouteNotImplementedError, type SweepRequestInput, type SweepResultView } from "@aios/api-client";
import { SweepResultsPage, type SweepResultsPageProps } from "./SweepResultsPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

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
    metric: "finalEquity",
    dataLineageHash: "hash",
    rollupVersion: "v1",
    seed: 1,
    ...overrides,
  };
}

function sweepResult(overrides: Partial<SweepResultView> = {}): SweepResultView {
  return {
    axes: [
      { name: "rsi_len", values: [10, 14] },
      { name: "exit", values: [20, 30] },
    ],
    metric: "finalEquity",
    points: [
      {
        comboKey: "rsi_len=10,exit=20",
        comboIndex: 0,
        axisValues: { rsi_len: 10, exit: 20 },
        metricValue: "10500",
        reproducibilityKey: "repro-1",
        seed: 1,
      },
      {
        comboKey: "rsi_len=14,exit=30",
        comboIndex: 1,
        axisValues: { rsi_len: 14, exit: 30 },
        metricValue: "10900",
        reproducibilityKey: "repro-2",
        seed: 1,
      },
    ],
    stability: {
      bestAxisValues: { rsi_len: 14, exit: 30 },
      neighborMean: "10700",
      neighborStd: "50",
      isolated: false,
    },
    warnings: [],
    ...overrides,
  };
}

function renderPage(props: SweepResultsPageProps = {}, state: Record<string, unknown> | undefined = undefined) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[{ pathname: "/backtest/sweep-results", state }]}>
        <Routes>
          <Route path="/backtest/sweep-results" element={<SweepResultsPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SweepResultsPage", () => {
  it("스윕 요청이 없으면 안내 문구를 보여준다", () => {
    renderPage();
    expect(screen.getByText(/먼저 파라미터 스윕을 구성해/)).toBeInTheDocument();
  });

  it("결과가 오면 히트맵 셀과 재현 키를 렌더링한다", async () => {
    const runSweep = vi.fn().mockResolvedValue(sweepResult());
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() => expect(screen.getByTestId("sweep-cell-10-20")).toHaveTextContent("10500"));
    expect(screen.getByTestId("sweep-cell-14-30")).toHaveTextContent("10900");
    expect(screen.getByText("repro-1")).toBeInTheDocument();
    expect(screen.getByText("안정적")).toBeInTheDocument();
    expect(runSweep).toHaveBeenCalledWith(expect.objectContaining({ metric: "finalEquity" }));
  });

  it("축이 2개가 아니면 히트맵 대신 안내를 보여준다", async () => {
    const runSweep = vi.fn().mockResolvedValue(
      sweepResult({ axes: [{ name: "rsi_len", values: [10, 14] }], stability: null }),
    );
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() =>
      expect(screen.getByText(/히트맵은 축이 정확히 2개일 때만 표시됩니다/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/안정성 표면을 계산할 수 없습니다/)).toBeInTheDocument();
  });

  it("라우트가 아직 없으면(SweepRouteNotImplementedError) 경고를 보여준다", async () => {
    const runSweep = vi.fn().mockRejectedValue(new SweepRouteNotImplementedError());
    renderPage({ runSweep }, { sweepRequest: sweepRequest() });

    await waitFor(() =>
      expect(screen.getByText("파라미터 스윕 결과 API가 아직 제공되지 않습니다.")).toBeInTheDocument(),
    );
  });
});
