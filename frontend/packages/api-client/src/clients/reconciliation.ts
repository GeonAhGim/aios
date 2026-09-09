import type {
  ReconciliationStateListResponse,
  ReconciliationStateView,
  ResolveReconciliationRequest,
} from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// task-2337(FE-OPS-3): 대사(reconciliation) 상태 조회·해소(resolve) 클라이언트 —
// src/api/routers/foundation/reconciliation.py 원문 기준. 둘 다
// `-> ApiResponse[...]`+`ok(...)`로 응답하므로(envelope=true) requestByRoute/
// postEnvelope만 쓴다(mandates.ts/riskGate.ts와 동일 관용, 새 파서 없음). 대사
// 실행(POST /runs)은 이 클라이언트에 없다(apiRoutes.ts 등록 코멘트 참조).
export function withReconciliation<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async listReconciliationStates(): Promise<ReconciliationStateListResponse> {
      return this.requestByRoute("reconciliation.list");
    }

    async resolveReconciliation(
      targetRef: string,
      body: ResolveReconciliationRequest,
    ): Promise<ReconciliationStateView> {
      const path = resolvePath("reconciliation.resolve").replace(":targetRef", targetRef);
      return this.postEnvelope(path, body);
    }
  };
}
