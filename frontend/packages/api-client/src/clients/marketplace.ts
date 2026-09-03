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
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-13 마켓플레이스 — marketplace 라우터는 봉투 미적용, 기존 경로 유지.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketData.ts와 동일 관용).
export function withMarketplace<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async createListing(body: ListingCreateRequest): Promise<ListingResponse> {
      return this.post(resolvePath("marketplace.listings.base"), body);
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
        this.withQuery(resolvePath("marketplace.listings.base"), {
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
      const path = resolvePath("marketplace.listings.submitVerification").replace(
        ":listingId",
        String(listingId),
      );
      return this.post(path);
    }

    async verifyListing(
      listingId: number,
      body: VerificationDecisionRequest,
    ): Promise<{ listingId: number; status: string; rejectionReason: string | null }> {
      const path = resolvePath("marketplace.listings.verify").replace(":listingId", String(listingId));
      return this.post(path, body);
    }

    async purchaseListing(
      listingId: number,
      body: PurchaseCreateRequest,
      idempotencyKey?: string,
    ): Promise<PurchaseResponse> {
      const path = resolvePath("marketplace.listings.purchase").replace(":listingId", String(listingId));
      return this.postIdempotent(path, body, idempotencyKey);
    }

    async getStrategyDefinition(strategyId: string, version: string): Promise<StrategyDefinition> {
      const path = resolvePath("marketplace.strategies.get")
        .replace(":strategyId", strategyId)
        .replace(":version", version);
      return this.request(path);
    }

    async createReview(listingId: number, body: ReviewCreateRequest): Promise<ReviewResponse> {
      const path = resolvePath("marketplace.listings.reviews").replace(":listingId", String(listingId));
      return this.post(path, body);
    }

    async listReviews(listingId: number): Promise<ReviewListResponse> {
      return this.request(resolvePath("marketplace.listings.reviews").replace(":listingId", String(listingId)));
    }

    async submitDispute(body: DisputeCreateRequest): Promise<DisputeResponse> {
      return this.post(resolvePath("marketplace.disputes.create"), body);
    }
  };
}
