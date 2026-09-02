// src/api/schemas/marketplace.py, src/services/listing_search_service.py 1:1 대응.

export interface ListingCreateRequest {
  strategyId: string;
  strategyVersion: string;
  price?: string;
}

export interface ListingResponse {
  id: number;
  strategyId: string;
  strategyVersion: string;
  sellerUserId: string;
  sellerType: "USER" | "PLATFORM";
  price: string | null;
  status: string;
  createdAt: string;
}

export interface ListingSummary {
  id: number;
  strategyId: string;
  strategyVersion: string;
  sellerUserId: string;
  sellerType: "USER" | "PLATFORM";
  price: string | null;
  verifiedAt: string | null;
  sharpeRatio: string | null;
}

export interface PlatformListingCreateRequest {
  strategyId: string;
  strategyVersion: string;
  price?: string;
}

export interface ListingSearchResponse {
  items: ListingSummary[];
  total: number;
  page: number;
  pageSize: number;
}

export interface VerificationDecisionRequest {
  decision: "APPROVE" | "REJECT";
  rejectionReason?: string;
}

export interface PurchaseCreateRequest {
  riskWarningAcknowledged?: boolean;
}

export interface PurchaseResponse {
  purchaseId: number;
  status: string;
  riskWarning: boolean;
  riskWarningReason: string | null;
  platformCommissionAmount: string | null;
  sellerPayoutAmount: string | null;
}

export interface ReviewCreateRequest {
  rating: number;
  comment?: string;
}

export interface ReviewResponse {
  reviewId: number;
  listingId: number;
  rating: number;
  comment: string | null;
  createdAt: string;
}

export interface ReviewListResponse {
  reviews: ReviewResponse[];
  reviewCount: number;
  averageRating: number | null;
}

export interface DisputeCreateRequest {
  purchaseId: number;
  reason: string;
}

export interface DisputeResponse {
  disputeId: number;
  status: string;
  createdAt: string;
}

export interface StrategyDefinition {
  strategyId: string;
  version: string;
  ownerUserId: string;
  fsmDefinition: unknown;
}
