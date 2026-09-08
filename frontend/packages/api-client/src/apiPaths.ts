// L4 platform spec §3.3(ApiResponse 봉투는 "/api/v1 경로에만 적용, 레거시 alias는
// 구형 그대로 반환") + §9 PLT-16(mount_v1)/PLT-17~21(라우터별 봉투 이관).
// 서버가 모든 라우터를 /api/v1 아래로 옮기는 동안, 프론트 clients/*.ts에 흩어진
// 문자열 경로를 이 파일 하나로 모으고 legacy↔v1 전환 스위치(useV1)를 준비한다.
//
// 이 파일은 등록만 한다 — 기본값은 항상 legacy(useV1 미지정 시 false)이므로
// clients/*.ts를 단 한 줄도 바꾸지 않고도 안전하게 머지할 수 있다. 실제 클라이언트
// 배선(this.request 호출부를 resolvePath/requestByRoute로 교체)은 PLT-17~21 순서를
// 따라가는 후속 리프의 몫이다.
//
// task-2098(P6 300줄 상한): route()/defineApiRoutes()는 apiRouteTypes.ts로, 실제
// API_ROUTES 등록 표는 apiRoutes.ts로 분리했다. 이 파일은 그 둘을 재수출하고
// resolvePath/resolveEnvelope/isRouteImplemented 조회 함수만 갖는다 — 동작 변경 없음.

import type { ApiRouteDefinition } from "./apiRouteTypes";
import { API_ROUTES } from "./apiRoutes";

export type { ApiRouteDefinition } from "./apiRouteTypes";
export { defineApiRoutes } from "./apiRouteTypes";
export { API_ROUTES } from "./apiRoutes";

export type ApiRouteName = keyof typeof API_ROUTES;

export interface ResolvePathOptions {
  useV1?: boolean;
}

function getRouteDefinition(route: ApiRouteName): ApiRouteDefinition {
  const def = API_ROUTES[route];
  if (!def) {
    throw new Error(`apiPaths: 미등록 route입니다("${route}")`);
  }
  return def;
}

// v1Path가 없는 라우트는 useV1=true를 줘도 legacy로 폴백한다 — 서버 이관이
// 그 라우터에 아직 도달하지 않았다는 뜻이라 v1 경로 자체가 존재하지 않는다.
export function resolvePath(route: ApiRouteName, options: ResolvePathOptions = {}): string {
  const def = getRouteDefinition(route);
  if (options.useV1 && def.v1Path) return def.v1Path;
  return def.legacyPath;
}

// v1로 실제 해석됐을 때는 스펙상 항상 봉투가 적용된다(§3.3) — legacy로 폴백된
// 경우(useV1 미지정 포함)에만 라우트별 현재값을 쓴다.
export function resolveEnvelope(route: ApiRouteName, options: ResolvePathOptions = {}): boolean {
  const def = getRouteDefinition(route);
  if (options.useV1 && def.v1Path) return true;
  return def.envelope;
}

// task-1325: implemented=false(유령 경로)면 sessions.ts 같은 호출부가 네트워크 시도
// 전에 typed 오류로 단락할 수 있게 한다.
export function isRouteImplemented(route: ApiRouteName): boolean {
  return getRouteDefinition(route).implemented ?? true;
}
