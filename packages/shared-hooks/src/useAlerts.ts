import type { AlertCreateRequest } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function useMyAlerts() {
  return useQuery({
    queryKey: ["myAlerts"],
    queryFn: () => apiClient.listMyAlerts(),
  });
}

export function useCreateAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AlertCreateRequest) => apiClient.createAlert(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["myAlerts"] }),
  });
}

export function useCancelAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (alertId: number) => apiClient.cancelAlert(alertId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["myAlerts"] }),
  });
}
