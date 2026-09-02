import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
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
