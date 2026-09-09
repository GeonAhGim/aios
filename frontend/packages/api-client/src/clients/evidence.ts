import type { AuditTimelinePage, ChainVerificationResult } from "@aios/shared-types";
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

export interface AuditTimelineParams {
  cursor?: string;
  limit?: number;
  aggregateType?: string;
  action?: string;
}

// task-2337(FE-OPS-3): 증빙(evidence) 타임라인 조회 + 감사 체인 검증(chain:verify)
// 클라이언트 — src/api/routers/foundation/evidence.py 원문 기준. 둘 다
// `-> ApiResponse[...]`+`ok(...)`로 응답한다(envelope=true). chain:verify는
// tenant_id를 생략하면 서버가 system 이벤트 체인만 검증한다(admin.py:41-48
// 원문) — 여기서는 값을 그대로 옮기고 판정(해시 체인 검증)은 재구현하지
// 않는다(decision: LC-3 hash_chain 재구현 금지).
export function withEvidence<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getAuditTimeline(params: AuditTimelineParams = {}): Promise<AuditTimelinePage> {
      const path = this.withQuery(resolvePath("evidence.timeline"), {
        cursor: params.cursor,
        limit: params.limit,
        aggregate_type: params.aggregateType,
        action: params.action,
      });
      return resolveEnvelope("evidence.timeline") ? this.requestEnvelope(path) : this.request(path);
    }

    async verifyAuditChain(tenantId?: string): Promise<ChainVerificationResult> {
      const path = this.withQuery(resolvePath("evidence.chainVerify"), { tenant_id: tenantId });
      return this.postEnvelope(path);
    }
  };
}
