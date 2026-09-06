import "@testing-library/jest-dom/vitest";
import { ApiError, type CandleQueryResult, type InstrumentListResult } from "@aios/api-client";
import type { CandleRecord, SeriesKey } from "@aios/shared-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CompareSymbols,
  computeComparison,
  isDuplicateCompareSymbol,
  type FetchCompareCandles,
  type ListCompareInstruments,
} from "./CompareSymbols";

// ChartPage.test.tsx와 동일 관용: ErrorMessage는 err instanceof ApiError로
// errorCode를 뽑으므로(err.message 직접 렌더 금지) 실제 ApiError 인스턴스로만 검증한다.
function apiErrorLike(statusCode: number, errorCode: string): ApiError {
  return new ApiError(statusCode, errorCode, undefined, errorCode);
}

const BASE_KEY: SeriesKey = { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" };
const OTHER_KEY: SeriesKey = { venue: "BITGET", instrument_id: "ETHUSDT", timeframe: "1h" };

function candle(key: SeriesKey, hourOffset: number, close: string): CandleRecord {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  const close_time = new Date(Date.UTC(2026, 8, 3, hourOffset + 1, 0, 0));
  return {
    key,
    open_time: open.toISOString(),
    close_time: close_time.toISOString(),
    open: close,
    high: close,
    low: close,
    close,
    volume: "1",
    quote_volume: null,
  };
}

function okResult(candles: CandleRecord[]): CandleQueryResult {
  return {
    series: { kind: "ok", value: { key: OTHER_KEY, candles, gaps: [], adjustment: "RAW", as_of: "2026-09-03T05:00:00Z", series_hash: "h" } },
    quality: null,
  };
}

afterEach(cleanup);

function instrumentView(instrumentId: string) {
  return {
    kind: "ok" as const,
    value: {
      instrument_id: instrumentId,
      venue: "BITGET" as const,
      canonical_symbol: instrumentId,
      venue_symbol: instrumentId,
      asset_class: "CRYPTO" as const,
      base: null,
      quote: null,
      tick_size: "0.01",
      lot_size: "0.001",
      status: "LISTED" as const,
      listed_at: "2020-01-01T00:00:00Z",
      delisted_at: null,
    },
  };
}

function renderCompare(
  fetchCandles: FetchCompareCandles,
  baseCandles: CandleRecord[] = [candle(BASE_KEY, 0, "100"), candle(BASE_KEY, 1, "105")],
  listInstruments: ListCompareInstruments = async () => ({ items: [], nextCursor: null }),
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <CompareSymbols
        baseVenue="BITGET"
        baseInstrumentId="BTCUSDT"
        baseCandles={baseCandles}
        timeframe="1h"
        start="2026-09-03T00:00:00Z"
        end="2026-09-03T05:00:00Z"
        compareSymbols={[{ instrumentId: "ETHUSDT", venue: "BITGET" }]}
        onAdd={vi.fn()}
        onRemove={vi.fn()}
        fetchCandles={fetchCandles}
        listInstruments={listInstruments}
      />
    </QueryClientProvider>,
  );
}

