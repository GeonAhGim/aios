import type { TopupRequestBody } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function useWalletBalance() {
  return useQuery({
    queryKey: ["walletBalance"],
    queryFn: () => apiClient.getWalletBalance(),
  });
}

export function useRequestTopup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: TopupRequestBody; idempotencyKey: string }) =>
      apiClient.requestTopup(body, idempotencyKey),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["walletBalance"] }),
  });
}
