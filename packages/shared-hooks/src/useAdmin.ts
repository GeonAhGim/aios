import type {
  DisputeResolveRequest,
  PlatformListingCreateRequest,
  SuspendSellerRequest,
} from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function useAdminDisputes(disputeStatus?: string) {
  return useQuery({
    queryKey: ["adminDisputes", disputeStatus],
    queryFn: () => apiClient.listAdminDisputes(disputeStatus),
  });
}

export function useAdminDispute(disputeId: number | null) {
  return useQuery({
    queryKey: ["adminDispute", disputeId],
    queryFn: () => apiClient.getAdminDispute(disputeId as number),
    enabled: !!disputeId,
  });
}

export function useResolveDispute() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      disputeId,
      body,
    }: {
      disputeId: number;
      body: DisputeResolveRequest;
    }) => apiClient.resolveDispute(disputeId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adminDisputes"] }),
  });
}

export function useAdminUsers(emailSearch?: string) {
  return useQuery({
    queryKey: ["adminUsers", emailSearch],
    queryFn: () => apiClient.listAdminUsers(emailSearch),
  });
}

export function useChangeUserStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, status }: { userId: string; status: string }) =>
      apiClient.changeUserStatus(userId, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adminUsers"] }),
  });
}

export function useSuspendSeller() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: SuspendSellerRequest }) =>
      apiClient.suspendSeller(userId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adminUsers"] }),
  });
}

export function usePendingTopups(page = 1, pageSize = 20) {
  return useQuery({
    queryKey: ["pendingTopups", page, pageSize],
    queryFn: () => apiClient.listPendingTopups(page, pageSize),
  });
}

export function useConfirmTopup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      topupId,
      idempotencyKey,
    }: {
      topupId: number;
      idempotencyKey: string;
    }) => apiClient.confirmTopup(topupId, idempotencyKey),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pendingTopups"] }),
  });
}

export function useCreatePlatformListing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PlatformListingCreateRequest) => apiClient.createPlatformListing(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["listings"] }),
  });
}

export function useApproveRequest() {
  return useMutation({
    mutationFn: (requestId: number) => apiClient.approveRequest(requestId),
  });
}

export function useRejectRequest() {
  return useMutation({
    mutationFn: (requestId: number) => apiClient.rejectRequest(requestId),
  });
}
