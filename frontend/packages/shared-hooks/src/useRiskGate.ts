import type { RecoverySafetyControlRequest } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// task-2335(FE-OPS-1): 안전 통제(safety control) 조회·해제 훅. useAdmin.ts와 동일
// 관용(useQuery/useMutation + apiClient 싱글턴) — 새 데이터 페칭 패턴을 만들지 않는다.
export function useSafetyControls() {
  return useQuery({
    queryKey: ["safetyControls"],
    queryFn: () => apiClient.listSafetyControls(),
  });
}

export function useDeactivateSafetyControl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (controlId: string) => apiClient.deactivateSafetyControl(controlId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["safetyControls"] }),
  });
}

export function useEvaluateRecovery() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      controlId,
      body,
    }: {
      controlId: string;
      body: RecoverySafetyControlRequest;
    }) => apiClient.evaluateRecovery(controlId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["safetyControls"] }),
  });
}
