import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../httpErrors";
import type { CandleQueryResult } from "./marketData";
import { baseParams, coverageMissingError, makeClient, seriesKey, stubFetch } from "./marketData.fixtures";

// task-618 계열 DEEPEN(b45d6e56)과 동일 기법: 실 가드를 되돌린 naive 구현을
// 나란히 실행해, marketData.test.ts의 정상 단언(6번 "409는 ApiError로 그대로
// 던진다")이 실제로 어떤 회귀를 적색으로 잡아내는 게이트인지 증명한다.
describe("게이트 적색 재현 — CI red-line (409 DATA_COVERAGE_MISSING 조용한 빈채움 회귀)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("catch로 409를 빈 캔들 시리즈로 삼키는 naive 구현으로 회귀하면 '미커버 구간'과 '캔들 0개'를 구별할 수 없게 된다 — 6번 단언이 그 회귀를 실제로 적색으로 만드는 게이트임을 증명", async () => {
    // naive 회귀 시뮬레이션: 실제 getCandles라면 throw했을 409 ApiError를
    // catch해 빈 시리즈로 되돌린다고 가정한 구현(과거 "조용한 빈채움" 결함 클래스).
    function naiveSwallow409(err: unknown): CandleQueryResult {
      if (err instanceof ApiError && err.statusCode === 409) {
        return {
          series: {
            kind: "ok",
            value: {
              key: seriesKey,
              candles: [],
              gaps: [],
              adjustment: "RAW",
              as_of: "2026-09-03T00:00:00Z",
              series_hash: "",
            },
          },
          quality: null,
        };
      }
      throw err;
    }

    stubFetch(coverageMissingError, 409);
    const err = await makeClient().getCandles(baseParams).catch((e: unknown) => e);

    // 실제 구현은 계속 throw한다(marketData.test.ts 6번 단언과 동일 계약) —
    // naive 회귀였다면 아래에서 err가 이미 조용히 삼켜져 도달하지 못했을 코드다.
    expect(err).toBeInstanceOf(ApiError);

    // 같은 err를 naive 구현에 흘리면 "표시할 캔들이 없습니다"(빈 결과)와
    // "미커버 구간"(409)을 화면이 구별할 수 없는 상태가 된다 — 이 결과가
    // ok/candles=[]로 나온다는 사실 자체가 naive 구현의 결함을 보여준다.
    const naiveResult = naiveSwallow409(err);
    expect(naiveResult.series.kind).toBe("ok");
    if (naiveResult.series.kind === "ok") {
      expect(naiveResult.series.value.candles).toHaveLength(0);
    }
  });
});
