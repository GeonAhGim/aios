import { AiosApiClient, ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTenant } from "./useTenant";

const VALID_TENANT_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6";

// 모듈 스코프 싱글턴 스토어이므로 각 테스트 뒤 personal로 되돌려 격리한다.
afterEach(() => {
  const { result } = renderHook(() => useTenant());
  act(() => {
    result.current.setActiveTenant(null);
  });
});

describe("useTenant", () => {
  it("초기 상태는 personal(activeTenantId=null)이다", () => {
    const { result } = renderHook(() => useTenant());
    expect(result.current.activeTenantId).toBeNull();
    expect(result.current.tenantHeaders()).toEqual({});
  });

  it("setActiveTenant(유효 UUID)는 activeTenantId와 tenantHeaders를 갱신한다", () => {
    const { result } = renderHook(() => useTenant());

    act(() => {
      const accepted = result.current.setActiveTenant(VALID_TENANT_ID);
      expect(accepted).toBe(true);
    });

    expect(result.current.activeTenantId).toBe(VALID_TENANT_ID);
    expect(result.current.tenantHeaders()).toEqual({ "X-Tenant-Id": VALID_TENANT_ID });
  });

  it("비UUID tenantId는 거부하고 activeTenantId를 바꾸지 않는다", () => {
    const { result } = renderHook(() => useTenant());

    act(() => {
      result.current.setActiveTenant(VALID_TENANT_ID);
    });

    act(() => {
      const accepted = result.current.setActiveTenant("not-a-uuid");
      expect(accepted).toBe(false);
    });

    expect(result.current.activeTenantId).toBe(VALID_TENANT_ID);
  });

  it("handleForbidden(AUTH_TENANT_MISMATCH)은 personal로 폴백하고 폴백 사실을 반환한다", () => {
    const { result } = renderHook(() => useTenant());

    act(() => {
      result.current.setActiveTenant(VALID_TENANT_ID);
    });

    let fallback;
    act(() => {
      fallback = result.current.handleForbidden({
        statusCode: 403,
        errorCode: "AUTH_TENANT_MISMATCH",
      });
    });

    expect(fallback).toEqual({ previousTenantId: VALID_TENANT_ID });
    expect(result.current.activeTenantId).toBeNull();
    expect(result.current.tenantHeaders()).toEqual({});
  });

  it("handleForbidden(다른 에러)은 상태를 바꾸지 않고 null을 반환한다", () => {
    const { result } = renderHook(() => useTenant());

    act(() => {
      result.current.setActiveTenant(VALID_TENANT_ID);
    });

    let fallback;
    act(() => {
      fallback = result.current.handleForbidden({ statusCode: 403, errorCode: "AUTHZ_FORBIDDEN" });
    });

    expect(fallback).toBeNull();
    expect(result.current.activeTenantId).toBe(VALID_TENANT_ID);
  });
});

// task-1158 §3.5 실배선 회귀 가드. 607f832까지 이 훅은 tenantHeaders()만 노출하고
// api-client에 헤더 공급자를 등록하는 모듈 스코프 1줄이 없어 X-Tenant-Id가 실제
// 요청에 붙지 않았다(스토어 단위 테스트는 전부 통과하는 채로). 여기서는 이 모듈을
// import한 것만으로 진짜 AiosApiClient 요청에 헤더가 실리는지/빠지는지를 fetch
// 스텁으로 검사한다 — configureTenantHeadersProvider를 테스트가 직접 부르지 않는다.
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const ME_ENVELOPE = { data: { id: "user-1", email: "u@example.test" }, meta: {} };

// 호출마다 새 Response를 만드는 factory — 공유 Response 재사용 금지(Body has already been read).
function stubFetch(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(200, ME_ENVELOPE)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentHeaders(fetchMock: ReturnType<typeof vi.fn>, callIndex = 0): Headers {
  const [, init] = fetchMock.mock.calls[callIndex] as [string, RequestInit];
  return new Headers(init.headers);
}

describe("useTenant — X-Tenant-Id 실배선(§3.5)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("모듈 로드만으로 apiClient에 공급자가 등록된다: personal → 헤더 없음, 전환 → 부착, 복귀 → 제거", async () => {
    const fetchMock = stubFetch();
    const client = new AiosApiClient("https://api.example.test", () => "token");
    const { result } = renderHook(() => useTenant());

    await client.getMe();
    expect(sentHeaders(fetchMock, 0).has("X-Tenant-Id")).toBe(false);

    act(() => {
      result.current.setActiveTenant(VALID_TENANT_ID);
    });
    await client.getMe();
    expect(sentHeaders(fetchMock, 1).get("X-Tenant-Id")).toBe(VALID_TENANT_ID);

    act(() => {
      result.current.setActiveTenant(null);
    });
    await client.getMe();
    expect(sentHeaders(fetchMock, 2).has("X-Tenant-Id")).toBe(false);
  });

  it("negative: 빈 문자열 tenantId는 거부되어 빈 값의 X-Tenant-Id가 실리지 않는다", async () => {
    const fetchMock = stubFetch();
    const { result } = renderHook(() => useTenant());

    let accepted = true;
    act(() => {
      accepted = result.current.setActiveTenant("");
    });
    await new AiosApiClient("https://api.example.test", () => "token").getMe();

    expect(accepted).toBe(false);
    expect(sentHeaders(fetchMock).has("X-Tenant-Id")).toBe(false);
  });

  it("실 403 AUTH_TENANT_MISMATCH ApiError → classifyForbidden=tenant_mismatch → handleForbidden 폴백 → 다음 요청은 personal", async () => {
    const fetchMock = stubFetch().mockImplementationOnce(() =>
      Promise.resolve(
        jsonResponse(403, { error_code: "AUTH_TENANT_MISMATCH", message: "mismatch", trace_id: "t-1158" }),
      ),
    );
    const client = new AiosApiClient("https://api.example.test", () => "token");
    const { result } = renderHook(() => useTenant());
    act(() => {
      result.current.setActiveTenant(VALID_TENANT_ID);
    });

    const err = await client.getMe().catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(classifyForbidden(err)).toBe("tenant_mismatch");
    expect(routeApiError(err)).toEqual({ kind: "tenant_mismatch" });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    act(() => {
      expect(result.current.handleForbidden(err)).toEqual({ previousTenantId: VALID_TENANT_ID });
    });
    expect(result.current.activeTenantId).toBeNull();
    await client.getMe();
    expect(sentHeaders(fetchMock, 1).has("X-Tenant-Id")).toBe(false);
  });
});
