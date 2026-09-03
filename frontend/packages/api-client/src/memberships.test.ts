import { afterEach, describe, expect, it, vi } from "vitest";
import { configureTenantHeadersProvider } from "./http";
import { createMembershipsClient, MembershipParseError } from "./memberships";

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

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

const membershipViewRaw = {
  membership_id: "membership-1",
  tenant_id: "tenant-1",
  subject_id: "subject-1",
  role: "MEMBER",
  state: "ACTIVE",
  revision: 1,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z",
  schema_version: "v1",
};

function envelopeOf(data: unknown): unknown {
  return {
    data,
    meta: { trace_id: "11111111-1111-1111-1111-111111111111", as_of: "2026-09-03T00:00:00Z", page: null },
  };
}

function makeClient() {
  return createMembershipsClient("https://api.example.test", () => null);
}

describe("createMembershipsClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    configureTenantHeadersProvider(null);
  });

  it("grant: POST /v1/foundation/trust/memberships로 body를 snake_case 싣고 응답을 MembershipView로 파싱한다", async () => {
    const fetchMock = stubFetch(envelopeOf(membershipViewRaw), 201);

    const result = await makeClient().grant({ subjectId: "subject-1", role: "MEMBER" });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/trust/memberships");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ subject_id: "subject-1", role: "MEMBER" });
    expect(result).toEqual({
      membershipId: "membership-1",
      tenantId: "tenant-1",
      subjectId: "subject-1",
      role: "MEMBER",
      state: "ACTIVE",
      revision: 1,
      createdAt: "2026-09-01T00:00:00Z",
      updatedAt: "2026-09-02T00:00:00Z",
    });
  });

  it("suspend: POST /v1/foundation/trust/memberships/{id}:suspend를 호출한다", async () => {
    const fetchMock = stubFetch(envelopeOf({ ...membershipViewRaw, state: "SUSPENDED" }));

    const result = await makeClient().suspend("membership-1");

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/trust/memberships/membership-1:suspend");
    expect(init.method).toBe("POST");
    expect(result.state).toBe("SUSPENDED");
  });

  it("revoke: POST /v1/foundation/trust/memberships/{id}:revoke를 호출한다", async () => {
    const fetchMock = stubFetch(envelopeOf({ ...membershipViewRaw, state: "REVOKED" }));

    const result = await makeClient().revoke("membership-1");

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/trust/memberships/membership-1:revoke");
    expect(init.method).toBe("POST");
    expect(result.state).toBe("REVOKED");
  });

  it("§3.5 MembershipView 계약과 어긋나는 응답(필드 누락)은 MembershipParseError를 던진다", async () => {
    stubFetch(envelopeOf({ ...membershipViewRaw, role: "SUPERADMIN" }));

    await expect(makeClient().revoke("membership-1")).rejects.toBeInstanceOf(MembershipParseError);
  });

  it("X-Tenant-Id는 tenantContext.ts(task-455)가 주입한 provider를 그대로 태운다 — 새로 만들지 않는다", async () => {
    const fetchMock = stubFetch(envelopeOf(membershipViewRaw));
    configureTenantHeadersProvider(() => ({ "X-Tenant-Id": "3fa85f64-5717-4562-b3fc-2c963f66afa6" }));

    await makeClient().grant({ subjectId: "subject-1", role: "MEMBER" });

    const { init } = requestOf(fetchMock);
    expect(new Headers(init.headers).get("X-Tenant-Id")).toBe("3fa85f64-5717-4562-b3fc-2c963f66afa6");
  });

  it("provider가 없으면(personal) X-Tenant-Id를 싣지 않는다", async () => {
    const fetchMock = stubFetch(envelopeOf(membershipViewRaw));

    await makeClient().grant({ subjectId: "subject-1", role: "MEMBER" });

    const { init } = requestOf(fetchMock);
    expect(new Headers(init.headers).has("X-Tenant-Id")).toBe(false);
  });
});
