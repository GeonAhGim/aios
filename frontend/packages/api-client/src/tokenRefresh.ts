// L4 platform spec §3.3 error taxonomy: "AUTH_TOKEN_EXPIRED ... 재시도: expired만
// refresh". http.ts는 라우터/스토어를 직접 import하지 않으므로(순환 의존
// 방지 + 계층 분리, configureUnauthorizedHandler와 동일한 이유) 실제 refresh
// 호출(POST /auth/refresh + 새 access token 저장)은 상위 계층(앱 부트스트랩)이
// 이 훅으로 주입한다. 성공하면 true(재시도 가능), 실패(네트워크 오류·refresh
// token도 무효 등)하면 false(즉시 로그아웃 대상)를 반환해야 한다.
export type TokenRefreshHandler = () => Promise<boolean>;

let refreshHandler: TokenRefreshHandler | null = null;
let inFlightRefresh: Promise<boolean> | null = null;

export function configureTokenRefreshHandler(handler: TokenRefreshHandler | null): void {
  refreshHandler = handler;
  inFlightRefresh = null;
}

// 화면 진입 시 병렬로 나가는 여러 요청이 동시에 AUTH_TOKEN_EXPIRED를 받아도
// refresh는 1회만 호출한다 — refresh token은 사용 시 회전되고 이전 해시
// 재사용은 서버가 세션 폐기로 간주하므로(spec §2.3), 동시 refresh 호출은
// 두 번째 호출이 이미 회전된 토큰을 재사용하는 꼴이 되어 정상 세션까지
// 강제 로그아웃될 수 있다. 진행 중인 refresh가 있으면 그 결과를 공유한다.
export function refreshAccessToken(): Promise<boolean> {
  if (!refreshHandler) return Promise.resolve(false);
  if (!inFlightRefresh) {
    const handler = refreshHandler;
    inFlightRefresh = handler().finally(() => {
      inFlightRefresh = null;
    });
  }
  return inFlightRefresh;
}
