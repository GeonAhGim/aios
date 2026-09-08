import type { RecoveryDecisionView, RecoverySafetyControlRequest, SafetyControlListResponse, SafetyControlView } from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// task-2335(FE-OPS-1): 안전 통제(safety control) 조회·해제(release) 클라이언트 —
// src/api/routers/foundation/risk_gate.py 원문 기준. decision상 이 리프는 개통
// (activate)·룰번들 승인/활성화·evaluate 트리거를 다루지 않는다(apiRoutes.ts 등록
// 코멘트 참조) — 이 파일도 그 세 라우트의 메서드를 두지 않는다.
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
  };
}
