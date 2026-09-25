import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../httpErrors";
import type { CoverageQueryParams } from "./marketData";
import { coverageParams, coverageSpanRaw, envelope, makeClient, requestUrl, stubFetch, TRACE_ID } from "./marketData.fixtures";

// task-2196(DC-18b): DC-6 contracts/v2/coverage.py CoverageSpan 목록. 이
// 클라이언트는 지금껏 테스트가 없었다(D3 하한 미달 사유) — 정상 매핑 1건 +
// 계약 위반(예외/reject) 경로를 별도로 검증한다.
describe("getCoverage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("정상 응답: 실응답 CoverageSpan 목록을 camelCase로 매핑하고 경로·쿼리를 그대로 싣는다", async () => {
    const fetchMock = stubFetch(envelope([coverageSpanRaw]));

    const result = await makeClient().getCoverage(coverageParams);

    expect(result).toEqual([
      {
        instrumentId: coverageSpanRaw.instrument_id,
        venue: "BITGET",
        assetClass: "CRYPTO",
        timeframe: "1h",
        qualityGrade: "VALIDATED",
        startAt: "2026-09-03T00:00:00Z",
        endAt: "2026-09-03T04:00:00Z",
      },
    ]);
    expect(requestUrl(fetchMock)).toBe(
      `https://api.example.test/v1/foundation/market-data/coverage?instrument_id=${coverageSpanRaw.instrument_id}&venue=BITGET&timeframe=1h`,
    );
  });

  it("no-coverage/no-entitlement: 서버가 200+[]로 접으면 throw하지 않고 빈 배열을 그대로 돌려준다", async () => {
    stubFetch(envelope([]));

    await expect(makeClient().getCoverage(coverageParams)).resolves.toEqual([]);
  });

  it("negative: 미지 timeframe은 요청 전에 reject하고 fetch를 호출하지 않는다", async () => {
    const fetchMock = stubFetch(envelope([coverageSpanRaw]));
    const client = makeClient();

    await expect(
      client.getCoverage({ ...coverageParams, timeframe: "2h" as CoverageQueryParams["timeframe"] }),
    ).rejects.toThrow(/timeframe/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("negative: quality_grade가 계약 밖 값(WHITELIST에 없는 문자열)인 span은 0으로 채우지 않고 통째로 버린다", async () => {
    const result = await (async () => {
      stubFetch(envelope([coverageSpanRaw, { ...coverageSpanRaw, quality_grade: "PLATINUM" }]));
      return makeClient().getCoverage(coverageParams);
    })();

    expect(result).toHaveLength(1);
    expect(result[0].qualityGrade).toBe("VALIDATED");
  });

  it("negative: 필드가 누락된 span(start_at 없음)도 통째로 버린다(0/빈문자열로 채우지 않음)", async () => {
    const { start_at: _omit, ...withoutStartAt } = coverageSpanRaw;
    stubFetch(envelope([withoutStartAt]));

    const result = await makeClient().getCoverage(coverageParams);

    expect(result).toEqual([]);
  });

  it("negative: data가 배열이 아니면(계약 위반 형태) throw하지 않고 빈 배열로 접는다", async () => {
    stubFetch(envelope({ items: [coverageSpanRaw] }));

    await expect(makeClient().getCoverage(coverageParams)).resolves.toEqual([]);
  });

  it("negative: 5xx 에러는 ApiError로 그대로 던진다(빈 배열로 뭉개 미커버 판정을 숨기지 않음)", async () => {
    stubFetch({ error_code: "INTERNAL", message: "boom", details: {}, trace_id: TRACE_ID, retry_after_seconds: null }, 500);

    const err = await makeClient().getCoverage(coverageParams).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    if (err instanceof ApiError) {
      expect(err.statusCode).toBe(500);
    }
  });
});
