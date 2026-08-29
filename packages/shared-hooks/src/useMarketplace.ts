import type {
  DisputeCreateRequest,
  ListingCreateRequest,
  PurchaseCreateRequest,
  ReviewCreateRequest,
  VerificationDecisionRequest,
} from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";

export function useListingSearch(params: {
  assetClass?: string;
  exchange?: string;
  maxPrice?: string;
  page?: number;
  pageSize?: number;
}) {
  return useQuery({
    queryKey: ["listings", params],
    queryFn: () => apiClient.searchListings(params),
  });
}

export function useCreateListing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ListingCreateRequest) => apiClient.createListing(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["listings"] }),
  });
}

export function useSubmitForVerification() {
  return useMutation({
    mutationFn: (listingId: number) => apiClient.submitForVerification(listingId),
  });
}

export function useVerificationQueue() {
  return useQuery({
    queryKey: ["verificationQueue"],
    queryFn: () => apiClient.getVerificationQueue(),
  });
}

export function useVerifyListing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      listingId,
      body,
    }: {
      listingId: number;
      body: VerificationDecisionRequest;
    }) => apiClient.verifyListing(listingId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["verificationQueue"] }),
  });
}

export function usePurchaseListing() {
  return useMutation({
    mutationFn: ({
      listingId,
      body,
      idempotencyKey,
    }: {
      listingId: number;
      body: PurchaseCreateRequest;
      idempotencyKey: string;
    }) => apiClient.purchaseListing(listingId, body, idempotencyKey),
  });
}

export function useStrategyDefinition(strategyId: string | null, version: string | null) {
  return useQuery({
    queryKey: ["strategyDefinition", strategyId, version],
    queryFn: () => apiClient.getStrategyDefinition(strategyId as string, version as string),
    enabled: !!strategyId && !!version,
    retry: false,
  });
}

export function useListingReviews(listingId: number | null) {
  return useQuery({
    queryKey: ["reviews", listingId],
    queryFn: () => apiClient.listReviews(listingId as number),
    enabled: !!listingId,
  });
}

export function useCreateReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ listingId, body }: { listingId: number; body: ReviewCreateRequest }) =>
      apiClient.createReview(listingId, body),
    onSuccess: (_data, vars) =>
      qc.invalidateQueries({ queryKey: ["reviews", vars.listingId] }),
  });
}

export function useSubmitDispute() {
  return useMutation({
    mutationFn: (body: DisputeCreateRequest) => apiClient.submitDispute(body),
  });
}
