import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
import { ApiClientBase } from "../http";
import { ApiError } from "../httpErrors";
import { withScripts } from "./scripts";

class ScriptsTestClient extends withScripts(ApiClientBase) {}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): ScriptsTestClient {
  return new ScriptsTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  return fetchMock.mock.calls[0] as [string, RequestInit];
}

describe("scripts apiPaths 레지스트리: scripts.compile은 envelope=true다", () => {
  it("scripts.compile: envelope=true, v1Path 미배선(mount_v1 미도달)", () => {
    expect(API_ROUTES["scripts.compile"].envelope).toBe(true);
    expect(API_ROUTES["scripts.compile"].v1Path).toBeUndefined();
    expect(API_ROUTES["scripts.compile"].legacyPath).toBe("/v1/scripts/compile");
  });
});

describe("withScripts", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("compileScript: POST /v1/scripts/compile 봉투를 풀어 camelCase로 돌려준다", async () => {
    const fetchMock = stubFetch({
      data: {
        script_hash: "a".repeat(64),
        grammar_version: "aios-script-1",
        ir_version: "aios-ir-1",
        registry_version: "reg-v1",
        ir_sha256: "b".repeat(64),
        instr_count: 5,
        resources: {
          series_count: 1,
          lookback_total: 14,
          op_count: 3,
          call_count: 1,
          call_depth: 1,
          plot_count: 1,
        },
        elapsed_ms: 12,
      },
      meta: { trace_id: "t-1", as_of: "2026-09-06T00:00:00Z" },
    });

    const result = await makeClient().compileScript("plot(close, 1)\n");

    const [url, init] = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/scripts/compile");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ source: "plot(close, 1)\n" });
    expect(result.scriptHash).toBe("a".repeat(64));
    expect(result.resources.plotCount).toBe(1);
    expect(result.elapsedMs).toBe(12);
  });

  it("negative: 컴파일 오류(400 VALIDATION_INVALID_FIELD)는 ApiError.details에 code/line/col을 그대로 싣는다", async () => {
    const fetchMock = stubFetch(
      {
        error_code: "VALIDATION_INVALID_FIELD",
        message: "SCRIPT_SYNTAX: unexpected end of input",
        details: { code: "SCRIPT_SYNTAX", line: 1, col: 12 },
        trace_id: "t-400",
      },
      400,
    );

    const err = await makeClient()
      .compileScript("let a = 1 +")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(400);
    expect(err.errorCode).toBe("VALIDATION_INVALID_FIELD");
    expect(err.details).toEqual({ code: "SCRIPT_SYNTAX", line: 1, col: 12 });
    expect(err.traceId).toBe("t-400");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

// DEPTH_DSL_IND(task-2727)가 원 task-1558(af0cbf8)을 D2 축 하한 미달(D1)로
// 판정 — negative가 2~3건뿐이라 얕았고(에디터 2건 + 여기 1건, 합쳐도 경계
// 수준) 실패 주입·성능 단언·게이트 적색 재현이 전부 비어 있었다(task-2922
// DEEPEN). 위 400 테스트는 "서버가 에러 바디를 내려줬다"만 흉내내지, 네트워크
// 자체가 끊기거나 타임아웃되거나 응답 바디가 전송 중 깨지는 실제 결함 클래스는
// 다루지 않았다 — POST는 GET과 달리 withGetRetry(httpErrors.ts) 재시도 대상이
// 아니므로(멱등키 규약 충돌), 아래 결함들은 전부 재시도 없이 1회 fetch로 그대로
// 던져지는지까지 함께 확인한다.
describe("failure-injection — 실 어댑터/네트워크 결함 시뮬레이션", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("negative: 네트워크 완전 단절(fetch 자체가 reject)이면 ApiError로 재분류하지 않고 원본 예외를 그대로 던진다", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    const err = await makeClient()
      .compileScript("plot(close, 1)\n")
      .catch((e: unknown) => e);

    expect(err).toBeInstanceOf(TypeError);
    expect(err).not.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("negative: 네트워크 타임아웃(AbortError)도 재시도 없이 원본 예외를 그대로 던진다(POST는 withGetRetry 대상이 아님)", async () => {
    const timeoutError = new Error("The operation was aborted due to timeout");
    timeoutError.name = "AbortError";
    const fetchMock = vi.fn().mockRejectedValue(timeoutError);
    vi.stubGlobal("fetch", fetchMock);

    const err = await makeClient()
      .compileScript("plot(close, 1)\n")
      .catch((e: unknown) => e as Error);

    expect(err.name).toBe("AbortError");
    expect(err).not.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("negative: 응답 바디가 전송 중 잘린 깨진 JSON(실 어댑터 결함 — 에러 봉투가 아니라 파싱 자체가 불가)이면 SyntaxError를 그대로 던진다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{"data": {"script_hash":', {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const err = await makeClient()
      .compileScript("plot(close, 1)\n")
      .catch((e: unknown) => e);

    expect(err).toBeInstanceOf(SyntaxError);
  });
});

describe("negative — 5xx 서버 오류", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("500 INTERNAL_ERROR는 재시도 없이 1회 fetch 후 ApiError로 던진다", async () => {
    const fetchMock = stubFetch(
      {
        error_code: "INTERNAL_ERROR",
        message: "unexpected server failure",
        details: {},
        trace_id: "t-500",
      },
      500,
    );

    const err = await makeClient()
      .compileScript("plot(close, 1)\n")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(500);
    expect(err.errorCode).toBe("INTERNAL_ERROR");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("503 EXCHANGE_UNAVAILABLE도 GET과 달리 백오프 재시도 없이 즉시 실패한다(POST는 withGetRetry 미대상)", async () => {
    const fetchMock = stubFetch(
      {
        error_code: "EXCHANGE_UNAVAILABLE",
        message: "compiler worker pool unavailable",
        details: {},
        trace_id: "t-503",
      },
      503,
    );

    const err = await makeClient()
      .compileScript("plot(close, 1)\n")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(503);
    // GET이었다면 httpErrors.ts SERVER_ERROR_BACKOFF_SEC([1, 2])만큼 추가 호출이
    // 생겼을 것 — POST는 1회로 끝난다는 사실이 이 테스트의 핵심 단언이다.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("수치 성능 단언 — 대용량 소스 왕복", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("소스 20만자짜리 스크립트도 짧은 시간 안에 직렬화·요청·응답 파싱을 마친다(O(n) 유지 확인 — 회귀로 O(n^2)가 되면 이 임계값이 신호를 준다)", async () => {
    const bigSource = "plot(close, 1)\n".repeat(12_500); // 약 20만자
    stubFetch({
      data: {
        script_hash: "a".repeat(64),
        grammar_version: "aios-script-1",
        ir_version: "aios-ir-1",
        registry_version: "reg-v1",
        ir_sha256: "b".repeat(64),
        instr_count: 12_500,
        resources: { series_count: 1, lookback_total: 0, op_count: 12_500, call_count: 12_500, call_depth: 1, plot_count: 12_500 },
        elapsed_ms: 40,
      },
      meta: { trace_id: "t-perf", as_of: "2026-09-10T00:00:00Z" },
    });

    const startedAt = performance.now();
    const result = await makeClient().compileScript(bigSource);
    const elapsedMs = performance.now() - startedAt;

    expect(result.instrCount).toBe(12_500);
    expect(elapsedMs).toBeLessThan(1000);
  });
});

// task-618 계열 DEEPEN과 동일 기법: 실 가드(buildApiError가 details를 그대로
// 옮기는 동작)를 되돌린 naive 구현을 나란히 실행해, 위 400 negative 단언이
// 실제로 어떤 회귀를 적색으로 잡아내는 게이트인지 증명한다.
describe("게이트 적색 재현 — CI red-line (compile 오류 details 유실 회귀)", () => {
  it("details를 옮기지 않는 naive buildApiError로 회귀하면 ScriptEditorPage가 line/col 마커를 그릴 근거를 잃는다 — 위 400 negative 단언이 그 회귀를 적색으로 만드는 게이트임을 증명", async () => {
    const errorBody = {
      error_code: "VALIDATION_INVALID_FIELD",
      message: "SCRIPT_SYNTAX: unexpected end of input",
      details: { code: "SCRIPT_SYNTAX", line: 1, col: 12 },
      trace_id: "t-400b",
    };
    stubFetch(errorBody, 400);

    const err = await makeClient()
      .compileScript("let a = 1 +")
      .catch((e: unknown) => e as ApiError);

    // 실제 구현: details가 그대로 실린다(위 400 negative 단언과 동일 계약).
    expect(err.details).toEqual({ code: "SCRIPT_SYNTAX", line: 1, col: 12 });

    // naive 회귀 시뮬레이션: 과거 결함 클래스(task-388 이전)처럼 details를
    // 아예 옮기지 않는 buildApiError였다고 가정한다.
    function naiveBuildApiError(status: number, body: typeof errorBody): ApiError {
      return new ApiError(status, body.message, body.trace_id, body.error_code);
    }
    const naiveErr = naiveBuildApiError(400, errorBody);

    // naive 구현이었다면 details가 없어 ScriptEditorPage의
    // isScriptCompileErrorDetails 좁히기가 항상 실패해 마커를 절대 그릴 수 없다
    // — 이 결과 자체가 naive 회귀의 결함을 보여준다.
    expect(naiveErr.details).toBeUndefined();
  });
});
