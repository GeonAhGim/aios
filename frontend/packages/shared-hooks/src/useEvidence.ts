import { useMutation, useQuery } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// task-2337(FE-OPS-3): 증빙(evidence) 타임라인 조회 + 감사 체인 검증(chain:verify)
// 훅. chain:verify는 mandate/reconciliation과 달리 조회 결과를 무효화할 다른
// 캐시가 없다(순수 판정 커맨드) — useEvaluateMandatePolicy와 동일 이유로
// onSuccess 캐시 무효화를 두지 않는다.
export interface AuditTimelineQueryParams {
  cursor?: string;
  limit?: number;
  aggregateType?: string;
  action?: string;
}

export function useAuditTimeline(params: AuditTimelineQueryParams = {}) {
  return useQuery({
    queryKey: ["auditTimeline", params],
    queryFn: () => apiClient.getAuditTimeline(params),
  });
}

export function useVerifyAuditChain() {
  return useMutation({
    mutationFn: (tenantId?: string) => apiClient.verifyAuditChain(tenantId),
  });
}
