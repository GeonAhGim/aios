import "./ChartPage.testHarness";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ChartPage, type ChartPageProps, type FetchCandles } from "./ChartPage";
import { defaultFetchCoverage, fakeChartingPort, manyCandles, okResultWithCandles } from "./ChartPage.testHarness";

function smaVerifiedCatalog(): IndicatorCatalogEntry[] {
  const pin = VERIFIED_KERNEL_PINS.SMA!;
  return [{ name: "SMA", tier: pin.tier, category: "core", version: "ind-v1", hash: pin.entryHash, inputs: ["close"], outputs: ["value"] }];
}

function serverSmaSeries(count: number, lastValue: number) {
  return { value: Array.from({ length: count }, (_, i) => (i < 19 ? null : lastValue)) };
}

describe("ChartPage — CH-18b 클라이언트/서버 지표 패리티 폴백", () => {
  async function selectSma(fetchCandles: FetchCandles, extraProps: Partial<ChartPageProps>) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/chart?instrument_id=BTCUSDT"]}>
          <ChartPage
            fetchCandles={fetchCandles}
            fetchCoverage={defaultFetchCoverage}
            chartingPort={fakeChartingPort()}
            now={new Date("2026-09-04T05:02:00Z")}
            {...extraProps}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 25개"));
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));
    fireEvent.click(await screen.findByRole("option", { name: /^SMA/ }));
  }

  it("서버 참조와 일치하면 클라이언트 계산값을 그대로 그린다", async () => {
    const candles = manyCandles(25);
    const fetchCandles = vi.fn(async () => okResultWithCandles(candles));
    const resolveServerIndicatorSeries = vi.fn(() => serverSmaSeries(25, 50200));

    await selectSma(fetchCandles, { indicatorCatalog: smaVerifiedCatalog(), resolveServerIndicatorSeries });

    await waitFor(() => expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("50200.000000"));
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)");
    expect(screen.queryByTestId("indicator-parity-fallback-SMA")).not.toBeInTheDocument();
  });

  it("negative: 서버 참조와 불일치하면 서버값으로 폴백하고, 폴백 사유가 표면화되며, 클라이언트 값은 잔존하지 않는다", async () => {
    const candles = manyCandles(25);
    const fetchCandles = vi.fn(async () => okResultWithCandles(candles));
    // 클라이언트 SMA(20) 실값은 50200 — 서버가 50200.00002를 돌려주면 절대오차
    // 2e-5 > PARITY_TOLERANCE(1e-9)로 반드시 불일치가 된다(무음 통과 불가).
    const resolveServerIndicatorSeries = vi.fn(() => serverSmaSeries(25, 50200.00002));

    await selectSma(fetchCandles, { indicatorCatalog: smaVerifiedCatalog(), resolveServerIndicatorSeries });

    await waitFor(() => expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("50200.000020"));
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(server)");
    expect(screen.getByTestId("indicator-parity-fallback-SMA")).toBeInTheDocument();
    // 클라이언트가 계산했을 값(50200.000000)이 화면 어디에도 남아있지 않아야 한다.
    expect(screen.queryByText("50200.000000")).not.toBeInTheDocument();
  });

  it("서버 참조가 아직 없으면(포트 미배선) 미검증 상태로 fail-closed하고 숫자를 그리지 않는다", async () => {
    const candles = manyCandles(25);
    const fetchCandles = vi.fn(async () => okResultWithCandles(candles));

    await selectSma(fetchCandles, { indicatorCatalog: smaVerifiedCatalog() });

    await waitFor(() => expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("--"));
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(unverified)");
  });
});

