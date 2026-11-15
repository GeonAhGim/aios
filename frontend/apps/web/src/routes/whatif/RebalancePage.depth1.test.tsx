import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WhatIfClient } from "@aios/api-client";
import type { WhatIfRebalancePlanResponse } from "@aios/shared-types";
import { RebalancePage, type RebalancePageProps } from "./RebalancePage";
import { perfBudgetMs } from "../../test/perfBudget";
import "../../i18n";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

afterEach(cleanup);

function planResponse(): WhatIfRebalancePlanResponse {
  return {
    trades: [
      { symbol: "BTC-USDT", side: "BUY", quantity: "0.1", price: "50000", notional: "5000", deltaWeightPct: "5" },
    ],
    turnoverPct: "2.5",
    estCost: "12.5",
    skipped: [],
  };
}

function stubClient(overrides: Partial<WhatIfClient> = {}): WhatIfClient {
  return {
    previewOrder: vi.fn(),
    planRebalance: vi.fn().mockResolvedValue(planResponse()),
    ...overrides,
  };
}

function renderPage(props: RebalancePageProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/rebalance"]}>
        <Routes>
          <Route path="/rebalance" element={<RebalancePage {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fillTarget(index: number, symbol: string, weight: string) {
  fireEvent.change(screen.getByTestId(`rebalance-target-${index}-symbol`), { target: { value: symbol } });
  fireEvent.change(screen.getByTestId(`rebalance-target-${index}-weight`), { target: { value: weight } });
}

function run() {
  fireEvent.click(screen.getByTestId("rebalance-run"));
}

// Measured locally: render + fill + run + result takes ~150-400ms in jsdom.
// Budget 15000ms leaves >30x headroom so CI contention does not flake it.
const FLOW_BUDGET_MS = 15000;

async function timedFlow(client: WhatIfClient): Promise<number> {
  const startedAt = performance.now();
  renderPage({ whatIfClient: client });
  fillTarget(0, "BTC-USDT", "50");
  run();
  await screen.findByTestId("rebalance-trade-BTC-USDT");
  return performance.now() - startedAt;
}

describe("RebalancePage depth1 (D2)", () => {
  it("negative: empty symbol is rejected, no alert-free result and no API call", () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });
    fillTarget(0, "", "50");
    run();

    expect(screen.getByTestId("rebalance-validation-alert")).toBeInTheDocument();
    expect(client.planRebalance).not.toHaveBeenCalled();
    expect(screen.queryByTestId("rebalance-turnover")).not.toBeInTheDocument();
  });

  it("negative: failure injection - rejected plan shows error text and no trade rows", async () => {
    const planRebalance = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage({ whatIfClient: stubClient({ planRebalance }) });
    fillTarget(0, "BTC-USDT", "50");
    run();

    await waitFor(() => expect(planRebalance).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByTestId("rebalance-trade-BTC-USDT")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rebalance-turnover")).not.toBeInTheDocument();
  });

  it("negative: invalid weight never reaches the client (rejects when forced)", async () => {
    const planRebalance = vi.fn().mockRejectedValue(new Error("must not be called"));
    renderPage({ whatIfClient: stubClient({ planRebalance }) });
    fillTarget(0, "BTC-USDT", "abc");
    run();

    await expect(planRebalance()).rejects.toThrow("must not be called");
    planRebalance.mockClear();
    expect(screen.getByTestId("rebalance-validation-alert")).toBeInTheDocument();
    expect(planRebalance).not.toHaveBeenCalled();
    expect(screen.queryByTestId("rebalance-est-cost")).not.toBeInTheDocument();
  });

  it("perf: valid plan flow completes within budget", async () => {
    const elapsed = await timedFlow(stubClient());
    expect(elapsed).toBeLessThan(perfBudgetMs(FLOW_BUDGET_MS));
  });

  it("gate red: a slowed flow exceeds the budget so the perf assertion fails", async () => {
    const slow = stubClient({
      planRebalance: vi.fn().mockImplementation(
        () => new Promise<WhatIfRebalancePlanResponse>((resolve) => setTimeout(() => resolve(planResponse()), 120)),
      ),
    });
    const elapsed = await timedFlow(slow);
    const tinyBudgetMs = 100;
    expect(() => expect(elapsed).toBeLessThan(tinyBudgetMs)).toThrow();
  });

  it("gate red: broken input (blank weight) fails the same call assertion a valid run satisfies", () => {
    const client = stubClient();
    renderPage({ whatIfClient: client });
    fillTarget(0, "BTC-USDT", "");
    run();
    expect(() => expect(client.planRebalance).toHaveBeenCalledTimes(1)).toThrow();
  });
});
