// spec §3.4 TokenPairResponse 저장소. 메모리 보관이 기본이며 refresh_token은
// 회전형 비밀값(spec: "응답에 1회만 노출, 로그 금지 DENY_KEYS 'token'")이므로
// toJSON/toString 등 직렬화 경로에서 절대 원문을 내보내지 않는다.
//
// 범위 제한(task-426 decision): 실제 갱신 수행(single-flight refresh + 원요청
// 재시도)은 task-386 tokenRefresh.ts 소관이다. 이 모듈은 저장·조회·회전을
// 담당한다.
//
// 배선(task-955): setPair가 성공할 때마다 shouldPreRefresh 기준으로 만료
// skewSec 전에 refreshAccessToken()(tokenRefresh.ts)을 선제 호출하도록 타이머를
// 예약한다. refreshAccessToken은 이미 single-flight이므로, 이 타이머가 쏜
// 선제 갱신과 그 사이 401을 맞아 http.ts가 발사하는 사후 갱신(task-386)이
// 겹쳐도 같은 in-flight promise를 공유해 실제 네트워크 호출은 1회로 수렴한다.
// 실제 토큰 갱신 결과 저장(성공 시 store.setPair 재호출)은 여전히
// TokenRefreshHandler(상위 계층이 configureTokenRefreshHandler로 주입) 소관이다
// — 이 모듈은 결과를 기다리지 않는다.
//
// 범위 제한(task-955 decision): 서버(src/api/routers/auth.py)는 아직 §3.4
// TokenPairResponse(PLT-23/24)를 반환하지 않고 TokenResponse{access_token,
// token_type}만 준다 — refresh_token/expires_in/session_id가 없어 setPair는
// 항상 null을 반환한다. 그래서 LoginPage.tsx/useLogout.ts 등 앱 계층은 여전히
// useAuthStore(access-token 전용, task-354)로 로그인 상태를 관리하고 이
// createTokenStore()는 아직 어디서도 인스턴스화되지 않는다. 앱을 이 저장소로
// 옮기려면 로그인 성공 시 원본(snake_case) 응답을 setPair에 먹이고 앱
// 부트스트랩이 configureTokenRefreshHandler를 등록하는 전역 provider 도입이
// 필요한데, 이는 이 leaf 범위를 벗어나 신설하지 않는다 — 서버가
// TokenPairResponse를 반환하기 시작하면 이 모듈을 그대로 앱 부트스트랩에
// 연결하면 된다.

import { parseTokenPair, shouldPreRefresh, type ParsedTokenPair } from "@aios/shared-types";
import { refreshAccessToken } from "./tokenRefresh";

const REDACTED = "[REDACTED]";

// tokenPair.ts의 shouldPreRefresh 기본 skewSec(60)과 반드시 같아야 한다 — 여기서는
// "언제 확인 타이머를 걸지"의 지연 시간 계산에, shouldPreRefresh 자체는 그
// 타이머가 쏘는 시점에 재확인 가드로 각각 쓰인다.
const PRE_REFRESH_SKEW_MS = 60_000;

export interface TokenStore {
  /** data를 §3.4 TokenPairResponse로 파싱해 저장한다. 이전 refresh_token은 즉시 폐기된다. */
  setPair(data: unknown): ParsedTokenPair | null;
  clear(): void;
  getAccess(): string | null;
  getRefresh(): string | null;
  peekSessionId(): string | null;
  toJSON(): Record<string, unknown>;
  toString(): string;
}

export function createTokenStore(): TokenStore {
  let accessToken: string | null = null;
  let refreshToken: string | null = null;
  let sessionId: string | null = null;
  let preRefreshTimer: ReturnType<typeof setTimeout> | null = null;

  function cancelPreRefresh(): void {
    if (preRefreshTimer === null) return;
    clearTimeout(preRefreshTimer);
    preRefreshTimer = null;
  }

  // 만료 skewSec 전에 refreshAccessToken()을 선제 호출하도록 타이머를 건다.
  // 타이머가 실제로 쏘는 시점에 shouldPreRefresh로 다시 확인하는 이유: 이
  // 사이 setPair가 재호출됐다면(회전) 이미 cancelPreRefresh로 취소됐겠지만,
  // 타이머 큐 지연 등으로 콜백이 취소 직전에 이미 실행 대기 중이었을 극단적
  // 경우까지 대비한 방어적 가드다.
  function schedulePreRefresh(expiresInSec: number): void {
    cancelPreRefresh();
    const expiresAtMs = Date.now() + expiresInSec * 1000;
    const delayMs = Math.max(0, expiresInSec * 1000 - PRE_REFRESH_SKEW_MS);
    preRefreshTimer = setTimeout(() => {
      preRefreshTimer = null;
      if (shouldPreRefresh(expiresAtMs, Date.now())) {
        void refreshAccessToken();
      }
    }, delayMs);
    // Node 환경(SSR·테스트)에서 이 타이머 하나 때문에 프로세스가 계속 떠 있지
    // 않도록 unref한다 — 브라우저 setTimeout 반환값에는 없는 메서드라 optional.
    (preRefreshTimer as unknown as { unref?: () => void }).unref?.();
  }

  function clear(): void {
    cancelPreRefresh();
    accessToken = null;
    refreshToken = null;
    sessionId = null;
  }

  function setPair(data: unknown): ParsedTokenPair | null {
    const parsed = parseTokenPair(data);
    if (!parsed) return null;

    // 회전: 재대입이 이전 refresh_token 참조를 즉시 잃게 만든다.
    accessToken = parsed.accessToken;
    refreshToken = parsed.refreshToken;
    sessionId = parsed.sessionId;
    schedulePreRefresh(parsed.expiresIn);
    return parsed;
  }

  function toJSON(): Record<string, unknown> {
    return {
      accessToken: accessToken ? REDACTED : null,
      refreshToken: refreshToken ? REDACTED : null,
      sessionId,
    };
  }

  return {
    setPair,
    clear,
    getAccess: () => accessToken,
    getRefresh: () => refreshToken,
    peekSessionId: () => sessionId,
    toJSON,
    toString: () => JSON.stringify(toJSON()),
  };
}
