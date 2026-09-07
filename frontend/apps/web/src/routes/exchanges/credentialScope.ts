import { getApiErrorMessage, parseSecretRef } from "@aios/shared-types";

// §3.6: 백엔드가 SecretRef 문자열을 아직 안 내려줄 수 있어 이 필드는 선택이다
// (PLT-33 이전). 목록 응답 타입(CredentialResponse)을 건드리지 않기 위해 여기서만
// 확장한다 — 값이 없거나 파싱 실패하면 scope를 추측하지 않고 "알 수 없음"으로 둔다.
export interface CredentialWithSecretRef {
  secretRef?: string;
}

export const LIVE_BLOCKED_NOTICE = getApiErrorMessage("POLICY_LIVE_BLOCKED");

export function credentialScope(secretRef: string | undefined) {
  const parsed = secretRef ? parseSecretRef(secretRef) : null;
  if (!parsed) return { label: "알 수 없음", tone: "neutral" as const, isLive: false };
  return parsed.scope === "live"
    ? { label: "LIVE", tone: "danger" as const, isLive: true }
    : { label: "PAPER", tone: "success" as const, isLive: false };
}
