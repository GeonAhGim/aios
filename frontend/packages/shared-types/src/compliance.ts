// src/foundation/mandates/contracts/v1.py ComplianceVerdict/RuleHit/ComplianceDecision
// 1:1 대응(CM-1/CM-13), src/api/routers/foundation/compliance.py GET
// /decisions/{decision_id}(task-2618, CM-17)가 이 형태로 응답한다. task-2668(CM-18)
// 판정 조회 화면이 소비한다 — 서버가 이미 내려주는 값을 그대로 보여줄 뿐 판정
// 로직을 재구현하지 않는다(CM-A5, mandates.ts와 동일 decision).

export type ComplianceVerdict = "ALLOW" | "WARN" | "DENY";

export interface RuleHit {
  ruleId: string;
  severity: ComplianceVerdict;
  message: string;
  evidence: Record<string, unknown>;
}

export interface ComplianceDecisionView {
  decisionId: string;
  verdict: ComplianceVerdict;
  ruleHits: RuleHit[];
  inputsHash: string;
  bundleVersion: string;
  evaluatedAt: string;
  schemaVersion: string;
}
