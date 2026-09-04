// L4 platform spec §3.3 error taxonomy: "AUTH_TOKEN_EXPIRED ... 재시도: expired만
// refresh". http.ts는 라우터/스토어를 직접 import하지 않으므로(순환 의존
// 방지 + 계층 분리, configureUnauthorizedHandler와 동일한 이유) 실제 refresh
// 호출(POST /auth/refresh + 새 access token 저장)은 상위 계층(앱 부트스트랩)이
// 이 훅으로 주입한다. 성공하면 true(재시도 가능), 실패(네트워크 오류·refresh
// token도 무효 등)하면 false(즉시 로그아웃 대상)를 반환해야 한다.
export type TokenRefreshHandler = () => Promise<boolean>;

// task-1020(§3.4/§9 PLT-23): refresh 회전 재사용 감지·기타 401/403 실패는 서버가
// 세션 전체를 revoke하므로, 클라이언트도 보관 중인 토큰 쌍을 전량 폐기해야
// 한다(재시도 절대 금지 — 무한 401 루프 방지는 이미 handleAuthFailure가
// `if (refreshed) return retry()`로 보장한다). 이 모듈은 tokenStore.ts를 직접
// import하지 않는다(tokenStore.ts가 이미 이 모듈을 import하므로 역참조하면
// 순환 의존이 된다) — tokenStore.ts가 자신의 clear()를 이 훅에 등록한다. 전역
// 로그아웃 알림은 이미 http.ts(handleAuthFailure, task-386)가 notifyUnauthorized로
// 처리하므로 여기서 새로 호출하지 않는다(중복 알림 방지, task-354 가드 재사용).
export type TokenClearHandler = () => void;

let refreshHandler: TokenRefreshHandler | null = null;
let clearHandler: TokenClearHandler | null = null;
let inFlightRefresh: Promise<boolean> | null = null;

export function configureTokenRefreshHandler(handler: TokenRefreshHandler | null): void {
  refreshHandler = handler;
  inFlightRefresh = null;
}

export function configureTokenClearHandler(handler: TokenClearHandler | null): void {
  clearHandler = handler;
}

// 화면 진입 시 병렬로 나가는 여러 요청이 동시에 AUTH_TOKEN_EXPIRED를 받아도
// refresh는 1회만 호출한다 — refresh token은 사용 시 회전되고 이전 해시
// 재사용은 서버가 세션 폐기로 간주하므로(spec §2.3), 동시 refresh 호출은
// 두 번째 호출이 이미 회전된 토큰을 재사용하는 꼴이 되어 정상 세션까지
// 강제 로그아웃될 수 있다. 진행 중인 refresh가 있으면 그 결과를 공유한다.
//
// task-1380(§3.4/§9 PLT-23, task-1373 리뷰 REJECT 후속): TokenRefreshHandler
// 계약(주석 위 5-6행)은 "실패하면 false를 반환"이지만, 주입되는 실제 핸들러가
// 이를 어기고 reject하거나(네트워크 예외를 catch하지 않고 전파 등) 동기적으로
// throw할 수 있다 — 이전 구현은 그 경로에서 .then의 성공 콜백만 clearHandler를
// 불렀으므로 실패가 전량 폐기(clearHandler) 없이 그대로 다시 던져졌고(우회),
// tokenStore.ts:85 `void refreshAccessToken()`(선제 갱신 타이머)처럼 결과를
// catch하지 않는 호출부에서는 unhandled rejection까지 발생했다. handler()는
// (동시 호출 시 진행 중인 promise를 즉시 공유해야 하므로) 여전히 같은 tick에
// 동기 호출하되, 그 호출 자체를 try/catch로 감싸 동기 throw를 Promise.reject로
// 정규화하고, .then의 두 번째 인자(onRejected)에서도 반드시 clearHandler를
// 호출한 뒤 false로 수렴시켜 이 함수가 항상 resolve만 하도록 보장한다(문서화된
// Promise<boolean> 계약 유지, reject 경로 제거).
export function refreshAccessToken(): Promise<boolean> {
  if (!refreshHandler) return Promise.resolve(false);
  if (!inFlightRefresh) {
    const handler = refreshHandler;
    let started: Promise<boolean>;
    try {
      started = Promise.resolve(handler());
    } catch (err) {
      started = Promise.reject(err);
    }
    inFlightRefresh = started
      .then(
        (ok) => {
          if (!ok) clearHandler?.();
          return ok;
        },
        () => {
          clearHandler?.();
          return false;
        },
      )
      .finally(() => {
        inFlightRefresh = null;
      });
  }
  return inFlightRefresh;
}
