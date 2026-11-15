import "./ChartPage.testHarness";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { perfBudgetMs } from "../../test/perfBudget";
import { manyCandles, okResult, okResultWithCandles, renderPage } from "./ChartPage.testHarness";

describe("ChartPage depth1 (D2)", () => {
  // Local measurement: ~80ms to render 200 candles; budget 4000ms is >30x headroom
  // (absorbs shared-CI contention) yet still fails a quadratic-or-worse regression.
  it("perf: 200 candles render within the time budget", async () => {
    const fetchCandles = vi.fn(async () => okResultWithCandles(manyCandles(200)));
    const startedAt = performance.now();
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("200"));
    const elapsed = performance.now() - startedAt;
    expect(elapsed).toBeLessThan(perfBudgetMs(4000));
  });

  // gate red: a delayed dependency makes the very same budget assertion fail.
  it("gate red: the same time-budget assertion fails when the candle fetch is delayed", async () => {
    const fetchCandles = vi.fn(
      () => new Promise<ReturnType<typeof okResult>>((resolve) => setTimeout(() => resolve(okResult()), 150)),
    );
    const startedAt = performance.now();
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("3"));
    const elapsed = performance.now() - startedAt;
    expect(() => expect(elapsed).toBeLessThan(100)).toThrow();
  });

  it("negative: without instrument_id nothing is fetched and no chart renders", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles, null);
    expect(await screen.findByText(/심볼을 먼저 선택하세요/)).toBeInTheDocument();
    expect(fetchCandles).not.toHaveBeenCalled();
    expect(screen.queryByTestId("candlestick-chart")).not.toBeInTheDocument();
  });

  it("failure injection: a rejected candle fetch renders no chart", async () => {
    const fetchCandles = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage(fetchCandles);
    await waitFor(() => expect(fetchCandles).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("candlestick-chart")).not.toBeInTheDocument();
  });

  it("negative: a never-resolving fetch rejects the chart wait", async () => {
    const fetchCandles = vi.fn(() => new Promise<ReturnType<typeof okResult>>(() => undefined));
    renderPage(fetchCandles);
    await expect(screen.findByTestId("candlestick-chart", {}, { timeout: 100 })).rejects.toThrow();
  });
});
