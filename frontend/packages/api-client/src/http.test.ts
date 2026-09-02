import { afterEach, describe, expect, it, vi } from "vitest";
import { isValidRequestId } from "./requestId";
import { ApiClientBase, configureTenantHeadersProvider } from "./http";

class TestClient extends ApiClientBase {
  get<T>(path: string, init?: RequestInit): Promise<T> {
    return this.request<T>(path, init);
  }
}

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

function makeClient(): TestClient {
  return new TestClient("https://api.example.test", () => null);
}

function headersOf(fetchMock: ReturnType<typeof vi.fn>): Headers {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return new Headers(init.headers);
}

// spec §3.1/§3.5: 모든 요청에 X-Request-Id를 싣고, 활성 테넌트가 있을 때만
// X-Tenant-Id를 싣는다. requestId.ts(생성·검증)·tenantContext.ts(스토어)는
// 그대로 재사용하고, 여기서는 fetchJson이 두 헤더를 실제로 붙이는지만 본다.
describe("ApiClientBase — 헤더 배선", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    configureTenantHeadersProvider(null);
  });

  it("모든 요청에 유효한 X-Request-Id를 자동으로 싣는다", async () => {
    const fetchMock = stubFetch({ ok: true });

    await makeClient().get("/ping");

    const requestId = headersOf(fetchMock).get("X-Request-Id");
    expect(requestId).not.toBeNull();
    expect(isValidRequestId(requestId!)).toBe(true);
  });

  it("호출자가 실어 둔 유효한 X-Request-Id는 그대로 재사용한다", async () => {
    const fetchMock = stubFetch({ ok: true });
    const presetId = "01ARZ3NDEKTSV4RRFFQ69G5FAV";

    await makeClient().get("/ping", { headers: { "X-Request-Id": presetId } });

    expect(headersOf(fetchMock).get("X-Request-Id")).toBe(presetId);
  });

  it("호출자가 실어 둔 무효한 X-Request-Id는 버리고 새로 생성한다", async () => {
    const fetchMock = stubFetch({ ok: true });

    await makeClient().get("/ping", { headers: { "X-Request-Id": "not-a-ulid" } });

    const requestId = headersOf(fetchMock).get("X-Request-Id");
    expect(requestId).not.toBe("not-a-ulid");
    expect(isValidRequestId(requestId!)).toBe(true);
  });

  it("configureTenantHeadersProvider를 설정하지 않으면 X-Tenant-Id를 싣지 않는다(personal)", async () => {
    const fetchMock = stubFetch({ ok: true });

    await makeClient().get("/ping");

    expect(headersOf(fetchMock).has("X-Tenant-Id")).toBe(false);
  });

  it("configureTenantHeadersProvider가 반환하는 X-Tenant-Id를 요청에 싣는다", async () => {
    const fetchMock = stubFetch({ ok: true });
    const tenantId = "3fa85f64-5717-4562-b3fc-2c963f66afa6";
    configureTenantHeadersProvider(() => ({ "X-Tenant-Id": tenantId }));

    await makeClient().get("/ping");

    expect(headersOf(fetchMock).get("X-Tenant-Id")).toBe(tenantId);
  });

  it("configureTenantHeadersProvider(null)로 되돌리면 다시 X-Tenant-Id를 싣지 않는다", async () => {
    configureTenantHeadersProvider(() => ({ "X-Tenant-Id": "3fa85f64-5717-4562-b3fc-2c963f66afa6" }));
    configureTenantHeadersProvider(null);
    const fetchMock = stubFetch({ ok: true });

    await makeClient().get("/ping");

    expect(headersOf(fetchMock).has("X-Tenant-Id")).toBe(false);
  });
});
