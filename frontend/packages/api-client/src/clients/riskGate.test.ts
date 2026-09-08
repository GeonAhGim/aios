import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase } from "../http";
import { withRiskGate } from "./riskGate";

class RiskGateTestClient extends withRiskGate(ApiClientBase) {}

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

function makeClient(): RiskGateTestClient {
  return new RiskGateTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

// risk_gate.py 라우터 3개 전부 -> ApiResponse[T] + ok()라 봉투(data/meta)로 응답한다
// (foundation.test.ts와 동일 관용).
function envelope<T>(data: T): { data: T; meta: { trace_id: string; as_of: string; page: null } } {
  return { data, meta: { trace_id: "trace-1", as_of: "2026-09-09T00:00:00Z", page: null } };
}

const CONTROL_VIEW = {
  id: "c1",
  scope: "GLOBAL",
  scope_ref: "tenant-1",
  state: "ACTIVE",
  reason: "manual halt",
  fence_token: 1,
  created_at: "2026-09-09T00:00:00Z",
  deactivated_at: null,
  idempotency_digest: null,
  schema_version: "v1",
};

const RECOVERY_DECISION = {
  id: "d1",
  gate_kind: "RECOVERY",
  outcome: "ALLOW",
  reason_codes: [],
  evaluated_at: "2026-09-09T00:00:00Z",
  expires_at: "2026-09-09T01:00:00Z",
  trace_id: "trace-2",
};

describe("withRiskGate: safety-controls 조회·해제(deactivate/evaluate-recovery)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listSafetyControls: GET으로 조회하고 봉투(data.controls/asOf)를 풀어 camelCase로 반환한다", async () => {
    stubFetch(envelope({ controls: [CONTROL_VIEW], as_of: "2026-09-09T00:00:00Z" }));

    const result = await makeClient().listSafetyControls();

    expect(result).toEqual({
      controls: [
        {
          id: "c1",
          scope: "GLOBAL",
          scopeRef: "tenant-1",
          state: "ACTIVE",
          reason: "manual halt",
          fenceToken: 1,
          createdAt: "2026-09-09T00:00:00Z",
          deactivatedAt: null,
          idempotencyDigest: null,
          schemaVersion: "v1",
        },
      ],
      asOf: "2026-09-09T00:00:00Z",
    });
  });

  it("deactivateSafetyControl: :controlId:deactivate 경로로 body 없이 POST한다", async () => {
    const fetchMock = stubFetch(envelope(CONTROL_VIEW));

    const result = await makeClient().deactivateSafetyControl("c1");

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/risk-gate/safety-controls/c1:deactivate");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(result.state).toBe("ACTIVE");
  });

  it("evaluateRecovery: :controlId:evaluate-recovery 경로로 evidenceRef/approvalId를 snake_case body에 싣는다", async () => {
    const fetchMock = stubFetch(envelope(RECOVERY_DECISION));

    const result = await makeClient().evaluateRecovery("c1", { evidenceRef: "ev-1", approvalId: 42 });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/risk-gate/safety-controls/c1:evaluate-recovery");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ evidence_ref: "ev-1", approval_id: 42 });
    expect(result.outcome).toBe("ALLOW");
  });
});
