import { classifyForbidden } from "@aios/shared-types";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase, ApiError, configureTenantHeadersProvider } from "./http";
import { createTenantStore, isValidTenantId } from "./tenantContext";

const VALID_TENANT_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6";

describe("isValidTenantId", () => {
  it("정상 UUID는 통과한다", () => {
    expect(isValidTenantId(VALID_TENANT_ID)).toBe(true);
  });

  it("UUID가 아닌 문자열은 거부한다", () => {
    expect(isValidTenantId("not-a-uuid")).toBe(false);
  });

  it("UUID와 길이는 같지만 구획이 다른 문자열은 거부한다", () => {
    expect(isValidTenantId("3fa85f6457174562b3fc2c963f66afa6")).toBe(false);
  });
});

describe("tenantHeaders", () => {
  it("활성 테넌트가 없으면(personal) 빈 객체를 반환한다", () => {
    const store = createTenantStore();
    expect(store.tenantHeaders()).toEqual({});
  });

  it("활성 테넌트가 있으면 X-Tenant-Id 헤더를 반환한다", () => {
    const store = createTenantStore();
    store.setActiveTenant(VALID_TENANT_ID);
    expect(store.tenantHeaders()).toEqual({ "X-Tenant-Id": VALID_TENANT_ID });
  });
});

describe("setActiveTenant", () => {
  it("유효한 UUID를 설정하면 getActiveTenant가 그 값을 반환한다", () => {
    const store = createTenantStore();
    expect(store.setActiveTenant(VALID_TENANT_ID)).toBe(true);
    expect(store.getActiveTenant()).toBe(VALID_TENANT_ID);
  });

  it("비UUID tenantId는 거부하고(false) 기존 상태를 유지한다", () => {
    const store = createTenantStore();
    store.setActiveTenant(VALID_TENANT_ID);

    expect(store.setActiveTenant("bad-id")).toBe(false);
    expect(store.getActiveTenant()).toBe(VALID_TENANT_ID);
  });

  it("null을 설정하면 personal로 되돌아가고 항상 성공한다", () => {
    const store = createTenantStore();
    store.setActiveTenant(VALID_TENANT_ID);

    expect(store.setActiveTenant(null)).toBe(true);
    expect(store.getActiveTenant()).toBeNull();
  });
});

describe("handleForbidden", () => {
  it("403 AUTH_TENANT_MISMATCH면 활성 테넌트를 해제하고 폴백 사실을 반환한다", () => {
    const store = createTenantStore();
    store.setActiveTenant(VALID_TENANT_ID);

    const fallback = store.handleForbidden({ statusCode: 403, errorCode: "AUTH_TENANT_MISMATCH" });

    expect(fallback).toEqual({ previousTenantId: VALID_TENANT_ID });
    expect(store.getActiveTenant()).toBeNull();
    expect(store.tenantHeaders()).toEqual({});
  });

  it("다른 403 갈래(AUTHZ_FORBIDDEN)는 상태를 바꾸지 않고 null을 반환한다", () => {
    const store = createTenantStore();
    store.setActiveTenant(VALID_TENANT_ID);

    const fallback = store.handleForbidden({ statusCode: 403, errorCode: "AUTHZ_FORBIDDEN" });

    expect(fallback).toBeNull();
    expect(store.getActiveTenant()).toBe(VALID_TENANT_ID);
  });

  it("403이 아닌 에러는 상태를 바꾸지 않고 null을 반환한다", () => {
    const store = createTenantStore();
    store.setActiveTenant(VALID_TENANT_ID);

    const fallback = store.handleForbidden({ statusCode: 401, errorCode: "AUTH_TOKEN_EXPIRED" });

    expect(fallback).toBeNull();
    expect(store.getActiveTenant()).toBe(VALID_TENANT_ID);
  });

  it("이미 personal 상태에서 mismatch를 받아도 폴백 사실은 반환한다(previousTenantId=null)", () => {
    const store = createTenantStore();

    const fallback = store.handleForbidden({ statusCode: 403, errorCode: "AUTH_TENANT_MISMATCH" });

    expect(fallback).toEqual({ previousTenantId: null });
    expect(store.getActiveTenant()).toBeNull();
  });
});

// task-1158 §3.5 실배선 회귀 가드. 스토어 단위 동작(위)만으로는 "헤더가 실제
// 요청에 실리는가"를 증명하지 못한다(I-10: 배선·우회불가·증명). 여기서는
// 스토어의 tenantHeaders를 configureTenantHeadersProvider로 주입한 뒤 진짜
// ApiClientBase.fetchJson이 만드는 Headers를 검사한다 — 새 분류기·새 스토어 없음.
class TestClient extends ApiClientBase {
  get<T>(path: string): Promise<T> {
    return this.request<T>(path);
  }
}

