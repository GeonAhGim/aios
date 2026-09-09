import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase } from "../http";
import { withTrust } from "./trust";

class TrustTestClient extends withTrust(ApiClientBase) {}

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

function makeClient(): TrustTestClient {
  return new TrustTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

// trust.py/trust_memberships.py 5라우트 전부 -> ApiResponse[T] + ok()라 봉투(data/meta)로
// 응답한다(mandates.test.ts와 동일 관용).
function envelope<T>(data: T): { data: T; meta: { trace_id: string; as_of: string; page: null } } {
  return { data, meta: { trace_id: "trace-1", as_of: "2026-09-09T00:00:00Z", page: null } };
}

const CONSENT_DECISION = {
  consent_id: "consent-1",
  tenant_id: "tenant-1",
  purpose: "MARKETING",
  disclosure_id: "disclosure-1",
  disclosure_revision: 1,
  state: "ACTIVE",
  accepted_at: "2026-09-09T00:00:00Z",
  revoked_at: null,
  expires_at: null,
  schema_version: "v1",
};

const MEMBERSHIP_RESPONSE = {
  membership_id: "membership-1",
  tenant_id: "tenant-1",
  subject_id: "subject-1",
  role: "MEMBER",
  state: "ACTIVE",
  revision: 1,
};

describe("withTrust: status·consents:revoke·memberships grant/suspend/revoke", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getTrustStatus: GET으로 조회하고 봉투(data)를 풀어 camelCase로 반환한다", async () => {
    const fetchMock = stubFetch(
      envelope({ tenant_id: "tenant-1", consents: [CONSENT_DECISION], as_of: "2026-09-09T00:00:00Z" }),
    );

    const result = await makeClient().getTrustStatus();

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/trust/status");
    expect(init.method ?? "GET").toBe("GET");
    expect(result).toEqual({
      tenantId: "tenant-1",
      consents: [
        {
          consentId: "consent-1",
          tenantId: "tenant-1",
          purpose: "MARKETING",
          disclosureId: "disclosure-1",
          disclosureRevision: 1,
          state: "ACTIVE",
          acceptedAt: "2026-09-09T00:00:00Z",
          revokedAt: null,
          expiresAt: null,
          schemaVersion: "v1",
        },
      ],
      asOf: "2026-09-09T00:00:00Z",
    });
  });

  it("revokeConsent: /consents/:consentId:revoke 경로로 body 없이 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...CONSENT_DECISION, state: "REVOKED", revoked_at: "2026-09-09T01:00:00Z" }));

    const result = await makeClient().revokeConsent("consent-1");

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/trust/consents/consent-1:revoke");
    expect(init.method).toBe("POST");
    expect(result.state).toBe("REVOKED");
  });

  it("grantMembership: /memberships 경로로 subjectId/role을 snake_case body에 실어 POST한다", async () => {
    const fetchMock = stubFetch(envelope(MEMBERSHIP_RESPONSE), 201);

    const result = await makeClient().grantMembership({ subjectId: "subject-1", role: "MEMBER" });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/trust/memberships");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ subject_id: "subject-1", role: "MEMBER" });
    expect(result).toEqual({
      membershipId: "membership-1",
      tenantId: "tenant-1",
      subjectId: "subject-1",
      role: "MEMBER",
      state: "ACTIVE",
      revision: 1,
    });
  });

  it("suspendMembership: /memberships/:subjectId:suspend 경로로 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...MEMBERSHIP_RESPONSE, state: "SUSPENDED" }));

    const result = await makeClient().suspendMembership("subject-1");

    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/trust/memberships/subject-1:suspend");
    expect(result.state).toBe("SUSPENDED");
  });

  it("revokeMembership: /memberships/:subjectId:revoke 경로로 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...MEMBERSHIP_RESPONSE, state: "REVOKED" }));

    const result = await makeClient().revokeMembership("subject-1");

    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/trust/memberships/subject-1:revoke");
    expect(result.state).toBe("REVOKED");
  });
});
