import { describe, expect, it } from "vitest";
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
