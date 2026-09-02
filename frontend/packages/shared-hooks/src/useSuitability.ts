import type { SuitabilityAnswers } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";
import { useAuthStore } from "./useAuthStore";

export function useRiskProfile() {
  const token = useAuthStore((s) => s.token);
  return useQuery({
    queryKey: ["riskProfile"],
    queryFn: () => apiClient.getRiskProfile(),
    enabled: !!token,
  });
}

export function useSubmitRiskAssessment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SuitabilityAnswers) => apiClient.submitRiskAssessment(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["riskProfile"] }),
  });
}
