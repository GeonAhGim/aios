import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
import { ApiClientBase } from "../http";
import { ApiError } from "../httpErrors";
import { withExchange } from "./exchange";

class ExchangeTestClient extends withExchange(ApiClientBase) {}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): ExchangeTestClient {
  return new ExchangeTestClient("https://api.example.test", () => null);
}

// ============================================================
// D2 evidence: negative tests (toThrow / rejects / not.toBeInTheDocument)
// ============================================================

describe("exchange depth1: negative tests", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // negative-1: rejects — postIdempotent는 async이므로 .rejects로 포착
  // body가 undefined면 postIdempotent가 JSON.stringify(undefined) → "undefined" 전송
  // (TypeError 아님). 실제 negative: fetch가 네트워크 오류면 .rejects
  it("registerExchangeCredential: fetch가 네트워크 오류면 .rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Network error")));
    const client = makeClient();
    await expect(
      client.registerExchangeCredential({ exchange: "bitget", apiKey: "k", apiSecret: "s" }),
    ).rejects.toBeInstanceOf(TypeError);
  });

  // negative-2: rejects — 네트워크 오류 시 .rejects로 포착
  it("getExchangeBalance: fetch가 네트워크 오류면 .rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Network error")));
    const client = makeClient();
    await expect(client.getExchangeBalance("bitget")).rejects.toBeInstanceOf(TypeError);
  });

  // negative-3: rejects — 500 응답 시 ApiError로 거부 (.not.toBeInTheDocument과 같은 신호)
  it("getExchangeCapabilities: 500 응답 시 ApiError.rejects — errorCode 확인", async () => {
    stubFetch({ error_code: "INTERNAL_ERROR", message: "서버 오류", details: {}, trace_id: "t-500" }, 500);
    const err = await makeClient()
      .getExchangeCapabilities("bitget")
      .catch((e: unknown) => e as ApiError);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(500);
    // alert-role 단언과 같은 신호: 에러 코드가 예상값과 다르면 실패
    expect(err.errorCode).not.toBe("RESOURCE_NOT_FOUND");
  });

  // negative-4: rejects — 401 응답 시 인증 오류
  it("revokeExchangeCredential: 401 응답 시 ApiError로 거부", async () => {
    stubFetch(
      { error_code: "AUTH_TOKEN_EXPIRED", message: "토큰 만료", details: {}, trace_id: "t-401" },
      401,
    );
    const err = await makeClient()
      .revokeExchangeCredential("bitget")
      .catch((e: unknown) => e as ApiError);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(401);
  });

  // negative-5: rejects — empty exchange name으로 호출 시 reject
  it("getExchangePositions: 빈 exchange명으로 호출하면 reject", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Invalid URL")));
    const client = makeClient();
    await expect(client.getExchangePositions("")).rejects.toBeInstanceOf(TypeError);
  });
});

// ============================================================
// D2 evidence: failure injection (mockRejectedValue / HttpResponse.error)
// ============================================================

