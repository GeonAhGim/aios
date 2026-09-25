import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../httpErrors";
import { baseParams, jsonResponse, makeClient, TRACE_ID } from "./marketData.fixtures";

// DEPTH_LA_LB_LC(task-2723)가 원 task-1525(561a9e6)를 D3 축 하한 미달(D1)로
// 판정 — 모의 HTTP 오류 바디 외의 실 어댑터/네트워크 결함 시뮬레이션·수치 성능
// 단언·게이트 적색 재현이 비어 있었다(task-2996 DEEPEN). marketData.test.ts의
// "409는 ApiError로 그대로 던진다" 테스트는 "서버가 에러 바디를 내려줬다"만
// 흉내내지, 네트워크 자체가 끊기거나 응답 바디가 전송 중 깨지는 실제 결함
// 클래스는 다루지 않았다.
describe("failure-injection — LA-24 실 어댑터/네트워크 결함 시뮬레이션", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("네트워크 완전 단절(fetch 자체가 reject)이면 HTTP 에러 바디 없이도 원본 예외를 그대로 던진다(ApiError로 재분류·빈 시리즈로 뭉개지 않음)", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    const err = await makeClient().getCandles(baseParams).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(TypeError);
    expect(err).not.toBeInstanceOf(ApiError);
  });

  it("응답 바디가 전송 중 잘린 깨진 JSON(실 어댑터 결함 — 에러 봉투가 아니라 파싱 자체가 불가)이면 SyntaxError를 그대로 던진다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{"data": {"key":', {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(makeClient().getCandles(baseParams)).rejects.toThrow(SyntaxError);
  });

  it("응답 바디가 완전히 빈 문자열(연결이 중간에 끊긴 실 결함)이면 봉투 형식 위반으로 던지고 빈 시리즈로 채우지 않는다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(makeClient().getCandles(baseParams)).rejects.toThrow(/봉투 형식/);
  });

  it("GET 재시도 소진: 503이 반복되면 정책된 백오프(1초·2초, httpErrors.ts SERVER_ERROR_BACKOFF_SEC)를 실제로 다 태우고도 여전히 실패해 ApiError를 던진다(무한 재시도 아님)", async () => {
    vi.useFakeTimers();
    const errorBody = {
      error_code: "EXCHANGE_UNAVAILABLE",
      message: "거래소 연결 불가",
      details: {},
      trace_id: TRACE_ID,
      retry_after_seconds: null,
    };
    const fetchMock = vi.fn().mockImplementation(async () => jsonResponse(503, errorBody));
    vi.stubGlobal("fetch", fetchMock);

    const resultPromise = makeClient().getCandles(baseParams).catch((e: unknown) => e);
    // 정책상 재시도는 최초 1회 + 백오프 2회(1초, 2초) 상한 — 3초를 넘게 흘려보내도
    // 4번째 호출은 없어야 한다(무한 재시도 방지 확인).
    await vi.advanceTimersByTimeAsync(5_000);
    const err = await resultPromise;

    expect(err).toBeInstanceOf(ApiError);
    if (err instanceof ApiError) expect(err.statusCode).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
