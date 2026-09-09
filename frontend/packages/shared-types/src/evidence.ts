// src/foundation/evidence/contracts/v1.py 1:1 대응. task-2337(FE-OPS-3): 증빙
// 타임라인 조회 + 감사 체인 검증(chain:verify)만 다룬다 — 쓰기 엔드포인트는
// 서버에도 없다(evidence.py 원문: "감사 이벤트는 다른 bounded context가 자기
// 커맨드의 부수효과로 내부에서 기록하는 것"). 해시 체인 재계산은 프론트에서
// 하지 않는다(decision: LC-3 hash_chain 재구현 금지, 서버 판정을 표시만 한다).

export type AuditOutcome = "SUCCESS" | "DENIED" | "ERROR";

export type AuditClassification = "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED" | "SECRET_REFERENCE";

export interface AuditEventView {
  id: string;
  tenantId: string | null;
  sequenceNo: number;
  aggregateType: string;
  aggregateId: string;
  aggregateRevision: number | null;
  action: string;
  outcome: AuditOutcome;
  actorSubjectId: string | null;
  traceId: string;
  payloadHash: string;
  payload: Record<string, unknown>;
  classification: AuditClassification;
  previousHash: string | null;
  eventHash: string;
  occurredAt: string;
  schemaVersion: string;
}

export interface AuditTimelinePage {
  items: AuditEventView[];
  nextCursor: string | null;
  asOf: string;
}

// evidence.py:44 -> ApiResponse[dict[str, bool]], 항상 {"verified": true}만
// 성공 응답으로 온다(실패는 409 ChainIntegrityError로 던져진다) — 그래도 서버
// 응답 모양 그대로를 타입으로 옮긴다(§C decision).
export interface ChainVerificationResult {
  verified: boolean;
}
