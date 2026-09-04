// spec §3.4 인증 토큰·세션 + §9 PLT-23/24. decision(task-606): 서버 세션
// 라우트(GET /auth/sessions, DELETE /auth/sessions/{id})가 아직 없을 수 있다 —
// task-454(로그아웃 클라이언트) 선례대로 계약·클라이언트 모듈과 vitest만 두고
// 실서버 호출 검증은 요구하지 않는다. 엔드포인트 경로는 §3.4 규격대로 고정하되,
// 서버가 다른 경로를 쓰는 것으로 밝혀지면 이 파일이 아니라 task note에 남긴다.
//
// task-1325: 위 decision의 "아직 없을 수 있다"가 실제로 확인됐다(src/api/routers에
// 세션 라우터 없음 — apiPaths.ts의 auth.sessions.*가 implemented=false로 등록돼
// GHOST_PATH_WHITELIST와 정합된다). 이전 구현은 그 사실을 모른 채 매번 실제로 fetch를
// 시도했고, revoke()의 404 흡수(§3.3 RESOURCE_NOT_FOUND 전용이 아니라 "statusCode===404
// 전부"를 흡수하는 isResourceNotFound)가 라우트가 아예 없어서 나는 FastAPI 기본 404
// (봉투 없음·error_code 없음)까지 "이미 폐기됨 → 성공"으로 조용히 삼켰다 — 실제로는
// 아무 일도 없었는데 UI는 로그아웃 성공처럼 보이는 침묵 실패였다. 이제 list()/revoke()
// 둘 다 apiPaths.ts의 implemented 플래그를 먼저 확인해 fetch를 시도하기 전에
// SessionsRouteNotImplementedError(typed)로 즉시 실패한다 — 라우터가 생기면(§9 PLT
// 후속 리프) apiPaths.ts의 implemented만 true로 바꾸면 이 단락 없이 그대로 배선된다.
//
// logout.ts는 "베스트 에포트"(서버 결과 무시, 로컬 정리만 보장)라 의도적으로
// http.ts를 우회했지만, 세션 목록/개별 폐기는 실패를 사용자에게 그대로 보여줘야
// 하는 일반 조회·변경 요청이므로 이 모듈은 http.ts(ApiClientBase)를 상속해서만
// 쓴다 — auth 라우터의 나머지 엔드포인트(clients/auth.ts)와 동일하게 ApiResponse
// 봉투(requestEnvelope) 경로를 탄다.
import { canRevoke, isResourceNotFound, parseSessionView, type ParsedSessionView } from "@aios/shared-types";
import { isRouteImplemented as defaultIsRouteImplemented, resolvePath, type ApiRouteName } from "./apiPaths";
import { ApiClientBase } from "./http";
import type { LogoutClient, LogoutTokenStore } from "./logout";

export type { ParsedSessionView };
export { canRevoke, parseSessionView };

const NOT_IMPLEMENTED_MESSAGE: Record<"auth.sessions.list" | "auth.sessions.revoke", string> = {
  "auth.sessions.list": "세션 목록 조회 API가 아직 제공되지 않습니다.",
  "auth.sessions.revoke": "세션 폐기 API가 아직 제공되지 않습니다.",
};

// task-1325: §3.3 유령 경로를 typed 오류로 노출한다 — 호출부(SessionsPage.tsx 등)가
// 일반 Error 메시지 매칭이 아니라 instanceof/route 필드로 "아직 없는 라우트"를
// 판별할 수 있게 한다.
export class SessionsRouteNotImplementedError extends Error {
  readonly route: ApiRouteName;

  constructor(route: "auth.sessions.list" | "auth.sessions.revoke") {
    super(NOT_IMPLEMENTED_MESSAGE[route]);
    this.name = "SessionsRouteNotImplementedError";
    this.route = route;
  }
}

