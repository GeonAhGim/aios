// L4_platform_observability_tenancy_api_v1.0.md §3.3 error taxonomy 표: POLICY_*/RISK_*의
// 호출자 조치는 "details.reason_codes"다(POLICY_LIVE_BLOCKED·AUTHZ_ZONE_VIOLATION도 같은
// 계열의 거부 사유를 담을 수 있다). 지금까지 프론트는 이 배열을 전혀 읽지 않고 apiError.ts의
// 폼 전체 배너 메시지로만 뭉뚱그려 보여줬다 — 이 파일은 그 details.reason_codes를 파싱해
// 사람이 읽을 수 있는 문장 목록으로 바꾸는 순수 함수만 담당한다(표시는 DenialReasons 몫).

const REASON_CODE_PREFIXES = ["POLICY_", "RISK_", "AUTHZ_"];

interface ReasonCodeSource {
  error_code?: unknown;
  details?: unknown;
}

function isReasonCodeSource(err: unknown): err is ReasonCodeSource {
  return typeof err === "object" && err !== null;
}

function extractReasonCodesArray(details: unknown): string[] {
  if (typeof details !== "object" || details === null) return [];
  const reasonCodes = (details as { reason_codes?: unknown }).reason_codes;
  if (!Array.isArray(reasonCodes)) return [];
  return reasonCodes.filter((code): code is string => typeof code === "string" && code.length > 0);
}

/**
 * ApiError 봉투(§3.3)에서 거부 사유 코드 목록을 뽑는다. error_code가
 * `POLICY_`/`RISK_`/`AUTHZ_` 접두가 아니거나 details.reason_codes가 배열이 아니거나
 * 비어있으면 빈 배열을 반환한다 — throw하지 않으므로 호출자는 항상 안전하게 호출할 수 있다.
 */
export function extractReasonCodes(err: unknown): string[] {
  if (!isReasonCodeSource(err)) return [];

  const errorCode = err.error_code;
  if (typeof errorCode !== "string" || !REASON_CODE_PREFIXES.some((prefix) => errorCode.startsWith(prefix))) {
    return [];
  }

  return extractReasonCodesArray(err.details);
}

// 알려진 거부 사유 코드만 한국어 문장으로 바꾼다. 도메인별 접두 화이트리스트는
// error_codes.py가 강제하므로 여기 없는 코드가 얼마든지 서버에서 추가될 수 있다 —
// 107 §3.2 fallback 원칙에 따라 미지 코드는 절대 throw/빈 문자열 없이 코드 원문을
// 그대로 반환한다(사용자에게는 원문이라도 보여주는 편이 아무 안내가 없는 것보다 낫다).
const REASON_CODE_MESSAGES: Record<string, string> = {
  POLICY_LIVE_BLOCKED: "실거래 모드에서는 허용되지 않는 작업입니다.",
  AUTHZ_ZONE_VIOLATION: "허용되지 않은 영역에 대한 요청입니다.",
  RISK_MAX_DRAWDOWN_EXCEEDED: "최대 손실 한도를 초과하여 거부되었습니다.",
  RISK_MAX_POSITION_EXCEEDED: "허용된 최대 포지션 한도를 초과하여 거부되었습니다.",
  RISK_MAX_ORDER_SIZE_EXCEEDED: "허용된 최대 주문 규모를 초과하여 거부되었습니다.",
  POLICY_TRADING_WINDOW_CLOSED: "거래 가능 시간이 아니어서 거부되었습니다.",
};

export function describeReasonCode(code: string): string {
  return REASON_CODE_MESSAGES[code] ?? code;
}
