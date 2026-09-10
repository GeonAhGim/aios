import "@testing-library/jest-dom/vitest";
import { ApiError, type CandleQueryParams, type CandleQueryResult, type InstrumentListResult } from "@aios/api-client";
import type { CandleRecord, SeriesKey } from "@aios/shared-types";
import { alignSeries } from "@aios/chart-engine/src/compare/align";
import { normalizeToBase100, NormalizeError } from "@aios/chart-engine/src/compare/normalize";
import { spread } from "@aios/chart-engine/src/compare/spread";
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
  compareSymbols: { instrumentId: string; venue: "BITGET" | "KIS_KRX" | "KIS_US" }[] = [{ instrumentId: "ETHUSDT", venue: "BITGET" }],
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
        compareSymbols={compareSymbols}
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

  it("D3 다중 인스턴스: 비교 심볼 2개가 동시에 하나는 성공·하나는 404여도 서로의 페인을 교차오염하지 않는다", async () => {
    const fetchCandles = vi.fn(async (params: CandleQueryParams) => {
      if (params.instrumentId === "ETHUSDT") return okResult([candle(OTHER_KEY, 0, "50"), candle(OTHER_KEY, 1, "55")]);
      throw apiErrorLike(404, "RESOURCE_NOT_FOUND");
    });
    renderCompare(fetchCandles, undefined, undefined, [
      { instrumentId: "ETHUSDT", venue: "BITGET" },
      { instrumentId: "SOLUSDT", venue: "BITGET" },
    ]);

    const ethPane = await screen.findByTestId("compare-pane-ETHUSDT");
    const solPane = await screen.findByTestId("compare-pane-SOLUSDT");
    expect(await within(ethPane).findByText(/정규화 오버레이/)).toBeInTheDocument();
    expect(await within(solPane).findByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument();
    // 각 페인은 자신의 queryKey(instrumentId 포함)로만 조회한다 -- 한쪽 실패가 다른 쪽 표시로 새면 안 된다.
    expect(within(ethPane).queryByText("요청한 항목을 찾을 수 없습니다.")).not.toBeInTheDocument();
    expect(within(solPane).queryByText(/정규화 오버레이/)).not.toBeInTheDocument();
    expect(fetchCandles).toHaveBeenCalledTimes(2);
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

  it("성능: 캔들 5,000개씩 align+normalize+spread 계산이 1.5s 이내에 끝난다", () => {
    const base = Array.from({ length: 5000 }, (_, i) => candle(BASE_KEY, i, (100 + i * 0.01).toFixed(4)));
    const other = Array.from({ length: 5000 }, (_, i) => candle(OTHER_KEY, i, (50 + i * 0.005).toFixed(4)));
    const t0 = performance.now();
    const outcome = computeComparison(base, other);
    const elapsedMs = performance.now() - t0;
    expect(outcome.kind).toBe("ok");
    expect(elapsedMs).toBeLessThan(1500);
  });

  it("게이트 적색 재현: anchor를 find() 대신 정렬된 첫 원소로 naive하게 고르면, 실 구현이 ok로 처리하는 입력을 잘못 empty로 떨어뜨린다", () => {
    // 실 구현(computeComparison)은 aligned.points.find((p) => p.base && p.other)로
    // base·other 둘 다 존재하는 첫 지점을 anchor로 고른다. 이 guard를 걷어내고
    // "정렬된 첫 원소가 곧 anchor"라고 가정하는 naive 판정으로 되돌리면, 겹침
    // 구간의 첫 타임스탬프에 한쪽만 존재할 때 존재하지 않는 쪽의 anchor를 찾다가
    // NormalizeError(anchor_not_found)로 떨어져 정상 입력을 잘못 empty로 만든다.
    function naiveComputeComparison(base: readonly CandleRecord[], other: readonly CandleRecord[]) {
      const aligned = alignSeries(base, other);
      const anchor = aligned.points[0];
      if (!anchor) return { kind: "empty" as const };
      try {
        const overlayBase = normalizeToBase100(base, anchor.timeMs);
        const overlayOther = normalizeToBase100(other, anchor.timeMs);
        const spreadPoints = spread(aligned, "ratio");
        return { kind: "ok" as const, overlayBase, overlayOther, spreadPoints };
      } catch (err) {
        if (err instanceof NormalizeError) return { kind: "empty" as const };
        throw err;
      }
    }

    // base는 hour0,1,3만(hour2 결측), other는 hour2,3부터 시작 -- 겹침 구간 첫
    // 타임스탬프(hour2)는 other만 있고 base는 없다. find()는 이를 건너뛰어
    // hour3(둘 다 존재)을 anchor로 고른다; 정렬된 첫 원소는 hour2다.
    const base: CandleRecord[] = [candle(BASE_KEY, 0, "100"), candle(BASE_KEY, 1, "110"), candle(BASE_KEY, 3, "130")];
    const other: CandleRecord[] = [candle(OTHER_KEY, 2, "44"), candle(OTHER_KEY, 3, "46")];

    const real = computeComparison(base, other);
    const naive = naiveComputeComparison(base, other);

    expect(real.kind).toBe("ok");
    expect(naive.kind).toBe("empty");
  });
});
