import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase } from "../http";
import { withCompliance } from "./compliance";

class ComplianceTestClient extends withCompliance(ApiClientBase) {}

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

function makeClient(): ComplianceTestClient {
  return new ComplianceTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

// compliance.py GET /decisions/{decision_id} -> ApiResponse[...] + ok()라 봉투
// (data/meta)로 응답한다(mandates.test.ts와 동일 관용).
function envelope<T>(data: T): { data: T; meta: { trace_id: string; as_of: string; page: null } } {
  return { data, meta: { trace_id: "trace-1", as_of: "2026-09-09T00:00:00Z", page: null } };
}

describe("withCompliance: getComplianceDecision", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GET /v1/foundation/compliance/decisions/:decisionId로 조회하고 봉투를 풀어 camelCase로 반환한다", async () => {
    const fetchMock = stubFetch(
      envelope({
        decision_id: "d-1",
        verdict: "DENY",
        rule_hits: [
          { rule_id: "RESTRICTED_LIST", severity: "DENY", message: "금지 종목입니다.", evidence: { symbol: "XYZ" } },
        ],
        inputs_hash: "a".repeat(64),
        bundle_version: "b".repeat(64),
        evaluated_at: "2026-09-09T00:00:00Z",
        schema_version: "v1",
      }),
    );

    const result = await makeClient().getComplianceDecision("d-1");

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/compliance/decisions/d-1");
    expect(init.method ?? "GET").toBe("GET");
    expect(result).toEqual({
      decisionId: "d-1",
      verdict: "DENY",
      ruleHits: [
        { ruleId: "RESTRICTED_LIST", severity: "DENY", message: "금지 종목입니다.", evidence: { symbol: "XYZ" } },
      ],
      inputsHash: "a".repeat(64),
      bundleVersion: "b".repeat(64),
      evaluatedAt: "2026-09-09T00:00:00Z",
      schemaVersion: "v1",
    });
  });

  it("decisionId를 경로에 그대로 치환한다(다른 id를 넘기면 다른 경로로 요청한다)", async () => {
    const fetchMock = stubFetch(
      envelope({
        decision_id: "d-2",
        verdict: "ALLOW",
        rule_hits: [],
        inputs_hash: "a".repeat(64),
        bundle_version: "b".repeat(64),
        evaluated_at: "2026-09-09T00:00:00Z",
        schema_version: "v1",
      }),
    );

    await makeClient().getComplianceDecision("d-2");

    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/compliance/decisions/d-2");
  });
});
