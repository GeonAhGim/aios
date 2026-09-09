import type { ResolveReconciliationRequest } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// task-2337(FE-OPS-3): 대사(reconciliation) 상태 조회·해소(resolve) 훅.
// useMandateStatus(useMandates.ts)와 동일 관용(useQuery/useMutation + apiClient
// 싱글턴) — 새 데이터 페칭 패턴을 만들지 않는다.
const RECONCILIATION_STATES_QUERY_KEY = ["reconciliationStates"];

export function useReconciliationStates() {
  return useQuery({
    queryKey: RECONCILIATION_STATES_QUERY_KEY,
    queryFn: () => apiClient.listReconciliationStates(),
  });
}

export function useResolveReconciliation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ targetRef, body }: { targetRef: string; body: ResolveReconciliationRequest }) =>
      apiClient.resolveReconciliation(targetRef, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: RECONCILIATION_STATES_QUERY_KEY }),
  });
}
