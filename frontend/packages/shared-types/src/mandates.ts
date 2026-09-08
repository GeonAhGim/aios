// src/foundation/mandates/contracts/v1.py, src/api/schemas/foundation/mandates.py 1:1
// 대응. task-2336(FE-OPS-2): 위임장(mandate) status·drafts·amendments·activate·
// pause·resume·policy:evaluate 7라우트 전부를 다룬다(task-2335 SafetyControlsPage와
// 달리 이 리프는 범위를 자르지 않는다 — decision 참조).
//
// 규칙 필드(MandateRuleInput)는 서버 계약을 그대로 옮긴다 — 프론트에서 판정
// 로직을 재구현하지 않는다(decision: "판정은 서버 권위, CM-A5").

export type MandateRevisionState = "DRAFT" | "PROPOSED" | "ACTIVE" | "PAUSED" | "SUPERSEDED" | "CANCELLED";

export type MandateAutonomy = "OBSERVE" | "PAPER" | "LIMITED_LIVE";

export type PolicyOutcome =
  | "ALLOW"
  | "DENY"
  | "REQUIRE_APPROVAL"
  | "REQUIRE_REASSESSMENT"
  | "PAUSE_REQUIRED";

// CreateMandateDraft/ProposeAmendment 요청 body — 75번 §3 6개 규칙(mandates.py:63-70).
export interface MandateRuleInput {
  maxTotalExposurePct: number;
  maxSingleInstrumentPct: number;
  minCashBufferPct: number;
  maxDailyLossPct: number;
  allowedAutonomy: MandateAutonomy;
  forbiddenAssets: string[];
}

export interface MandateRevisionView {
  id: string;
  mandateId: string;
  revisionNo: number;
  state: MandateRevisionState;
  maxTotalExposurePct: number;
  maxSingleInstrumentPct: number;
  minCashBufferPct: number;
  maxDailyLossPct: number;
  allowedAutonomy: MandateAutonomy;
  forbiddenAssets: string[];
  revisionHash: string;
  coolingOffStartedAt: string | null;
  createdAt: string | null;
  activatedAt: string | null;
  schemaVersion: string;
}

// GET /status 응답 — mandates.py:35-38(MandateStatusResponse)에는 as_of가 없다.
export interface MandateStatusResponse {
  tenantId: string;
  activeRevision: MandateRevisionView | null;
  pendingRevision: MandateRevisionView | null;
}

// revisions/{revision_id}:activate 요청 body — password/totpCode가 있으면 서버가
// reauthenticate()를 거쳐 material change 게이트를 통과시킨다(mandates.py:26-32).
export interface ActivateRevisionRequest {
  password?: string;
  totpCode?: string;
}

export interface PolicyEvaluationSubject {
  commandType: string;
  instrumentExposurePct?: number;
  totalExposurePct?: number;
  cashBufferPct?: number;
  projectedDailyLossPct?: number;
  requestedAutonomy?: MandateAutonomy;
  asset?: string;
}

export interface PolicyDecisionView {
  id: string;
  tenantId: string;
  bundleId: string;
  commandType: string;
  outcome: PolicyOutcome;
  reasonCodes: string[];
  obligations: string[];
  evaluatedAt: string;
  expiresAt: string | null;
  schemaVersion: string;
}