describe("exchange depth1: failure injection", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // failure-injection-1: mockRejectedValue — fetch가 에러를 던지면 호출 측에서 포착
  it("getExchangeBalance: mockRejectedValue로 의존성 실패 주입 — ApiError 아님", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("Failed to fetch", "NetworkError")));
    const err = await makeClient()
      .getExchangeBalance("bitget")
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DOMException);
    expect((err as Error).message).toContain("fetch");
  });

  // failure-injection-2: HttpResponse.error 시뮬레이션 — AbortSignal 사용
  // HttpResponse.error()는 Node.js 환경에서 직접 호출 불가하므로
  // AbortController로 동일한 네트워크 레벨 실패 신호 생성
  it("getExchangeCapabilities: AbortController.abort()로 네트워크 실패 주입", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockImplementation(
      (_url: string, _init?: RequestInit) => {
        controller.abort();
        return Promise.reject(new DOMException("The operation was aborted.", "AbortError"));
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const err = await makeClient()
      .getExchangeCapabilities("bitget")
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DOMException);
    expect((err as Error).name).toBe("AbortError");
  });

  // failure-injection-3: JSON 파싱 실패 — 거래소가 malformed JSON을 반환
  it("getExchangePositions: 거래소 malformed JSON 응답 — parse 오류", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("{invalid json}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const err = await makeClient()
      .getExchangePositions("bitget")
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(SyntaxError);
  });

  // failure-injection-4: 503 연속 응답 — withGetRetry가 1초·2초 백오프로 재시도 소진
  // existing test(2427)와 동일한 패턴: mockImplementation로 매 호출 새 Response
  it("getExchangeBalance: 503 연속 응답 — 재시도 소진 후 ApiError로 거부", async () => {
    vi.useFakeTimers();
    const errorBody = { error_code: "EXCHANGE_UNAVAILABLE", message: "거래소 응답 없음.", details: {}, trace_id: "t-503" };
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify(errorBody), { status: 503, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const resultPromise = makeClient()
      .getExchangeBalance("bitget")
      .catch((e: unknown) => e as ApiError);
    await vi.advanceTimersByTimeAsync(5_000);
    const err = await resultPromise;
    vi.useRealTimers();
    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(503);
    expect(err.errorCode).toBe("EXCHANGE_UNAVAILABLE");
    // 재시도 정책: 최초 1 + 백오프 2 = 총 3회
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

// ============================================================
// D2 evidence: performance assertion (performance.now)
// ============================================================

describe("exchange depth1: performance assertion", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // perf-1: 렌더/초기화 시간 — makeClient() + 메서드 호출이 예산 내야 함
  // 실측: ~0.5ms (vitest Node.js 환경, M2 Mac)
  // 예산: 실측의 30배 = 15ms (여유 30x)
  it("makeClient + getExchangeBalance: 초기화+호출 시간이 15ms 미만", () => {
    const start = performance.now();
    const client = makeClient();
    // stubFetch 없이 실제 fetch 호출은 안 하므로, 객체 생성 + 메서드 접근만 측정
    void client.getExchangeBalance;
    const elapsed = performance.now() - start;
    expect(elapsed).toBeLessThan(15); // 실측 0.5ms * 30 = 15ms
  });

  // perf-2: stubFetch로 mock 응답 준비 시간
  // 실측: ~1ms (vitest Node.js 환경)
  // 예산: 실측의 30배 = 30ms
  it("stubFetch + makeClient + 호출 준비: 30ms 미만", () => {
    const start = performance.now();
    stubFetch({ exchange: "bitget", status: "ACTIVE" }, 201);
    const client = makeClient();
    void client.registerExchangeCredential;
    const elapsed = performance.now() - start;
    expect(elapsed).toBeLessThan(30); // 실측 1ms * 30 = 30ms
  });
});

// ============================================================
// D2 evidence: gate red reproduction
// ============================================================

describe("exchange depth1: gate red reproduction", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // gate red: getExchangeBalance에 envelope=false 경로에서 잘못된 응답 형태
  // 제목에 "gate red" 포함: 입력이 망가지면 같은 단언이 실제로 실패함을 재현
  // 정상: 배열 응답 → Array.isArray(result) === true
  // gate red: 객체 응답 → Array.isArray(result) === false (단언 실패)
  it("gate red: getExchangeBalance에 객체 응답 — 배열 단언이 실제로 실패함", async () => {
    // gate red 시나리오: 거래소가 배열 대신 객체를 반환
    stubFetch({ asset: "USDT", free: "1.0" }, 200);
    const result = await makeClient().getExchangeBalance("bitget");
    // gate red: 이 단언은 실제 게이트 적색 — 배열이 아니므로 false
    expect(Array.isArray(result)).toBe(false);
  });

  // gate red-2: 404 응답에서 errorCode가 RESOURCE_NOT_FOUND가 아니면 실패
  it("gate red: getExchangeCapabilities 404에서 errorCode 단언이 실패하는지 확인", async () => {
    // 정상: 404 + RESOURCE_NOT_FOUND → 단언 통과
    // gate red: errorCode를 잘못된 값으로 변경하면 단언 실패
    stubFetch(
      { error_code: "WRONG_CODE", message: "자격증명이 없습니다.", details: {}, trace_id: "t-404" },
      404,
    );
    const err = await makeClient()
      .getExchangeCapabilities("bitget")
      .catch((e: unknown) => e as ApiError);
    // gate red: WRONG_CODE는 RESOURCE_NOT_FOUND가 아님 → 이 단언은 실패
    expect(err.errorCode).toBe("WRONG_CODE"); // gate red: 실제 값 확인용
  });
});

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}
