// spec §3.5 테넌트 계약 + §9 PLT-28. resolve_tenant_context는 `X-Tenant-Id`
// 헤더가 없으면 personal tenant로 해석하고, membership이 없거나 비활성이면
// 403 AUTH_TENANT_MISMATCH를 던진다(§3.5 표, contracts/v1.py TenantKind·
// MembershipRole enum). 이 모듈은 그 계약의 클라이언트 대응만 담당한다 —
// 활성 테넌트 저장, 헤더 빌더, 403 수신 시 personal 폴백.
//
// 403 분류는 task-393(classifyForbidden)을 그대로 재사용한다 — 이 파일은
// 새 403 분류기를 만들지 않는다(이 leaf의 decision). TenantKind·MembershipRole은
// §3.5 enum 값 그대로 문자열 유니온으로 선언만 하며, 서버 미구현 라우트인
// trust_memberships(PLT-29)는 호출하지 않는다.
//
// 범위 제한(이 leaf의 decision): http.ts는 건드리지 않는다(task-413/414/415/427/454와
// 동시 수정 충돌 방지). 이 모듈은 순수 저장소·헤더 빌더·폴백 판정만 제공하고,
// 실제 요청에 헤더를 주입하는 배선은 후속 리프에서 한다.
import { classifyForbidden } from "@aios/shared-types";

/** contracts/v1.py TenantKind enum과 1:1 대응. */
export type TenantKind = "PERSONAL" | "HOUSEHOLD" | "ORGANIZATION";

/** contracts/v1.py MembershipRole enum과 1:1 대응. */
export type MembershipRole = "OWNER" | "ADMIN" | "MEMBER" | "AUDITOR" | "SERVICE";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** tenant_id는 UUID다 — 형식이 아닌 값은 활성 테넌트로 설정을 거부한다. */
export function isValidTenantId(value: string): boolean {
  return UUID_PATTERN.test(value);
}

/** 403 AUTH_TENANT_MISMATCH로 personal 폴백이 일어났음을 호출자에게 알리는 사실. */
export interface TenantMismatchFallback {
  /** 폴백 직전 활성 상태였던 tenant_id. 이미 personal(null)이었으면 null. */
  readonly previousTenantId: string | null;
}

export interface TenantStore {
  /**
   * 활성 테넌트를 설정한다. tenantId가 null이면 personal로 되돌리고 항상 성공한다.
   * 비UUID 문자열은 거부하고(false) 기존 상태를 유지한다.
   */
  setActiveTenant(tenantId: string | null): boolean;
  /** 현재 활성 tenant_id. personal이면 null. */
  getActiveTenant(): string | null;
  /**
   * 활성 테넌트가 있을 때만 {"X-Tenant-Id": id}를 반환한다. 없으면 빈 객체 —
   * 서버가 헤더 부재를 personal로 해석하는 계약(§3.5)과 대칭.
   */
  tenantHeaders(): Record<string, string>;
  /**
   * 응답 에러가 403 AUTH_TENANT_MISMATCH면 활성 테넌트를 해제하고 폴백 사실을
   * 반환한다. 그 외 에러(다른 403 갈래·403이 아님)는 상태를 바꾸지 않고 null.
   */
  handleForbidden(err: unknown): TenantMismatchFallback | null;
}

/** §3.5 클라이언트 테넌트 컨텍스트 스토어. 프레임워크 비의존 — React 배선은 useTenant.ts 몫. */
export function createTenantStore(): TenantStore {
  let activeTenantId: string | null = null;

  function setActiveTenant(tenantId: string | null): boolean {
    if (tenantId === null) {
      activeTenantId = null;
      return true;
    }
    if (!isValidTenantId(tenantId)) return false;
    activeTenantId = tenantId;
    return true;
  }

  function tenantHeaders(): Record<string, string> {
    return activeTenantId ? { "X-Tenant-Id": activeTenantId } : {};
  }

  function handleForbidden(err: unknown): TenantMismatchFallback | null {
    if (classifyForbidden(err) !== "tenant_mismatch") return null;
    const previousTenantId = activeTenantId;
    activeTenantId = null;
    return { previousTenantId };
  }

  return {
    setActiveTenant,
    getActiveTenant: () => activeTenantId,
    tenantHeaders,
    handleForbidden,
  };
}
