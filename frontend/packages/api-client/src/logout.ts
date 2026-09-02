// spec §3.4 인증 토큰·세션 + §9 PLT-24. 감사 지적("로그아웃 no-op"):
// 서버 /auth/logout·/auth/logout-all은 PLT-24 미구현이라 200을 보장하지
// 않는다(404·501 포함). 이 leaf의 decision에 따라 서버 응답과 무관하게
// 로컬 토큰 정리는 항상 성공해야 하므로, 이 모듈은 서버 호출 결과를
// 절대 사용자에게 노출하지 않고(throw 없음) 로컬 정리만 보장한다.
//
// 토큰 저장(task-426 tokenStore)·자동 refresh(task-386 tokenRefresh)는
// 그대로 재사용한다 — 이 파일은 새 저장소·새 401 핸들러를 만들지 않는다.
// http.ts(ApiClientBase)를 거치지 않는 이유: 그 경로는 401을 만나면
// refresh 후 재시도를 시도하는데(task-386), 로그아웃 요청 자체가 그
// 사이클에 다시 들어가는 것은 의미가 없고 봉투 파싱 실패(404/501 등
// 비봉투 응답)로 예외가 날 수도 있다. 로그아웃은 항상 "베스트 에포트"
// 요청 1번 + 로컬 정리로 끝난다.
import { configureTokenRefreshHandler } from "./tokenRefresh";

// tokenStore.ts의 TokenStore와 구조적으로 호환되는 최소 계약만 요구한다 —
// 실제 프로덕션 토큰 저장소(zustand 등)를 tokenStore.ts로 감싸지 않고도
// 그대로 주입할 수 있도록 하기 위함이다.
export interface LogoutTokenStore {
  clear(): void;
}

export interface LogoutClientOptions {
  baseUrl: string;
  getToken: () => string | null;
  store: LogoutTokenStore;
}

export interface LogoutClient {
  /** POST /auth/logout(현재 세션) 후 서버 결과와 무관하게 로컬 정리. */
  logout(): Promise<void>;
  /** POST /auth/logout-all(전체 세션) 후 서버 결과와 무관하게 로컬 정리. */
  logoutAll(): Promise<void>;
}

async function postBestEffort(baseUrl: string, path: string, token: string | null): Promise<void> {
  try {
    const headers = new Headers({ "Content-Type": "application/json" });
    if (token) headers.set("Authorization", `Bearer ${token}`);
    await fetch(`${baseUrl}${path}`, { method: "POST", headers });
  } catch {
    // 네트워크 오류도 서버 미응답과 동일하게 취급한다 — 로컬 정리는 진행.
  }
}

export function createLogoutClient(options: LogoutClientOptions): LogoutClient {
  const { baseUrl, getToken, store } = options;
  let inFlightLogout: Promise<void> | null = null;
  let inFlightLogoutAll: Promise<void> | null = null;

  // 정리 직전에 refresh 핸들러를 해제해 이후 refreshAccessToken() 호출이
  // 즉시 false로 해소되게 한다 — 정리 직후 뒤늦게 도착하는 자동 refresh가
  // 토큰을 되살리는 경합을 막기 위함(이 leaf의 decision 3번 항목).
  function cleanup(): void {
    configureTokenRefreshHandler(null);
    store.clear();
  }

  function logout(): Promise<void> {
    if (!inFlightLogout) {
      inFlightLogout = postBestEffort(baseUrl, "/auth/logout", getToken()).finally(() => {
        cleanup();
        inFlightLogout = null;
      });
    }
    return inFlightLogout;
  }

  function logoutAll(): Promise<void> {
    if (!inFlightLogoutAll) {
      inFlightLogoutAll = postBestEffort(baseUrl, "/auth/logout-all", getToken()).finally(() => {
        cleanup();
        inFlightLogoutAll = null;
      });
    }
    return inFlightLogoutAll;
  }

  return { logout, logoutAll };
}
