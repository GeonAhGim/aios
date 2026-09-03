// spec §3.4 인증 토큰·세션 + §9 PLT-23/24. decision(task-606): 서버 세션
// 라우트(GET /auth/sessions, DELETE /auth/sessions/{id})가 아직 없을 수 있다 —
// task-454(로그아웃 클라이언트) 선례대로 계약·클라이언트 모듈과 vitest만 두고
// 실서버 호출 검증은 요구하지 않는다. 엔드포인트 경로는 §3.4 규격대로 고정하되,
// 서버가 다른 경로를 쓰는 것으로 밝혀지면 이 파일이 아니라 task note에 남긴다.
//
// logout.ts는 "베스트 에포트"(서버 결과 무시, 로컬 정리만 보장)라 의도적으로
// http.ts를 우회했지만, 세션 목록/개별 폐기는 실패를 사용자에게 그대로 보여줘야
// 하는 일반 조회·변경 요청이므로 이 모듈은 http.ts(ApiClientBase)를 상속해서만
// 쓴다 — auth 라우터의 나머지 엔드포인트(clients/auth.ts)와 동일하게 ApiResponse
// 봉투(requestEnvelope) 경로를 탄다.
import { canRevoke, isResourceNotFound, parseSessionView, type ParsedSessionView } from "@aios/shared-types";
import { ApiClientBase } from "./http";
import type { LogoutClient, LogoutTokenStore } from "./logout";

export type { ParsedSessionView };
export { canRevoke, parseSessionView };

export interface SessionsClientOptions {
  baseUrl: string;
  getToken: () => string | null;
  /** 로컬에 보관 중인 access token이 속한 세션의 id. 없으면(미확보 등) null. */
  getCurrentSessionId: () => string | null;
  store: LogoutTokenStore;
  /** revokeAll()은 이 로그아웃 클라이언트의 logoutAll을 그대로 위임한다 —
   * 전체 세션 폐기 로직을 이 파일에서 다시 구현하지 않는다(중복 구현 금지). */
  logoutClient: Pick<LogoutClient, "logoutAll">;
}

export interface SessionsClient {
  /** GET /auth/sessions. §3.4 화이트리스트 파싱에 실패한(필수 필드 누락) 항목은
   * 예외 없이 조용히 걸러낸다. */
  list(): Promise<ParsedSessionView[]>;
  /** DELETE /auth/sessions/{sessionId}. 404 RESOURCE_NOT_FOUND(이미 폐기됨)는
   * 성공으로 흡수한다. 대상이 현재 세션이면 서버 응답과 무관하게(흡수 포함)
   * 로컬 토큰도 함께 정리한다. 401 AUTH_SESSION_REVOKED 등 그 외 에러는 그대로
   * 던져 http.ts의 전역 401 핸들러(task-354)가 처리하게 둔다. */
  revoke(sessionId: string): Promise<void>;
  /** logout.ts의 logoutAll을 그대로 호출한다. */
  revokeAll(): Promise<void>;
}

class SessionsHttpClient extends ApiClientBase {
  listRaw(): Promise<unknown> {
    return this.requestEnvelope<unknown>("/auth/sessions");
  }

  deleteSession(sessionId: string): Promise<void> {
    return this.requestEnvelope<void>(`/auth/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
  }
}

export function createSessionsClient(options: SessionsClientOptions): SessionsClient {
  const { baseUrl, getToken, getCurrentSessionId, store, logoutClient } = options;
  const http = new SessionsHttpClient(baseUrl, getToken);

  async function list(): Promise<ParsedSessionView[]> {
    const raw = await http.listRaw();
    const items = Array.isArray(raw) ? raw : [];
    return items
      .map((item) => parseSessionView(item))
      .filter((view): view is ParsedSessionView => view !== null);
  }

  async function revoke(sessionId: string): Promise<void> {
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
