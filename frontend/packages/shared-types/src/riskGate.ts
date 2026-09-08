// src/foundation/risk_gate/contracts/v1.py, src/api/schemas/foundation/risk_gate.py 1:1
// 대응. task-2335(FE-OPS-1): 안전 통제(safety control) 조회·해제(release)만 다룬다 —
// 개통(activate)·룰번들 승인/활성화·evaluate 트리거는 이 리프 범위 밖(decision 참조).

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
