// src/foundation/risk_gate/contracts/v1.py, src/api/schemas/foundation/risk_gate.py 1:1
// 대응. task-2335(FE-OPS-1): 안전 통제(safety control) 조회·해제(release). task-5808
// (FE-OPS-9)이 개통(admin activate)·룰번들 승인/활성화·evaluate 트리거 타입을 추가했다.

export type SafetyScope = "GLOBAL" | "TENANT" | "ACCOUNT" | "STRATEGY_DEPLOYMENT" | "PROVIDER";

export type SafetyControlState = "ACTIVE" | "INACTIVE";

export type GateKind = "DEPLOYMENT" | "PRE_INTENT" | "PRE_TRADE" | "PRE_SUBMIT" | "INTRADAY" | "RECOVERY";

export type RiskOutcome = "ALLOW" | "DENY" | "REDUCE" | "PAUSE" | "ESCALATE";

export interface SafetyControlView {
  id: string;
  scope: SafetyScope;
  scopeRef: string;
  state: SafetyControlState;
  reason: string;
  fenceToken: number;
  createdAt: string | null;
  deactivatedAt: string | null;
  idempotencyDigest: string | null;
  schemaVersion: string;
}

export interface SafetyControlListResponse {
  controls: SafetyControlView[];
  asOf: string;
}

// R-53 RECOVERY 게이트 요청(§9). evidenceRef가 없으면 서버가 RSK-007로 거부한다 —
// 여기서는 필드만 옮긴다.
export interface RecoverySafetyControlRequest {
  evidenceRef?: string;
  approvalId: number;
}

export interface RecoveryDecisionView {
  id: string;
  gateKind: GateKind;
  outcome: RiskOutcome;
  reasonCodes: string[];
  evaluatedAt: string;
  expiresAt: string;
  traceId: string;
}

// POST /admin/safety-controls 요청(src/api/schemas/foundation/risk_gate.py
// ActivateSafetyControlRequest). self-service POST /safety-controls과 body가
// 같지만, admin 라우트는 operator 권한(get_current_admin)이 있어야 라우팅된다.
export interface ActivateSafetyControlRequest {
  scope: SafetyScope;
  scopeRef?: string;
  reason: string;
}

// POST /evaluate 요청(EvaluateRiskGateRequest). connectionId를 지정하면 해당
// connection의 freshness를 검사에 포함한다(78번 §1).
export interface EvaluateRiskGateRequest {
  gateKind: GateKind;
  connectionId?: string;
}

export interface RiskEvaluationView {
  id: string;
  gateKind: GateKind;
  outcome: RiskOutcome;
  reasonCodes: string[];
  obligations: string[];
  ruleVersion: string;
  evaluatedAt: string;
  expiresAt: string | null;
  traceId: string | null;
  schemaVersion: string;
}

export interface ApproveRuleBundleRequest {
  approvalRef: string;
}

export type BundleState = "DRAFT" | "APPROVED" | "ACTIVE" | "RETIRED";

// src/core/risk/policy_bundle.py RiskRuleBundle 1:1 대응(R-23 DRAFT->APPROVED->ACTIVE).
export interface RiskRuleBundle {
  id: string;
  scope: string;
  version: string;
  ruleHash: string;
  engineVersion: string;
  policySnapshot: Record<string, unknown>;
  state: BundleState;
  effectiveFrom: string | null;
  effectiveTo: string | null;
  createdBy: string;
  approvedBy: string | null;
  approvalRef: string | null;
  approvedAt: string | null;
  activatedAt: string | null;
  retiredAt: string | null;
}
