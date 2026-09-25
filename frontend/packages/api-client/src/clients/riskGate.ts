import type {
  ActivateSafetyControlRequest,
  ApproveRuleBundleRequest,
  EvaluateRiskGateRequest,
  RecoveryDecisionView,
  RecoverySafetyControlRequest,
  RiskEvaluationView,
  RiskRuleBundle,
  SafetyControlListResponse,
  SafetyControlView,
} from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// task-2335(FE-OPS-1): 안전 통제(safety control) 조회·해제(release) + task-5808
// (FE-OPS-9): 개통(admin activate)·룰번들 승인/활성화·evaluate 트리거 4라우트 —
// src/api/routers/foundation/risk_gate.py 원문 기준.
export function withRiskGate<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    // GET/POST "/safety-controls"는 경로를 공유하지만(apiRouteTypes.ts 축약 관용)
    // 이 화면은 GET(list)만 쓴다 — POST(자가 activate)는 decision상 UI가 없다.
    async listSafetyControls(): Promise<SafetyControlListResponse> {
      return this.requestByRoute("riskGate.safetyControls.list");
    }

    async deactivateSafetyControl(controlId: string): Promise<SafetyControlView> {
      const path = resolvePath("riskGate.safetyControls.deactivate").replace(":controlId", controlId);
      return this.postEnvelope(path);
    }

    async evaluateRecovery(
      controlId: string,
      body: RecoverySafetyControlRequest,
    ): Promise<RecoveryDecisionView> {
      const path = resolvePath("riskGate.safetyControls.evaluateRecovery").replace(":controlId", controlId);
      return this.postEnvelope(path, body);
    }

    // POST /admin/safety-controls — operator 전용(get_current_admin), self-service
    // POST /safety-controls와 별개 경로로 분리돼 있다(risk_gate.py:212 docstring).
    async activateSafetyControl(body: ActivateSafetyControlRequest): Promise<SafetyControlView> {
      return this.postEnvelope(resolvePath("riskGate.safetyControls.activate"), body);
    }

    async evaluateRiskGate(body: EvaluateRiskGateRequest): Promise<RiskEvaluationView> {
      return this.postEnvelope(resolvePath("riskGate.evaluate"), body);
    }

    async approveRuleBundle(bundleId: string, body: ApproveRuleBundleRequest): Promise<RiskRuleBundle> {
      const path = resolvePath("riskGate.ruleBundles.approve").replace(":bundleId", bundleId);
      return this.postEnvelope(path, body);
    }

    async activateRuleBundle(bundleId: string): Promise<RiskRuleBundle> {
      const path = resolvePath("riskGate.ruleBundles.activate").replace(":bundleId", bundleId);
      return this.postEnvelope(path);
    }
  };
}
