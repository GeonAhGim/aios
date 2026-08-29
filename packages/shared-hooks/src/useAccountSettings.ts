import type {
  ApprovalSettingsRequest,
  DeletionRequest,
  WhitelistEntryRequest,
} from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function useApprovalSettings() {
  return useQuery({
    queryKey: ["approvalSettings"],
    queryFn: () => apiClient.getApprovalSettings(),
  });
}

export function useUpdateApprovalSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ApprovalSettingsRequest) => apiClient.updateApprovalSettings(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approvalSettings"] }),
  });
}

export function useWhitelistEntries() {
  return useQuery({
    queryKey: ["whitelistEntries"],
    queryFn: () => apiClient.listWhitelistEntries(),
  });
}

export function useRegisterWhitelistEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: WhitelistEntryRequest) => apiClient.registerWhitelistEntry(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["whitelistEntries"] }),
  });
}

export function useRequestAccountDeletion() {
  return useMutation({
    mutationFn: (body: DeletionRequest) => apiClient.requestAccountDeletion(body),
  });
}

export function useNotificationHistory(eventType?: string) {
  return useQuery({
    queryKey: ["notificationHistory", eventType],
    queryFn: () => apiClient.getNotificationHistory(eventType),
  });
}

export function useNotificationPreferences() {
  return useQuery({
    queryKey: ["notificationPreferences"],
    queryFn: () => apiClient.getNotificationPreferences(),
  });
}

export function useUpdateNotificationPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (changes: Record<string, boolean>) =>
      apiClient.updateNotificationPreferences(changes),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notificationPreferences"] }),
  });
}