export interface SessionsClientOptions {
  baseUrl: string;
  getToken: () => string | null;
  /** 로컬에 보관 중인 access token이 속한 세션의 id. 없으면(미확보 등) null. */
  getCurrentSessionId: () => string | null;
  store: LogoutTokenStore;
  /** revokeAll()은 이 로그아웃 클라이언트의 logoutAll을 그대로 위임한다 —
   * 전체 세션 폐기 로직을 이 파일에서 다시 구현하지 않는다(중복 구현 금지). */
  logoutClient: Pick<LogoutClient, "logoutAll">;
  /** task-1325: 라우트 구현 여부 판정을 주입한다. 기본값은 apiPaths.ts의 실제
   * 레지스트리(isRouteImplemented)를 그대로 따른다 — 라우터가 있다고 가정한
   * 시나리오를 검증하는 테스트만 override한다. */
  isRouteImplemented?: (route: ApiRouteName) => boolean;
}

export interface SessionsClient {
  /** GET /auth/sessions. §3.4 화이트리스트 파싱에 실패한(필수 필드 누락) 항목은
   * 예외 없이 조용히 걸러낸다. 라우트가 아직 없으면(현재 항상) 네트워크 호출 전에
   * SessionsRouteNotImplementedError로 실패한다. */
  list(): Promise<ParsedSessionView[]>;
  /** DELETE /auth/sessions/{sessionId}. 404 RESOURCE_NOT_FOUND(이미 폐기됨)는
   * 성공으로 흡수한다. 대상이 현재 세션이면 서버 응답과 무관하게(흡수 포함)
   * 로컬 토큰도 함께 정리한다. 401 AUTH_SESSION_REVOKED 등 그 외 에러는 그대로
   * 던져 http.ts의 전역 401 핸들러(task-354)가 처리하게 둔다. 라우트가 아직
   * 없으면(현재 항상) 네트워크 호출·로컬 토큰 정리 전에 SessionsRouteNotImplementedError로
   * 실패한다(실제로 폐기되지 않았는데 로그아웃된 것처럼 보이면 안 되므로). */
  revoke(sessionId: string): Promise<void>;
  /** logout.ts의 logoutAll을 그대로 호출한다. */
  revokeAll(): Promise<void>;
}

class SessionsHttpClient extends ApiClientBase {
  listRaw(): Promise<unknown> {
    return this.requestEnvelope<unknown>(resolvePath("auth.sessions.list"));
  }

  deleteSession(sessionId: string): Promise<void> {
    const path = resolvePath("auth.sessions.revoke").replace(":sessionId", encodeURIComponent(sessionId));
    return this.requestEnvelope<void>(path, { method: "DELETE" });
  }
}

export function createSessionsClient(options: SessionsClientOptions): SessionsClient {
  const { baseUrl, getToken, getCurrentSessionId, store, logoutClient } = options;
  const checkImplemented = options.isRouteImplemented ?? defaultIsRouteImplemented;
  const http = new SessionsHttpClient(baseUrl, getToken);

  function assertImplemented(route: "auth.sessions.list" | "auth.sessions.revoke"): void {
    if (!checkImplemented(route)) {
      throw new SessionsRouteNotImplementedError(route);
    }
  }

  async function list(): Promise<ParsedSessionView[]> {
    assertImplemented("auth.sessions.list");
    const raw = await http.listRaw();
    const items = Array.isArray(raw) ? raw : [];
    return items
      .map((item) => parseSessionView(item))
      .filter((view): view is ParsedSessionView => view !== null);
  }

  async function revoke(sessionId: string): Promise<void> {
    assertImplemented("auth.sessions.revoke");
    try {
      await http.deleteSession(sessionId);
    } catch (err) {
      if (!isResourceNotFound(err)) throw err;
    }
    if (sessionId === getCurrentSessionId()) {
      store.clear();
    }
  }

  return {
    list,
    revoke,
    revokeAll: () => logoutClient.logoutAll(),
  };
}
