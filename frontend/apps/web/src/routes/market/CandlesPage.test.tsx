import "@testing-library/jest-dom/vitest";
import { ApiError } from "@aios/api-client";
import type { CandleQueryResult } from "@aios/api-client";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CandlesPage, type FetchCandles } from "./CandlesPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: null }) },
}));

// lightweight-charts는 canvas·matchMedia 등 jsdom이 지원하지 않는 브라우저 API에
// 의존한다(fancy-canvas) — 실제 차트 렌더링은 유닛 테스트 영역이 아니므로
// CandlestickChart만 stub으로 바꾸고 나머지 ui-web export는 그대로 둔다.
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

const CANDLE = {
  key: KEY,
  open_time: "2026-09-03T00:00:00Z",
  close_time: "2026-09-03T01:00:00Z",
  open: "50000.00",
  high: "50500.00",
  low: "49800.00",
  close: "50200.00",
  volume: "12.5",
  quote_volume: "628500.00",
};

function okResult(overrides: { candles?: typeof CANDLE[]; gaps?: [string, string][] } = {}): CandleQueryResult {
  return {
    series: {
      kind: "ok",
      value: {
        key: KEY,
        candles: overrides.candles ?? [CANDLE],
        gaps: overrides.gaps ?? [],
        adjustment: "RAW",
        as_of: "2026-09-03T05:00:00Z",
        series_hash: "deadbeef",
      },
    },
    quality: { kind: "ok", value: { verdict: "ACCEPT", accepted: 1, quarantined: 0, rejected: 0, issues: [] } },
  };
}

function renderPage(fetchCandles: FetchCandles, now?: Date, instrumentId: string | null = "BTCUSDT") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const entry = instrumentId === null ? "/market/candles" : `/market/candles?instrument_id=${instrumentId}`;
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <CandlesPage fetchCandles={fetchCandles} now={now} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CandlesPage", () => {
  it("정상 캔들 조회 시 심볼·타임프레임으로 fetch하고 품질 배지·신선도를 보여준다", async () => {
    const now = new Date("2026-09-03T05:02:00Z");
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles, now);

    await waitFor(() => expect(screen.getByTestId("candle-quality-badge")).toBeInTheDocument());
    expect(fetchCandles).toHaveBeenCalledWith(
      expect.objectContaining({ venue: "BITGET", instrumentId: "BTCUSDT", timeframe: "1h" }),
    );
    expect(screen.getByText("기준 시각 · 2분 전")).toBeInTheDocument();
  });

  it("negative: 빈 시리즈면 조용히 숨기지 않고 표시할 캔들이 없다는 안내를 보여준다", async () => {
    const fetchCandles = vi.fn(async () => okResult({ candles: [] }));
    renderPage(fetchCandles);

    await waitFor(() => expect(screen.getByText("표시할 캔들이 없습니다.")).toBeInTheDocument());
    expect(screen.getByTestId("candle-quality-badge")).toBeInTheDocument();
  });

  it("negative: REJECT 판정이면 조용히 숨기지 않고 CandleQualityBadge로 사유를 노출한다", async () => {
    const fetchCandles = vi.fn(async () => ({
      series: okResult().series,
      quality: {
        kind: "ok" as const,
        value: {
          verdict: "REJECT" as const,
          accepted: 0,
          quarantined: 0,
          rejected: 1,
          issues: [{ type: "OHLC_INCONSISTENT" as const, severity: "REJECT" as const, open_time: CANDLE.open_time, detail: {} }],
        },
      },
    }));
    renderPage(fetchCandles);

    const badge = await screen.findByTestId("verdict-badge");
    expect(badge).toHaveTextContent("REJECT");
    expect(badge).toHaveTextContent("OHLC_INCONSISTENT(REJECT)");
  });

  it("negative: 404 RESOURCE_NOT_FOUND면 routeApiError+ErrorMessage 경로로 매핑된 문구를 보여주고 원본 message는 노출하지 않는다", async () => {
    const fetchCandles = vi.fn(async () => {
      throw new ApiError(404, "internal detail not for users", "trace-1", "RESOURCE_NOT_FOUND");
    });
    renderPage(fetchCandles);

    await waitFor(() => expect(screen.getByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument());
    expect(screen.queryByText("internal detail not for users")).not.toBeInTheDocument();
  });

  // task-1057 §3.3 5xx: EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY(503)는 재시도
  // 버튼을 보여주고, EXCHANGE_FATAL(502)은 재시도 없이 안내만 한다(task-937 규약
  // 재사용 — classifyServerError를 감싼 routeApiError로만 판정, 새 분류기 없음).
  it("negative: EXCHANGE_UNAVAILABLE(503)은 다시 시도 버튼과 함께 매핑된 문구를 보여준다", async () => {
    const fetchCandles = vi.fn(async () => {
      throw new ApiError(503, "raw server detail", "trace-503", "EXCHANGE_UNAVAILABLE");
    });
    renderPage(fetchCandles);

    await waitFor(() =>
      expect(
        screen.getByText("거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeEnabled();
  });

  it("negative: EXCHANGE_FATAL(502)은 재시도 버튼 없이 매핑된 문구만 보여준다", async () => {
    const fetchCandles = vi.fn(async () => {
      throw new ApiError(502, "raw server detail", "trace-502", "EXCHANGE_FATAL");
    });
    renderPage(fetchCandles);

    await waitFor(() =>
      expect(screen.getByText("거래소 자격증명을 확인해주세요.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  // task-1088: instrument_id 없이 직접 진입하면 자유입력으로 폴백하지 않고
  // InstrumentsPage로 가는 안내만 보여준다(task-837 결함의 정식 해소).
  it("negative: instrument_id 없이 진입하면 자유입력 없이 심볼 선택 안내만 보여준다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles, undefined, null);

    expect(await screen.findByText(/심볼을 먼저 선택하세요/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "심볼 목록으로 이동" })).toHaveAttribute("href", "/market/instruments");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(fetchCandles).not.toHaveBeenCalled();
  });

  it("instrument_id는 쿼리스트링에서 읽어 읽기 전용으로 보여주고 자유입력 필드는 없다", async () => {
    const fetchCandles = vi.fn(async () => okResult());
    renderPage(fetchCandles);

    await waitFor(() => expect(screen.getByTestId("candles-instrument-id")).toHaveTextContent("BTCUSDT"));
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
});
