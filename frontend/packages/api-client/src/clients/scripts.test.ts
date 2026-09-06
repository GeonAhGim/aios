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
