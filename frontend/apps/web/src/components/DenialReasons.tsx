import { describeReasonCode, extractReasonCodes } from "@aios/shared-types";

// ApiError(§3.3 ApiError 봉투) POLICY_*/RISK_*(및 AUTHZ_ZONE_VIOLATION 등)의
// details.reason_codes를 사용자용 거부 사유 목록으로 보여주는 표시 전용 컴포넌트.
// 매핑 로직 자체는 shared-types/reasonCodes.ts에 있으므로 여기서는 UI 배치만 담당한다.
//
// reason_codes가 없으면(구형/타 에러코드) null을 반환한다 — 이 경우 기존 ErrorMessage
// 배너가 계속 전체 메시지를 담당한다.
interface DenialReasonsProps {
  error?: unknown;
}

export function DenialReasons({ error }: DenialReasonsProps) {
  const reasonCodes = extractReasonCodes(error);
  if (reasonCodes.length === 0) return null;

  return (
    <ul className="mt-1 list-disc pl-5 text-sm text-fg-muted">
      {reasonCodes.map((code) => (
        <li key={code}>{describeReasonCode(code)}</li>
      ))}
    </ul>
  );
}
