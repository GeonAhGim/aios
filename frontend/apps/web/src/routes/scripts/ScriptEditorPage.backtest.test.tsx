import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { QuickBacktestResultView } from "@aios/api-client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScriptEditorPage, type CompileScript, type RunScriptBacktest } from "./ScriptEditorPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  apiClient: { compileScript: vi.fn() },
}));

afterEach(cleanup);

const COMPILE_RESULT = {
  scriptHash: "a".repeat(64),
  grammarVersion: "aios-script-1",
  irVersion: "aios-ir-1",
  registryVersion: "reg-v1",
  irSha256: "b".repeat(64),
  instrCount: 5,
  resources: { seriesCount: 1, lookbackTotal: 14, opCount: 3, callCount: 1, callDepth: 1, plotCount: 1 },
  elapsedMs: 12,
};

const BACKTEST_RESULT: QuickBacktestResultView = {
  fills: [],
  equityCurve: ["10000", "10500"],
  finalEquity: "10500",
  cash: "10500",
  positionQuantity: "0",
  fundingCost: "0",
  borrowCost: "0",
  bars: 1234,
  expiredOrders: 0,
  warnings: [],
};

async function renderCompiled(runBacktest?: RunScriptBacktest, url = "/scripts") {
  const compileScript: CompileScript = vi.fn(async () => COMPILE_RESULT);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[url]}>
        <ScriptEditorPage compileScript={compileScript} runBacktest={runBacktest} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "컴파일" }));
  await screen.findByTestId("compile-run-backtest");
}

describe("ScriptEditorPage backtest CTA", () => {
  it("shows a summary after a successful backtest", async () => {
    const runBacktest = vi.fn(async () => BACKTEST_RESULT);
    await renderCompiled(runBacktest);
    expect(screen.getByTestId("compile-open-chart")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("compile-run-backtest"));
    const summary = await screen.findByTestId("compile-backtest-summary");
    expect(summary).toHaveTextContent("10500");
    expect(summary).toHaveTextContent("1234");
    expect(runBacktest).toHaveBeenCalledTimes(1);
  });

  it("disables the button while the backtest is running", async () => {
    let resolve!: (v: QuickBacktestResultView) => void;
    const runBacktest = vi.fn(() => new Promise<QuickBacktestResultView>((r) => (resolve = r)));
    await renderCompiled(runBacktest);
    fireEvent.click(screen.getByTestId("compile-run-backtest"));
    await waitFor(() => expect(screen.getByTestId("compile-run-backtest")).toBeDisabled());
    resolve(BACKTEST_RESULT);
    await screen.findByTestId("compile-backtest-summary");
    expect(screen.getByTestId("compile-run-backtest")).toBeEnabled();
  });

  it("negative: shows a role=alert when the backtest fails", async () => {
    const runBacktest = vi.fn(async () => {
      throw new Error("backtest boom");
    });
    await renderCompiled(runBacktest);
    fireEvent.click(screen.getByTestId("compile-run-backtest"));
    expect(await screen.findByRole("alert")).toHaveTextContent("backtest boom");
    expect(screen.queryByTestId("compile-backtest-summary")).not.toBeInTheDocument();
  });

  it("negative: default runner rejects without an instrument_id", async () => {
    await renderCompiled(undefined);
    fireEvent.click(screen.getByTestId("compile-run-backtest"));
    expect(await screen.findByRole("alert")).toHaveTextContent("instrument_id");
  });
});
