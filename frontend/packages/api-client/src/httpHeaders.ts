// task-801: http.ts(401줄, P6 300줄 규율 초과) 분할 — 이 모듈은 요청 헤더
// 조립(X-Request-Id/X-Tenant-Id/토큰) 책임만 담당한다. 순수 함수/상태만 두고
// fetch·재시도·봉투 분기(http.ts)나 멱등 계열(httpIdempotent.ts)은 건드리지
// 않는다 — client.ts 분할(task-132, 4aedd6c) 선례대로 동작을 바꾸지 않는 이동이다.

import { requestIdHeaders } from "./requestId";

export type TenantHeadersProvider = () => Record<string, string>;

let tenantHeadersProvider: TenantHeadersProvider | null = null;

// spec §3.5: 활성 테넌트가 있을 때만 X-Tenant-Id를 싣는다. configureUnauthorizedHandler와
// 같은 이유로(순환 의존 방지 + 계층 분리) api-client는 tenantContext.ts의
// createTenantStore 인스턴스를 직접 소유하지 않는다 — 앱 부트스트랩이
// useTenant.ts 등에서 만든 스토어의 tenantHeaders를 이 함수로 주입한다.
export function configureTenantHeadersProvider(provider: TenantHeadersProvider | null): void {
  tenantHeadersProvider = provider;
}

// task-481 decision: 401 refresh·403 step-up 재시도는 서버 관점에서 같은
// 논리 요청이므로 X-Request-Id를 원 요청과 동일하게 유지해야 한다.
// fetchJson은 매 호출마다 init.headers를 복사해 새 Headers를 만들 뿐
// init 자체를 변형하지 않으므로, 재시도 시 같은 init 객체를 그대로
// 넘겨도 헤더에 값이 없으면 매번 새 ID가 생성된다 — 이를 막기 위해
// performRequest(Envelope) 진입 시 1회만 ID를 확정해 init에 고정한다.
export function withStableRequestId(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  headers.set("X-Request-Id", requestIdHeaders(headers.get("X-Request-Id") ?? undefined)["X-Request-Id"]);
  return { ...init, headers };
}

// spec §3.1/§3.5: 요청마다 X-Request-Id를 싣고(호출자가 미리 실어 둔
// 유효값은 그대로 재사용), 활성 테넌트가 있으면 X-Tenant-Id도 싣는다.
// 서버가 아직 두 헤더를 다 처리하지 않아도 무해하므로 배선은 항상 켠다.
export function buildRequestHeaders(token: string | null, init?: RequestInit): Headers {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  headers.set("X-Request-Id", requestIdHeaders(headers.get("X-Request-Id") ?? undefined)["X-Request-Id"]);
  for (const [key, value] of Object.entries(tenantHeadersProvider?.() ?? {})) {
    headers.set(key, value);
  }

  return headers;
}

export type UnauthorizedHandler = (errorCode: string) => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;
let unauthorizedNotified = false;

// task-354: 401 AUTH_* 전역 처리용 훅. api-client는 라우터/스토어를 직접
// import하지 않고(순환 의존 방지 + 계층 분리) 상위 계층(앱 부트스트랩)이
// 이 함수로 콜백을 주입한다. 새 핸들러 등록(예: 재로그인 후 재구독)은
// 알림 가드도 함께 초기화한다.
export function configureUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
  unauthorizedNotified = false;
}

// 로그인 성공 등으로 새 세션이 시작되면 다음 401을 다시 알릴 수 있도록
// 가드를 푼다. useAuthStore.setToken이 호출한다.
export function resetUnauthorizedGuard(): void {
  unauthorizedNotified = false;
}

// 화면 진입 시 병렬로 나가는 여러 요청이 동시에 401을 받아도(예: 대시보드의
// useMe+usePortfolio+useExecutions) 콜백은 세션당 1회만 호출한다 —
// 중복 로그아웃·중복 리다이렉트를 막기 위함.
export function notifyUnauthorized(errorCode: string): void {
  if (unauthorizedNotified) return;
  unauthorizedNotified = true;
  unauthorizedHandler?.(errorCode);
}
