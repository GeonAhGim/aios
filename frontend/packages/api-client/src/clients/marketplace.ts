import type {
  DisputeCreateRequest,
  DisputeResponse,
  ListingCreateRequest,
  ListingResponse,
  ListingSearchResponse,
  PurchaseCreateRequest,
  PurchaseResponse,
  ReviewCreateRequest,
  ReviewListResponse,
  ReviewResponse,
  StrategyDefinition,
  VerificationDecisionRequest,
} from "@aios/shared-types";
import { keysToSnake } from "../caseConvert";
import type { AnyConstructor } from "../http";

// FD-13 마켓플레이스 — marketplace 라우터는 봉투 미적용, 기존 경로 유지.
export function withMarketplace<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async createListing(body: ListingCreateRequest): Promise<ListingResponse> {
      return this.post("/marketplace/listings", body);
    }

    async searchListings(params: {
      assetClass?: string;
      exchange?: string;
      maxPrice?: string;
      sortBy?: "RECOMMENDED" | "SHARPE_RATIO";
      page?: number;
      pageSize?: number;
    }): Promise<ListingSearchResponse> {
      return this.request(
        this.withQuery("/marketplace/listings", {
          asset_class: params.assetClass,
          exchange: params.exchange,
          max_price: params.maxPrice,
          sort_by: params.sortBy,
          page: params.page,
          page_size: params.pageSize,
        }),
      );
    }

    async submitForVerification(listingId: number): Promise<ListingResponse> {
      return this.post(`/marketplace/listings/${listingId}/submit-verification`);
    }

    async verifyListing(
      listingId: number,
      body: VerificationDecisionRequest,
    ): Promise<{ listingId: number; status: string; rejectionReason: string | null }> {
      return this.post(`/marketplace/listings/${listingId}/verify`, body);
    }

    async purchaseListing(
      listingId: number,
      body: PurchaseCreateRequest,
      idempotencyKey: string,
    ): Promise<PurchaseResponse> {
      return this.request(`/marketplace/listings/${listingId}/purchase`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(keysToSnake(body)),
      });
    }

    async getStrategyDefinition(strategyId: string, version: string): Promise<StrategyDefinition> {
      return this.request(`/marketplace/strategies/${strategyId}/${version}`);
    }

    async createReview(listingId: number, body: ReviewCreateRequest): Promise<ReviewResponse> {
      return this.post(`/marketplace/listings/${listingId}/reviews`, body);
    }

    async listReviews(listingId: number): Promise<ReviewListResponse> {
      return this.request(`/marketplace/listings/${listingId}/reviews`);
    }

    async submitDispute(body: DisputeCreateRequest): Promise<DisputeResponse> {
      return this.post("/marketplace/disputes", body);
    }
  };
}
