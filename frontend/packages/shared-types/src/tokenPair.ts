// L4_platform_observability_tenancy_api_v1.0.md §3.4 TokenPairResponse
// (/auth/login, /auth/refresh 응답 data) 1:1 대응. 서버 PLT-23/24는 아직
// 미구현이지만 §3.4 계약은 확정이므로 계약선행으로 클라이언트 파서·판정
// 순수함수만 둔다. 실제 저장소·갱신 배선은 api-client 쪽 후속 리프 소관.

export interface TokenPairResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  session_id: string;
}

export interface ParsedTokenPair {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  sessionId: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

// throw 금지 — 필드 누락·token_type!=='bearer'·expires_in<=0이면 null만 반환한다
// (호출부가 §3.4 계약 위반을 "파싱 실패"로 처리할 수 있게).
export function parseTokenPair(data: unknown): ParsedTokenPair | null {
  if (!isRecord(data)) return null;

  const { access_token, refresh_token, token_type, expires_in, session_id } = data;

  if (!isNonEmptyString(access_token)) return null;
  if (!isNonEmptyString(refresh_token)) return null;
  if (token_type !== "bearer") return null;
  if (typeof expires_in !== "number" || !Number.isFinite(expires_in) || expires_in <= 0) {
    return null;
  }
  if (!isNonEmptyString(session_id)) return null;

  return {
    accessToken: access_token,
    refreshToken: refresh_token,
    expiresIn: expires_in,
    sessionId: session_id,
  };
}

export interface ShouldPreRefreshOptions {
  skewSec?: number;
}

const DEFAULT_SKEW_SEC = 60;

// §3.4: access TTL이 60분 → 15분으로 짧아지므로 만료 skewSec(기본 60초) 전부터
// 선제 갱신 대상이다. 이미 만료된 경우도 true(회수 실패로 재시도 대상에서
// 빠지면 안 됨).
export function shouldPreRefresh(
  expiresAtMs: number,
  nowMs: number,
  { skewSec = DEFAULT_SKEW_SEC }: ShouldPreRefreshOptions = {},
): boolean {
  return expiresAtMs - nowMs <= skewSec * 1000;
}
