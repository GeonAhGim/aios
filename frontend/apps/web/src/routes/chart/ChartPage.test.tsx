import "@testing-library/jest-dom/vitest";
import type { CandleQueryResult } from "@aios/api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartPage, type FetchCandles } from "./ChartPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

// CandlesPage.test.tsx와 동일한 관용: lightweight-charts는 jsdom에서 canvas를
// 요구해 렌더링을 스텁으로 바꾼다(실제 렌더링은 유닛 테스트 영역이 아님).
vi.mock("@aios/ui-web", async () => {
  const actual = await vi.importActual<typeof import("@aios/ui-web")>("@aios/ui-web");
  return {
    ...actual,
    CandlestickChart: ({ data }: { data: unknown[] }) => (
      <div data-testid="candlestick-chart">캔들 {data.length}개</div>
    ),
  };
});

afterEach(cleanup);

const KEY = { venue: "BITGET" as const, instrument_id: "BTCUSDT", timeframe: "1h" as const };

function candle(hourOffset: number) {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  const close = new Date(Date.UTC(2026, 8, 3, hourOffset + 1, 0, 0));
  return {
    key: KEY,
    open_time: open.toISOString(),
    close_time: close.toISOString(),
    open: "50000.00",
    high: "50500.00",
    low: "49800.00",
    close: "50200.00",
    volume: "12.5",
    quote_volume: "628500.00",
  };
}

function okResult(): CandleQueryResult {
  return {
    series: {
      kind: "ok",
      value: {
        key: KEY,
        candles: [candle(0), candle(1), candle(2)],
        gaps: [],
        adjustment: "RAW",
        as_of: new Date(Date.UTC(2026, 8, 3, 5, 0, 0)).toISOString(),
        series_hash: "deadbeef",
      },
    },
    quality: null,
  };
}

function renderPage(fetchCandles: FetchCandles, instrumentId: string | null = "BTCUSDT") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const entry = instrumentId === null ? "/chart" : `/chart?instrument_id=${instrumentId}`;
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ChartPage fetchCandles={fetchCandles} now={new Date("2026-09-03T05:02:00Z")} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ChartPage", () => {
  it("정상 조회 시 CH-2 candleStream에 병합해 캔들스틱 차트를 보여준다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);

    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));
    expect(fetchCandles).toHaveBeenCalledWith(
      expect.objectContaining({ venue: "BITGET", instrumentId: "BTCUSDT", timeframe: "1h" }),
    );
  });

  it("negative: instrument_id 없이 진입하면 fetch하지 않고 안내만 보여준다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles, null);

    expect(await screen.findByText(/심볼을 먼저 선택하세요/)).toBeInTheDocument();
    expect(fetchCandles).not.toHaveBeenCalled();
  });

  it("CH-7 재생 컨트롤의 다음 봉 버튼을 누르면 visible 구간만 보여준다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: "다음 봉" }));
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 1개"));

    fireEvent.click(screen.getByRole("button", { name: "다음 봉" }));
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 2개"));
  });

  it("CH-4 그리기 도구로 그리기를 추가·삭제할 수 있다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: "수평선" }));
    fireEvent.click(screen.getByRole("button", { name: "그리기 추가" }));

    expect(await screen.findByText(/수평선 @/)).toBeInTheDocument();
    expect(screen.getByText("그리기 (1)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(screen.queryByText(/수평선 @/)).not.toBeInTheDocument();
    expect(screen.getByText("그리기 (0)")).toBeInTheDocument();
  });

  it("negative: 그리기 도구를 고르지 않으면 추가 버튼이 비활성화된다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    expect(screen.getByRole("button", { name: "그리기 추가" })).toBeDisabled();
  });

  it("CH-3 지표 선택기로 지표를 고르면 선택 칩이 나타난다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);
    await waitFor(() => expect(screen.getByTestId("candlestick-chart")).toHaveTextContent("캔들 3개"));

    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));
    fireEvent.click(screen.getByRole("option", { name: /^SMA/ }));

    expect(await screen.findByRole("button", { name: "SMA ✕" })).toBeInTheDocument();
  });
});
