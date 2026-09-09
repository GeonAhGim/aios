import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase } from "../http";
import { withConnections } from "./connections";

class ConnectionsTestClient extends withConnections(ApiClientBase) {}

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

function makeClient(): ConnectionsTestClient {
  return new ConnectionsTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

// connections.py 5라우트 전부 -> ApiResponse[T] + ok()라 봉투(data/meta)로
// 응답한다(trust.test.ts와 동일 관용).
function envelope<T>(data: T): { data: T; meta: { trace_id: string; as_of: string; page: null } } {
  return { data, meta: { trace_id: "trace-1", as_of: "2026-09-09T00:00:00Z", page: null } };
}

const CONNECTION_VIEW = {
  id: "connection-1",
  provider_code: "toss",
  masked_account_label: "toss ****1234",
  state: "ACTIVE_READONLY",
  capability_profile: ["READ_BALANCE"],
  revision: 1,
  created_at: "2026-09-09T00:00:00Z",
  scope_verified: true,
  schema_version: "v1",
};

const SNAPSHOT_VIEW = {
  connection_id: "connection-1",
  captured_at: "2026-09-09T01:00:00Z",
  provider_as_of: "2026-09-09T00:55:00Z",
  freshness: "FRESH",
  currency: "KRW",
  values: [{ entity_type: "ACCOUNT", entity_key: "cash", value: "1000000" }],
  schema_version: "v1",
};

describe("withConnections: list·begin·confirm·sync·revoke", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listConnections: GET으로 조회하고 봉투(data)를 풀어 camelCase로 반환한다", async () => {
    const fetchMock = stubFetch(
      envelope({ connections: [CONNECTION_VIEW], as_of: "2026-09-09T00:00:00Z" }),
    );

    const result = await makeClient().listConnections();

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/connections");
    expect(init.method ?? "GET").toBe("GET");
    expect(result).toEqual({
      connections: [
        {
          id: "connection-1",
          providerCode: "toss",
          maskedAccountLabel: "toss ****1234",
          state: "ACTIVE_READONLY",
          capabilityProfile: ["READ_BALANCE"],
          revision: 1,
          createdAt: "2026-09-09T00:00:00Z",
          scopeVerified: true,
          schemaVersion: "v1",
        },
      ],
      asOf: "2026-09-09T00:00:00Z",
    });
  });

  it("beginConnection: /connections 경로로 body를 snake_case로 실어 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...CONNECTION_VIEW, state: "PENDING_CONSENT" }), 201);

    const result = await makeClient().beginConnection({
      providerCode: "toss",
      opaqueAccountRef: "opaque-ref-1",
      requestedCapabilityProfile: ["READ_BALANCE"],
    });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/connections");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      provider_code: "toss",
      opaque_account_ref: "opaque-ref-1",
      requested_capability_profile: ["READ_BALANCE"],
    });
    expect(result.state).toBe("PENDING_CONSENT");
  });

  it("confirmConnection: /connections/:connectionId:confirm 경로로 body 없이 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...CONNECTION_VIEW, state: "ACTIVE_READONLY" }));

    const result = await makeClient().confirmConnection("connection-1");

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/connections/connection-1:confirm");
    expect(init.method).toBe("POST");
    expect(result.state).toBe("ACTIVE_READONLY");
  });

  it("syncConnection: /connections/:connectionId:sync 경로로 POST하고 스냅샷을 camelCase로 반환한다", async () => {
    const fetchMock = stubFetch(envelope(SNAPSHOT_VIEW));

    const result = await makeClient().syncConnection("connection-1");

    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/connections/connection-1:sync");
    expect(result).toEqual({
      connectionId: "connection-1",
      capturedAt: "2026-09-09T01:00:00Z",
      providerAsOf: "2026-09-09T00:55:00Z",
      freshness: "FRESH",
      currency: "KRW",
      values: [{ entityType: "ACCOUNT", entityKey: "cash", value: "1000000" }],
      schemaVersion: "v1",
    });
  });

  it("revokeConnection: /connections/:connectionId:revoke 경로로 POST한다", async () => {
    const fetchMock = stubFetch(envelope({ ...CONNECTION_VIEW, state: "REVOKED" }));

    const result = await makeClient().revokeConnection("connection-1");

    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/connections/connection-1:revoke");
    expect(result.state).toBe("REVOKED");
  });
});