describe("CompareSymbols", () => {
  it("겹치는 구간이 있으면 정규화 오버레이·스프레드 페인을 보여준다", async () => {
    const fetchCandles = vi.fn(async () => okResult([candle(OTHER_KEY, 0, "50"), candle(OTHER_KEY, 1, "55")]));
    renderCompare(fetchCandles);

    const pane = await screen.findByTestId("compare-pane-ETHUSDT");
    expect(await within(pane).findByText(/정규화 오버레이/)).toBeInTheDocument();
    expect(within(pane).getByText(/스프레드/)).toBeInTheDocument();
  });

  it("negative: 비교 심볼 캔들 조회가 404면 ErrorMessage로만 노출한다", async () => {
    const fetchCandles = vi.fn(async () => {
      throw apiErrorLike(404, "RESOURCE_NOT_FOUND");
    });
    renderCompare(fetchCandles);

    const pane = await screen.findByTestId("compare-pane-ETHUSDT");
    expect(await within(pane).findByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument();
  });

  it("negative: 비교 심볼 캔들 조회가 403이면 ErrorMessage로만 노출한다", async () => {
    const fetchCandles = vi.fn(async () => {
      throw apiErrorLike(403, "AUTHZ_FORBIDDEN");
    });
    renderCompare(fetchCandles);

    const pane = await screen.findByTestId("compare-pane-ETHUSDT");
    expect(await within(pane).findByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument();
  });

  it("negative: 교집합이 0봉이면 빈 상태 문구를 보여준다", async () => {
    // base는 9/3 0~1시, other는 9/10(멀리 떨어진 구간) — alignSeries가 no_overlap.
    const farOther = candle(OTHER_KEY, 24 * 7, "50");
    const fetchCandles = vi.fn(async () => okResult([farOther]));
    renderCompare(fetchCandles);

    const pane = await screen.findByTestId("compare-pane-ETHUSDT");
    expect(await within(pane).findByText("겹치는 캔들이 없습니다.")).toBeInTheDocument();
  });

  it("negative: 기준 심볼과 같은 심볼을 비교 심볼로 추가하면 거부된다", () => {
    const existing = [{ instrumentId: "ETHUSDT", venue: "BITGET" as const }];
    expect(isDuplicateCompareSymbol(existing, "BITGET", "BTCUSDT", { instrumentId: "BTCUSDT", venue: "BITGET" })).toBe(true);
    expect(isDuplicateCompareSymbol(existing, "BITGET", "BTCUSDT", { instrumentId: "ETHUSDT", venue: "BITGET" })).toBe(true);
    expect(isDuplicateCompareSymbol(existing, "BITGET", "BTCUSDT", { instrumentId: "SOLUSDT", venue: "BITGET" })).toBe(false);
  });

  it("negative: 추가 폼에서 기준·이미 추가된 심볼은 선택지에서 빠진다", async () => {
    const fetchCandles = vi.fn(async () => okResult([candle(OTHER_KEY, 0, "50"), candle(OTHER_KEY, 1, "55")]));
    const listInstruments = vi.fn(async (): Promise<InstrumentListResult> => ({
      items: [instrumentView("BTCUSDT"), instrumentView("ETHUSDT"), instrumentView("SOLUSDT")],
      nextCursor: null,
    }));
    renderCompare(fetchCandles, undefined, listInstruments);
    await screen.findByTestId("compare-pane-ETHUSDT");

    fireEvent.click(screen.getByRole("button", { name: "비교 심볼 추가" }));
    const instrumentSelect = (await screen.findByLabelText("비교 심볼 선택")) as HTMLSelectElement;
    await waitFor(() => expect(instrumentSelect.options.length).toBeGreaterThan(1));
    const optionValues = Array.from(instrumentSelect.options).map((o) => o.value);
    expect(optionValues).not.toContain("ETHUSDT");
    expect(optionValues).not.toContain("BTCUSDT");
    expect(optionValues).toContain("SOLUSDT");
  });
});

describe("computeComparison", () => {
  it("기준/비교 캔들이 겹치면 정규화 오버레이와 스프레드를 함께 반환한다", () => {
    const base = [candle(BASE_KEY, 0, "100"), candle(BASE_KEY, 1, "110")];
    const other = [candle(OTHER_KEY, 0, "50"), candle(OTHER_KEY, 1, "55")];
    const outcome = computeComparison(base, other);
    expect(outcome.kind).toBe("ok");
    if (outcome.kind === "ok") {
      expect(outcome.overlayBase[0]!.value).toBe(100);
      expect(outcome.spreadPoints[0]!.value).toBeCloseTo(0.5);
    }
  });

  it("negative: 어느 한쪽이 빈 시리즈면 empty를 반환한다", () => {
    expect(computeComparison([], [candle(OTHER_KEY, 0, "50")]).kind).toBe("empty");
    expect(computeComparison([candle(BASE_KEY, 0, "100")], []).kind).toBe("empty");
  });

  it("negative: 겹치는 구간이 없으면 empty를 반환한다", () => {
    const base = [candle(BASE_KEY, 0, "100")];
    const other = [candle(OTHER_KEY, 24 * 30, "50")];
    expect(computeComparison(base, other).kind).toBe("empty");
  });
});
