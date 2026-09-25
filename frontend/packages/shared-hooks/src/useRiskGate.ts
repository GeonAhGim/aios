import type {
  ActivateSafetyControlRequest,
  ApproveRuleBundleRequest,
  EvaluateRiskGateRequest,
  RecoverySafetyControlRequest,
} from "@aios/shared-types";
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

// task-5810(FE-OPS-9): 개통(admin activate)·룰번들 승인/활성화·evaluate 트리거 훅.
export function useActivateSafetyControl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ActivateSafetyControlRequest) => apiClient.activateSafetyControl(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["safetyControls"] }),
  });
}

export function useEvaluateRiskGate() {
  return useMutation({
    mutationFn: (body: EvaluateRiskGateRequest) => apiClient.evaluateRiskGate(body),
  });
}

export function useApproveRuleBundle() {
  return useMutation({
    mutationFn: ({ bundleId, body }: { bundleId: string; body: ApproveRuleBundleRequest }) =>
      apiClient.approveRuleBundle(bundleId, body),
  });
}

export function useActivateRuleBundle() {
  return useMutation({
    mutationFn: (bundleId: string) => apiClient.activateRuleBundle(bundleId),
  });
}
