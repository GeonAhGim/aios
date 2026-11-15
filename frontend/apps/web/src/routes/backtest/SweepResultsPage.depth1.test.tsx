import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SweepPointResultView, SweepRequestInput, SweepResultView } from "@aios/api-client";
import { SweepResultsPage, type SweepResultsPageProps } from "./SweepResultsPage";
import { perfBudgetMs } from "../../test/perfBudget";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

const request = {
  venue: "BITGET",
  instrumentId: "BTCUSDT",
  timeframe: "1h",
  start: "2026-01-01T00:00:00Z",
  end: "2026-02-01T00:00:00Z",
  initialCash: "10000",
  axes: [],
  combos: [],
  metric: "final_equity",
  dataLineageHash: "hash",
  rollupVersion: "v1",
  seed: 1,
} as unknown as SweepRequestInput;

function gridResult(size: number): SweepResultView {
  const values = Array.from({ length: size }, (_, i) => i);
  const points: SweepPointResultView[] = [];
  for (const x of values) {
    for (const y of values) {
      points.push({
        comboKey: `x=${x},y=${y}`,
        comboIndex: points.length,
        axisValues: { x, y },
        metricValue: String(x * 100 + y),
        reproducibilityKey: `repro-${x}-${y}`,
        seed: 1,
      });
    }
  }
  return {
    axes: [
      { name: "x", values },
      { name: "y", values },
    ],
    metric: "final_equity",
    points,
    stability: null,
    warnings: [],
  };
}

function renderPage(props: SweepResultsPageProps, withRequest = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[{ pathname: "/backtest/sweep-results", state: withRequest ? { sweepRequest: request } : undefined }]}
      >
        <Routes>
          <Route path="/backtest/sweep-results" element={<SweepResultsPage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Render 12x12 (144 combos) and return elapsed ms until the last cell appears.
async function timedRender(size: number): Promise<number> {
  const runSweep = vi.fn().mockResolvedValue(gridResult(size));
  const startedAt = performance.now();
  renderPage({ runSweep });
  await screen.findByTestId(`sweep-cell-${size - 1}-${size - 1}`);
  return performance.now() - startedAt;
}

describe("SweepResultsPage depth1 (D2)", () => {
  // Local measurement: ~60ms for 12x12 on a plain run; budget 4000ms is >30x headroom
  // (also absorbs shared-CI contention) yet still fails a quadratic-or-worse regression.
  it("perf: 12x12 grid renders within the time budget", async () => {
    const elapsed = await timedRender(12);
    expect(elapsed).toBeLessThan(perfBudgetMs(4000));
    expect(document.querySelectorAll('td[data-testid^="sweep-cell-"]')).toHaveLength(144);
  });

  // gate red: a broken (delayed) dependency makes the very same budget assertion fail.
  it("gate red: the same time-budget assertion fails when the sweep response is delayed", async () => {
    const runSweep = vi.fn().mockImplementation(
      () => new Promise<SweepResultView>((resolve) => setTimeout(() => resolve(gridResult(3)), 150)),
    );
    const startedAt = performance.now();
    renderPage({ runSweep });
    await screen.findByTestId("sweep-cell-2-2");
    const elapsed = performance.now() - startedAt;
    expect(() => expect(elapsed).toBeLessThan(100)).toThrow();
  });

  it("negative: without a sweep request nothing is fetched and no heatmap or alert renders", () => {
    const runSweep = vi.fn();
    renderPage({ runSweep }, false);
    expect(runSweep).not.toHaveBeenCalled();
    expect(screen.queryByTestId("sweep-cell-0-0")).not.toBeInTheDocument();
    expect(screen.queryByText("재현 키")).not.toBeInTheDocument();
  });

  it("failure injection: a rejected sweep shows no heatmap and no deploy button", async () => {
    const runSweep = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage({ runSweep });
    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByTestId("sweep-cell-0-0")).not.toBeInTheDocument();
    expect(screen.queryByText("모의 운용 배포하러 가기")).not.toBeInTheDocument();
    await waitFor(() => expect(runSweep).toHaveBeenCalledTimes(1));
  });

  it("negative: a never-resolving sweep rejects the heatmap wait (findBy times out)", async () => {
    const runSweep = vi.fn().mockReturnValue(new Promise(() => undefined));
    renderPage({ runSweep });
    await expect(screen.findByTestId("sweep-cell-0-0", {}, { timeout: 100 })).rejects.toThrow();
    expect(screen.queryByText("모의 운용 배포하러 가기")).not.toBeInTheDocument();
  });
});
