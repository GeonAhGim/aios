// L4_platform_observability_tenancy_api_v1.0.md §3.4 auth_session 조회 뷰
// (SessionView) 1:1 대응. 서버는 auth_session 테이블을 그대로 노출하지 않고
// (원문 저장 금지 108 §2.1) session_id/created_at/last_seen_at/user_agent/ip/
// revoked_at 6개 필드만 세션 목록 API 전용 뷰로 내려준다는 전제 하에 이 6개만
// 화이트리스트로 받는다.
//
// task-606 decision: 이 모듈은 tokenPair.ts(아직 http.ts 미배선이라 서버 원문
// snake_case를 그대로 받음)와 달리, 처음부터 api-client의 http.ts(ApiClientBase)
// 경유로 고정됐다. ApiClientBase는 모든 응답 body를 keysToCamel로 변환한 뒤
// 넘기므로, 여기서는 그 결과물(카멜케이스)을 파싱 대상으로 삼는다.

export interface ParsedSessionView {
  sessionId: string;
  createdAt: string;
  lastSeenAt: string;
  userAgent: string | null;
  ip: string | null;
  revokedAt: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isNullableString(value: unknown): value is string | null | undefined {
  return value === null || value === undefined || typeof value === "string";
}

// throw 금지 — sessionId/createdAt/lastSeenAt 중 하나라도 없거나 빈 문자열이면
// null만 반환한다. userAgent/ip/revokedAt은 없거나 null이면 null로 취급하고,
// 화이트리스트에 없는 나머지 필드는 조용히 무시한다.
export function parseSessionView(raw: unknown): ParsedSessionView | null {
  if (!isRecord(raw)) return null;

  const { sessionId, createdAt, lastSeenAt, userAgent, ip, revokedAt } = raw;

  if (!isNonEmptyString(sessionId)) return null;
  if (!isNonEmptyString(createdAt)) return null;
  if (!isNonEmptyString(lastSeenAt)) return null;
  if (!isNullableString(userAgent)) return null;
  if (!isNullableString(ip)) return null;
  if (!isNullableString(revokedAt)) return null;

  return {
    sessionId,
    createdAt,
    lastSeenAt,
    userAgent: userAgent ?? null,
    ip: ip ?? null,
    revokedAt: revokedAt ?? null,
  };
}

// revokedAt이 있으면(이미 폐기된 세션) 다시 폐기할 수 없다 — 목록 UI가 revoke
// 액션을 노출할지 판단하는 순수 함수. api-client의 revoke(sessionId)는 이
// 판정을 강제하지 않으므로(서버가 404로 흡수) 호출부가 명시적으로 써야 한다.
export function canRevoke(view: ParsedSessionView): boolean {
  return view.revokedAt === null;
}

// currentSessionId가 null이거나(로그인 직후 미확보 등) 일치하지 않으면 false다.
export function isCurrentSession(view: ParsedSessionView, currentSessionId: string | null): boolean {
  return currentSessionId !== null && view.sessionId === currentSessionId;
}
