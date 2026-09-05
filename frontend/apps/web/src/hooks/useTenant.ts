// spec §3.5 + §9 PLT-28. api-client/tenantContext.ts(createTenantStore)가
// 활성 테넌트 저장·헤더 빌더·403 AUTH_TENANT_MISMATCH 폴백 판정을 맡고,
// 이 훅은 그 위에 React 반응성(zustand)만 배선한다 — useAuthStore.ts와
// 동일한 분리 원칙(단방향 의존: 훅이 api-client를 알고, 그 반대는 아님).
//
// 실배선(task-1158 QA 발견·수정): task-455 당시 "http.ts 연결은 후속 리프"로
// 미뤄 둔 X-Tenant-Id 공급자 등록이 어디에서도 이뤄지지 않아 헤더가 실제 요청에
// 붙지 않았다. useAuthStore.ts가 configureUnauthorizedHandler를 모듈 스코프에서
// 1회 등록하는 선례와 동일하게, 이 모듈이 로드될 때 스토어의 tenantHeaders를
// api-client에 1회 주입한다 — personal(활성 테넌트 없음)이면 빈 객체라 헤더가
// 아예 실리지 않는다(빈 문자열 부착 금지, 서버가 헤더 부재=personal로 해석).
import {
  configureTenantHeadersProvider,
  createTenantStore,
  type TenantMismatchFallback,
} from "@aios/api-client";
import { create } from "zustand";

const tenantContext = createTenantStore();
configureTenantHeadersProvider(() => tenantContext.tenantHeaders());

interface TenantState {
  activeTenantId: string | null;
  setActiveTenant: (tenantId: string | null) => boolean;
  tenantHeaders: () => Record<string, string>;
  handleForbidden: (err: unknown) => TenantMismatchFallback | null;
}

// 모듈 스코프 싱글턴 스토어 — useAuthStore.ts와 동일하게, 여러 컴포넌트가
// 같은 활성 테넌트 상태를 공유해야 하기 때문이다.
const useTenantStore = create<TenantState>((set) => ({
  activeTenantId: tenantContext.getActiveTenant(),
  setActiveTenant: (tenantId) => {
    const accepted = tenantContext.setActiveTenant(tenantId);
    if (accepted) set({ activeTenantId: tenantContext.getActiveTenant() });
    return accepted;
  },
  tenantHeaders: () => tenantContext.tenantHeaders(),
  handleForbidden: (err) => {
    const fallback = tenantContext.handleForbidden(err);
    if (fallback) set({ activeTenantId: tenantContext.getActiveTenant() });
    return fallback;
  },
}));

export interface UseTenantResult {
  /** 현재 활성 tenant_id. personal이면 null. */
  activeTenantId: string | null;
  /** 활성 테넌트를 바꾼다. 비UUID는 거부(false)하고 기존 상태를 유지한다. */
  setActiveTenant: (tenantId: string | null) => boolean;
  /** 활성 테넌트가 있을 때만 X-Tenant-Id를 싣는 헤더 객체. */
  tenantHeaders: () => Record<string, string>;
  /**
   * 응답 에러를 검사해 403 AUTH_TENANT_MISMATCH면 personal로 폴백하고 그
   * 사실을 반환한다(호출자는 이 값으로 안내 배너를 띄울 수 있다). 그 외는 null.
   */
  handleForbidden: (err: unknown) => TenantMismatchFallback | null;
}

export function useTenant(): UseTenantResult {
  const activeTenantId = useTenantStore((s) => s.activeTenantId);
  const setActiveTenant = useTenantStore((s) => s.setActiveTenant);
  const tenantHeaders = useTenantStore((s) => s.tenantHeaders);
  const handleForbidden = useTenantStore((s) => s.handleForbidden);

  return { activeTenantId, setActiveTenant, tenantHeaders, handleForbidden };
}
