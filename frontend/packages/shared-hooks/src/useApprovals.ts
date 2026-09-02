import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

// FD-10.1 self-service — 본인 소유 승인요청만 조회/승인/거절.
export function useMyApprovalRequests() {
  return useQuery({
    queryKey: ["myApprovalRequests"],
    queryFn: () => apiClient.listMyApprovalRequests(),
  });
}

export function useApproveMyRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (requestId: number) => apiClient.approveMyRequest(requestId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["myApprovalRequests"] }),
  });
}

export function useRejectMyRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (requestId: number) => apiClient.rejectMyRequest(requestId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["myApprovalRequests"] }),
  });
}
