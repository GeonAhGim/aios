import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase } from "../http";
import { ApiError } from "../httpErrors";
import { withPerformance } from "./performance";

class PerformanceTestClient extends withPerformance(ApiClientBase) {}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): PerformanceTestClient {
  return new PerformanceTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

// performance.py 4라우트 전부 -> ApiResponse[T] + ok()라 봉투(data/meta)로 응답한다
// (mandates.test.ts와 동일 관용).
function envelope<T>(data: T): { data: T; meta: { trace_id: string; as_of: string; page: null } } {
  return { data, meta: { trace_id: "trace-1", as_of: "2026-09-23T00:00:00Z", page: null } };
}

const MONEY_ZERO = {
  amount: "0",
  currency: "USDT",
  precision: 2,
  as_of: "2026-09-23T00:00:00Z",
  state: "FINAL",
};

const STATEMENT_VIEW = {
  id: "stmt-1",
  tenant_id: "tenant-1",
  scope: "PAPER",
  scope_ref: "tenant-1",
  period_start: "2026-09-01T00:00:00Z",
  period_end: "2026-09-23T00:00:00Z",
  as_of: "2026-09-23T00:00:00Z",
  methodology_version: "pm-v1",
  methodology_hash: "a".repeat(64),
  input_refs: ["snapshot-1"],
  components: {
    gross_pnl: MONEY_ZERO,
    fees: MONEY_ZERO,
    slippage: MONEY_ZERO,
    funding: MONEY_ZERO,
    fx: MONEY_ZERO,
    cashflows_net: MONEY_ZERO,
    estimated_tax: MONEY_ZERO,
    net_pnl: MONEY_ZERO,
  },
  returns: [],
  risk: { vol_pct: null, mdd_pct: null, sharpe: null, calmar: null },
  benchmark: null,
  benchmark_ref: null,
  state: "ESTIMATED",
  revision_no: 1,
  prior_statement_id: null,
  identity_ok: true,
  identity_residual: null,
  limitations: [],
  evidence_refs: [],
  schema_version: "v1",
};

describe("withPerformance: compute·목록·상세·correct", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("computePerformanceStatement: :compute 경로로 body를 snake_case로 실어 POST하고 202 응답 봉투를 camelCase로 반환한다", async () => {
    const fetchMock = stubFetch(envelope(STATEMENT_VIEW), 202);

    const result = await makeClient().computePerformanceStatement({
      scope: "PAPER",
      periodStart: "2026-09-01T00:00:00Z",
      periodEnd: "2026-09-23T00:00:00Z",
    });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/performance-statements:compute");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      scope: "PAPER",
      period_start: "2026-09-01T00:00:00Z",
      period_end: "2026-09-23T00:00:00Z",
    });
    expect(result.id).toBe("stmt-1");
    expect(result.state).toBe("ESTIMATED");
    expect(result.components.netPnl).toEqual({
      amount: "0",
      currency: "USDT",
      precision: 2,
      asOf: "2026-09-23T00:00:00Z",
      state: "FINAL",
    });
  });

  it("computePerformanceStatement: scope=LIVE를 서버가 422 UNSUPPORTED_STATEMENT_SCOPE로 거부하면 ApiError로 전파한다", async () => {
    stubFetch(
      { error_code: "UNSUPPORTED_STATEMENT_SCOPE", message: "scope=LIVE는 아직 지원하지 않습니다.", details: {}, trace_id: "t-1" },
      422,
    );

    const err = await makeClient()
      .computePerformanceStatement({
        scope: "LIVE",
        periodStart: "2026-09-01T00:00:00Z",
        periodEnd: "2026-09-23T00:00:00Z",
      })
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(422);
    expect(err.errorCode).toBe("UNSUPPORTED_STATEMENT_SCOPE");
  });

  it("listPerformanceStatements: scope/portfolioId를 쿼리로 실어 GET하고 목록을 반환한다", async () => {
    const fetchMock = stubFetch(envelope({ statements: [STATEMENT_VIEW] }));

    const result = await makeClient().listPerformanceStatements({
      scope: "PAPER",
      portfolioId: "portfolio-1",
    });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe(
      "https://api.example.test/v1/foundation/performance-statements?scope=PAPER&portfolio_id=portfolio-1",
    );
    expect(init.method ?? "GET").toBe("GET");
    expect(result.statements).toHaveLength(1);
    expect(result.statements[0].id).toBe("stmt-1");
  });

  it("listPerformanceStatements: 파라미터 없이 호출하면 쿼리 없이 GET한다", async () => {
    const fetchMock = stubFetch(envelope({ statements: [] }));

    const result = await makeClient().listPerformanceStatements();

    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/performance-statements");
    expect(result.statements).toEqual([]);
  });

  it("getPerformanceStatement: /{statementId} 치환 + portfolioId 쿼리로 GET한다", async () => {
    const fetchMock = stubFetch(envelope(STATEMENT_VIEW));

    const result = await makeClient().getPerformanceStatement("stmt-1", { portfolioId: "portfolio-1" });

    const { url } = requestOf(fetchMock);
    expect(url).toBe(
      "https://api.example.test/v1/foundation/performance-statements/stmt-1?portfolio_id=portfolio-1",
    );
    expect(result.id).toBe("stmt-1");
  });

  it("getPerformanceStatement: 존재하지 않는 statementId는 404 RESOURCE_NOT_FOUND로 ApiError를 던진다", async () => {
    stubFetch(
      { error_code: "RESOURCE_NOT_FOUND", message: "명세서를 찾을 수 없습니다.", details: {}, trace_id: "t-2" },
      404,
    );

    const err = await makeClient()
      .getPerformanceStatement("missing-id")
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(404);
    expect(err.errorCode).toBe("RESOURCE_NOT_FOUND");
  });

  it("correctPerformanceStatement: /{statementId}:correct 경로로 reason을 snake_case body에 실어 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...STATEMENT_VIEW, state: "CORRECTED", revision_no: 2 }));

    const result = await makeClient().correctPerformanceStatement("stmt-1", { reason: "재계산 필요" });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/performance-statements/stmt-1:correct");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ reason: "재계산 필요" });
    expect(result.state).toBe("CORRECTED");
    expect(result.revisionNo).toBe(2);
  });

  it("correctPerformanceStatement: 타 테넌트 명세서를 교정 시도하면 403 FORBIDDEN으로 ApiError를 던진다", async () => {
    stubFetch(
      { error_code: "FORBIDDEN", message: "접근 권한이 없습니다.", details: {}, trace_id: "t-3" },
      403,
    );

    const err = await makeClient()
      .correctPerformanceStatement("stmt-1", { reason: "재계산 필요" })
      .catch((e: unknown) => e as ApiError);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(403);
    expect(err.errorCode).toBe("FORBIDDEN");
  });

  it("failure injection: 네트워크 자체가 끊기면(fetch reject) 봉투 파싱 없이 그대로 예외가 전파된다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    await expect(
      makeClient().getPerformanceStatement("stmt-1"),
    ).rejects.toThrow("Failed to fetch");
  });
});
