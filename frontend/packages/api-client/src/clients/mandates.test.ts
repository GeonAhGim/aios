import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase } from "../http";
import { withMandates } from "./mandates";

class MandatesTestClient extends withMandates(ApiClientBase) {}

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

function makeClient(): MandatesTestClient {
  return new MandatesTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

// mandates.py 7라우트 전부 -> ApiResponse[T] + ok()라 봉투(data/meta)로 응답한다
// (riskGate.test.ts와 동일 관용).
function envelope<T>(data: T): { data: T; meta: { trace_id: string; as_of: string; page: null } } {
  return { data, meta: { trace_id: "trace-1", as_of: "2026-09-09T00:00:00Z", page: null } };
}

const REVISION_VIEW = {
  id: "rev-1",
  mandate_id: "mandate-1",
  revision_no: 1,
  state: "DRAFT",
  max_total_exposure_pct: 50,
  max_single_instrument_pct: 20,
  min_cash_buffer_pct: 5,
  max_daily_loss_pct: 3,
  allowed_autonomy: "PAPER",
  forbidden_assets: [],
  revision_hash: "a".repeat(64),
  cooling_off_started_at: null,
  created_at: "2026-09-09T00:00:00Z",
  activated_at: null,
  schema_version: "v1",
};

const RULE_INPUT = {
  maxTotalExposurePct: 50,
  maxSingleInstrumentPct: 20,
  minCashBufferPct: 5,
  maxDailyLossPct: 3,
  allowedAutonomy: "PAPER" as const,
  forbiddenAssets: [],
};

describe("withMandates: status·drafts·amendments·activate·pause·resume·policy:evaluate", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getMandateStatus: GET으로 조회하고 봉투(data)를 풀어 camelCase로 반환한다", async () => {
    const fetchMock = stubFetch(
      envelope({ tenant_id: "tenant-1", active_revision: null, pending_revision: REVISION_VIEW }),
    );

    const result = await makeClient().getMandateStatus();

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/mandates/status");
    expect(init.method ?? "GET").toBe("GET");
    expect(result).toEqual({
      tenantId: "tenant-1",
      activeRevision: null,
      pendingRevision: {
        id: "rev-1",
        mandateId: "mandate-1",
        revisionNo: 1,
        state: "DRAFT",
        maxTotalExposurePct: 50,
        maxSingleInstrumentPct: 20,
        minCashBufferPct: 5,
        maxDailyLossPct: 3,
        allowedAutonomy: "PAPER",
        forbiddenAssets: [],
        revisionHash: "a".repeat(64),
        coolingOffStartedAt: null,
        createdAt: "2026-09-09T00:00:00Z",
        activatedAt: null,
        schemaVersion: "v1",
      },
    });
  });

  it("createMandateDraft: /drafts 경로로 규칙 입력을 snake_case body에 실어 POST한다", async () => {
    const fetchMock = stubFetch(envelope(REVISION_VIEW), 201);

    const result = await makeClient().createMandateDraft(RULE_INPUT);

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/mandates/drafts");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      max_total_exposure_pct: 50,
      max_single_instrument_pct: 20,
      min_cash_buffer_pct: 5,
      max_daily_loss_pct: 3,
      allowed_autonomy: "PAPER",
      forbidden_assets: [],
    });
    expect(result.state).toBe("DRAFT");
  });

  it("proposeMandateAmendment: /amendments 경로로 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...REVISION_VIEW, state: "PROPOSED" }), 201);

    await makeClient().proposeMandateAmendment(RULE_INPUT);

    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/mandates/amendments");
  });

  it("activateMandateRevision: /revisions/:id:activate 경로로 body 없이(기본값 {}) POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...REVISION_VIEW, state: "ACTIVE" }));

    const result = await makeClient().activateMandateRevision("rev-1");

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/mandates/revisions/rev-1:activate");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({});
    expect(result.state).toBe("ACTIVE");
  });

  it("activateMandateRevision: password/totpCode를 넘기면 snake_case body에 싣는다", async () => {
    const fetchMock = stubFetch(envelope({ ...REVISION_VIEW, state: "ACTIVE" }));

    await makeClient().activateMandateRevision("rev-1", { password: "pw", totpCode: "123456" });

    const { init } = requestOf(fetchMock);
    expect(JSON.parse(init.body as string)).toEqual({ password: "pw", totp_code: "123456" });
  });

  it("pauseMandate: /mandate:pause 경로로 body 없이 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...REVISION_VIEW, state: "PAUSED" }));

    const result = await makeClient().pauseMandate();

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/mandates/mandate:pause");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(result.state).toBe("PAUSED");
  });

  it("resumeMandate: /mandate:resume 경로로 body 없이 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...REVISION_VIEW, state: "ACTIVE" }));

    const result = await makeClient().resumeMandate();

    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/mandates/mandate:resume");
    expect(result.state).toBe("ACTIVE");
  });

  it("evaluateMandatePolicy: /policy:evaluate 경로로 subject를 snake_case body에 실어 POST하고 outcome/reasonCodes를 반환한다", async () => {
    const fetchMock = stubFetch(
      envelope({
        id: "decision-1",
        tenant_id: "tenant-1",
        bundle_id: "bundle-1",
        command_type: "ORDER_SUBMIT",
        outcome: "DENY",
        reason_codes: ["RISK_MAX_POSITION_EXCEEDED"],
        obligations: [],
        evaluated_at: "2026-09-09T00:00:00Z",
        expires_at: "2026-09-09T00:00:30Z",
        schema_version: "v1",
      }),
    );

    const result = await makeClient().evaluateMandatePolicy({ commandType: "ORDER_SUBMIT" });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/mandates/policy:evaluate");
    expect(JSON.parse(init.body as string)).toEqual({ command_type: "ORDER_SUBMIT" });
    expect(result.outcome).toBe("DENY");
    expect(result.reasonCodes).toEqual(["RISK_MAX_POSITION_EXCEEDED"]);
  });
});
