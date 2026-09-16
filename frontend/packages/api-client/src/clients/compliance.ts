import type { ComplianceDecisionView } from "@aios/shared-types";
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// task-2668(CM-18): GET /v1/foundation/compliance/decisions/{decision_id}
// (src/api/routers/foundation/compliance.py, task-2618) 단건 조회 클라이언트 —
// ":param" 치환이 있어 requestByRoute를 못 쓰므로 admin.ts의 getAdminDispute와
// 동일한 resolveEnvelope(route) ? requestEnvelope(path) : request(path) 삼항을
// 쓴다(apiPaths.clientsScan.test.ts task-1160이 이 형태만 인정한다).
export function withCompliance<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getComplianceDecision(decisionId: string): Promise<ComplianceDecisionView> {
      const path = resolvePath("compliance.decisions.get").replace(":decisionId", decisionId);
      return resolveEnvelope("compliance.decisions.get") ? this.requestEnvelope(path) : this.request(path);
    }
  };
}
