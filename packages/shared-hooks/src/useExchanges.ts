import type { CredentialRequest } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function useExchangeCredentials() {
  return useQuery({
    queryKey: ["exchangeCredentials"],
    queryFn: () => apiClient.listExchangeCredentials(),
  });
}

export function useRegisterExchangeCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CredentialRequest) => apiClient.registerExchangeCredential(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exchangeCredentials"] }),
  });
}

export function useRevokeExchangeCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (exchange: string) => apiClient.revokeExchangeCredential(exchange),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exchangeCredentials"] }),
  });
}

export function useExchangeBalance(exchange: string | null) {
  return useQuery({
    queryKey: ["exchangeBalance", exchange],
    queryFn: () => apiClient.getExchangeBalance(exchange as string),
    enabled: !!exchange,
  });
}