// fetch mock은 호출마다 새 Response를 만드는 factory여야 한다 — 공유 Response를
// 재사용하면 두 번째 호출에서 'Body has already been read'로 깨진다.
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stubFetch(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(200, { ok: true })));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentHeaders(fetchMock: ReturnType<typeof vi.fn>, callIndex = 0): Headers {
  const [, init] = fetchMock.mock.calls[callIndex] as [string, RequestInit];
  return new Headers(init.headers);
}

const MISMATCH_BODY = {
  error_code: "AUTH_TENANT_MISMATCH",
  message: "tenant mismatch",
  trace_id: "trace-1158",
};

describe("§3.5 실배선 회귀 가드 — tenantHeaders → ApiClientBase 요청 헤더", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    configureTenantHeadersProvider(null);
  });

  it("personal(활성 테넌트 없음)이면 X-Tenant-Id 키 자체를 만들지 않는다(빈 문자열 부착 금지)", async () => {
    const store = createTenantStore();
    configureTenantHeadersProvider(store.tenantHeaders);
    const fetchMock = stubFetch();

    expect("X-Tenant-Id" in store.tenantHeaders()).toBe(false);
    await new TestClient("https://api.example.test", () => null).get("/ping");

    expect(sentHeaders(fetchMock).has("X-Tenant-Id")).toBe(false);
  });

  it("빈 문자열 tenantId는 거부되어(false) 요청에 X-Tenant-Id: '' 가 실리지 않는다", async () => {
    const store = createTenantStore();
    configureTenantHeadersProvider(store.tenantHeaders);
    const fetchMock = stubFetch();

    expect(store.setActiveTenant("")).toBe(false);
    await new TestClient("https://api.example.test", () => null).get("/ping");

    expect(sentHeaders(fetchMock).has("X-Tenant-Id")).toBe(false);
  });

  it("활성 테넌트를 설정하면 이후 요청마다 X-Tenant-Id가 실린다", async () => {
    const store = createTenantStore();
    configureTenantHeadersProvider(store.tenantHeaders);
    const fetchMock = stubFetch();
    const client = new TestClient("https://api.example.test", () => null);

    store.setActiveTenant(VALID_TENANT_ID);
    await client.get("/first");
    await client.get("/second");

    expect(sentHeaders(fetchMock, 0).get("X-Tenant-Id")).toBe(VALID_TENANT_ID);
    expect(sentHeaders(fetchMock, 1).get("X-Tenant-Id")).toBe(VALID_TENANT_ID);
  });

  it("실 403 AUTH_TENANT_MISMATCH ApiError는 classifyForbidden 경로로 tenant_mismatch가 되고, handleForbidden 뒤 다음 요청은 personal(헤더 없음)이다", async () => {
    const store = createTenantStore();
    configureTenantHeadersProvider(store.tenantHeaders);
    const fetchMock = stubFetch().mockImplementationOnce(() => Promise.resolve(jsonResponse(403, MISMATCH_BODY)));
    const client = new TestClient("https://api.example.test", () => null);
    store.setActiveTenant(VALID_TENANT_ID);

    const err = await client.get("/scoped").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(classifyForbidden(err)).toBe("tenant_mismatch");
    // 403은 GET 자동 재시도 대상이 아니다 — 서버 왕복 1회로 고정.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentHeaders(fetchMock, 0).get("X-Tenant-Id")).toBe(VALID_TENANT_ID);

    expect(store.handleForbidden(err)).toEqual({ previousTenantId: VALID_TENANT_ID });
    await client.get("/after-fallback");
    expect(sentHeaders(fetchMock, 1).has("X-Tenant-Id")).toBe(false);
  });

  it("negative: 실 403 AUTHZ_FORBIDDEN은 폴백하지 않아 다음 요청에도 X-Tenant-Id가 유지된다", async () => {
    const store = createTenantStore();
    configureTenantHeadersProvider(store.tenantHeaders);
    const fetchMock = stubFetch().mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(403, { ...MISMATCH_BODY, error_code: "AUTHZ_FORBIDDEN" })),
    );
    const client = new TestClient("https://api.example.test", () => null);
    store.setActiveTenant(VALID_TENANT_ID);

    const err = await client.get("/scoped").catch((e: unknown) => e);

    expect(classifyForbidden(err)).toBe("forbidden");
    expect(store.handleForbidden(err)).toBeNull();
    await client.get("/still-scoped");
    expect(sentHeaders(fetchMock, 1).get("X-Tenant-Id")).toBe(VALID_TENANT_ID);
  });
});
