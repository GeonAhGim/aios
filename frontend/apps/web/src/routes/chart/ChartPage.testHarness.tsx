import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { ApiError, type CandleQueryResult } from "@aios/api-client";
import type { ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, vi } from "vitest";
import { ChartPage, type ChartPageProps, type FetchCandles, type FetchCoverage } from "./ChartPage";

const CANDLE_COUNT_LABEL = "캔들 {count}개";

// Shared ChartPage test environment: network stubs, candle fixtures and providers.
// ChartPage는 restore/save 에러를 ApiError instanceof로 판별해 errorCode/traceId를
// 뽑는다(query.error와 동일 관용) — 던지는 값이 실제 ApiError 인스턴스여야 한다.
export function apiErrorLike(statusCode: number, errorCode: string): ApiError {
  return new ApiError(statusCode, errorCode, undefined, errorCode);
}

export function fakeChartingPort(overrides: Partial<ChartingPort> = {}): ChartingPort {
  return {
    createLayout: vi.fn(),
    listLayouts: vi.fn(async () => []),
    getLayout: vi.fn(),
    updateLayout: vi.fn(),
    deleteLayout: vi.fn(),
    getDrawings: vi.fn(),
    putDrawings: vi.fn(),
    ...overrides,
  };
}

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
  // StrategyMarkers(task-1569)의 실행 선택 전에는 positions.list를 요청하지 않으므로
  // 이 화면 테스트에서는 실행 목록이 비어 있어도 무방하다.
  useExecutions: () => ({ data: [] }),
  // CH-9: ChartToolbar가 마운트하는 AlertFromChart가 필요로 한다 — 이 스위트는
  // 알림 다이얼로그 자체를 검증하지 않으므로(ChartToolbar.test.tsx 몫) 최소 스텁만 준다.
  useCreateAlert: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

// CandlesPage.test.tsx와 동일한 관용: lightweight-charts는 jsdom에서 canvas를
// 요구해 렌더링을 스텁으로 바꾼다(실제 렌더링은 유닛 테스트 영역이 아님).
vi.mock("@aios/ui-web", async () => {
  const actual = await vi.importActual<typeof import("@aios/ui-web")>("@aios/ui-web");
  return {
    ...actual,
    CandlestickChart: ({ data }: { data: unknown[] }) => {
      const candleCountLabel = CANDLE_COUNT_LABEL.replace("{count}", String(data.length));
      return <div data-testid="candlestick-chart">{candleCountLabel}</div>;
    },
  };
});

// IND-14(task-1915): IndicatorPicker 기본값은 createIndicatorsClient가 만드는 실제
// fetch 클라이언트다 — 이 화면 테스트가 "지표 선택" 버튼을 열 때 실제 네트워크를
// 타지 않도록 listIndicators만 스텁으로 바꾼다(ApiError 등 나머지 export는 실제 그대로).
vi.mock("@aios/api-client", async () => {
  const actual = await vi.importActual<typeof import("@aios/api-client")>("@aios/api-client");
  return {
    ...actual,
    createIndicatorsClient: () => ({
      listIndicators: vi.fn(async () => ({
        items: [
          {
            name: "SMA",
            tier: "core" as const,
            category: "Overlap Studies",
            version: "1",
            hash: "h",
            inputs: ["close"],
            outputs: ["value"],
          },
        ],
        nextCursor: null,
      })),
    }),
  };
});

afterEach(cleanup);

const KEY = { venue: "BITGET" as const, instrument_id: "BTCUSDT", timeframe: "1h" as const };

export function candle(hourOffset: number) {
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

export function okResult(): CandleQueryResult {
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

// DC-18b: coverageQuery는 candles와 같은 enabled 조건(instrumentId 있으면 즉시)이라
// fetchCandles와 동일하게 실 네트워크를 피하려면 이 스위트는 항상 스텁을 넘겨야
// 한다 — 미지정 시 빈 배열(getCoverage의 no-coverage 응답과 같은 모양)을 준다.
export const defaultFetchCoverage: FetchCoverage = async () => [];

export function renderPage(
  fetchCandles: FetchCandles,
  instrumentId: string | null = "BTCUSDT",
  chartingPort: ChartingPort = fakeChartingPort(),
  listInstruments?: ChartPageProps["listInstruments"],
  fetchCoverage: FetchCoverage = defaultFetchCoverage,
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const entry = instrumentId === null ? "/chart" : `/chart?instrument_id=${instrumentId}`;
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ChartPage
          fetchCandles={fetchCandles}
          fetchCoverage={fetchCoverage}
          chartingPort={chartingPort}
          listInstruments={listInstruments}
          now={new Date("2026-09-03T05:02:00Z")}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// CH-18b: parityCheck.ts 기반 클라이언트/서버 지표값 대조·폴백 화면 배선.
// 종가를 전부 50200으로 고정해(candle() 헬퍼) SMA(20)의 기대값을 손으로 계산할
// 필요 없이 정확히 50200으로 만든다 — timeperiod=20에 걸리도록 25개 봉을 만든다.
export function manyCandles(count: number) {
  return Array.from({ length: count }, (_, i) => candle(i));
}

export function okResultWithCandles(candles: ReturnType<typeof candle>[]): CandleQueryResult {
  return {
    series: {
      kind: "ok",
      value: {
        key: KEY,
        candles,
        gaps: [],
        adjustment: "RAW",
        as_of: new Date(Date.UTC(2026, 8, 4, 5, 0, 0)).toISOString(),
        series_hash: "deadbeef",
      },
    },
    quality: null,
  };
}

