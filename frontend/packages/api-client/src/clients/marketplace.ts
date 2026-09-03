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
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-13 마켓플레이스 — marketplace 라우터는 봉투 미적용, 기존 경로 유지.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketData.ts와 동일 관용).
// task-1160: 쿼리·:listingId/:strategyId/:version 치환이 있는 조회 3건은 (경로 조립
// 자체는 requestByRoute가 지원하지 않아) resolvePath로 경로를 만들고 resolveEnvelope(route)
// 로 request/requestEnvelope 분기만 apiPaths.ts 레지스트리 단일 출처로 이관했다
// (admin.ts task-1159 선례와 동일 관용) — 분기 결과 자체는 바꾸지 않는다.
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
      const path = this.withQuery(resolvePath("marketplace.listings.base"), {
        asset_class: params.assetClass,
        exchange: params.exchange,
        max_price: params.maxPrice,
        sort_by: params.sortBy,
        page: params.page,
        page_size: params.pageSize,
      });
      return resolveEnvelope("marketplace.listings.base") ? this.requestEnvelope(path) : this.request(path);
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
      return resolveEnvelope("marketplace.strategies.get") ? this.requestEnvelope(path) : this.request(path);
    }

    async createReview(listingId: number, body: ReviewCreateRequest): Promise<ReviewResponse> {
      const path = resolvePath("marketplace.listings.reviews").replace(":listingId", String(listingId));
      return this.post(path, body);
    }

    async listReviews(listingId: number): Promise<ReviewListResponse> {
      const path = resolvePath("marketplace.listings.reviews").replace(":listingId", String(listingId));
      return resolveEnvelope("marketplace.listings.reviews") ? this.requestEnvelope(path) : this.request(path);
    }

    async submitDispute(body: DisputeCreateRequest): Promise<DisputeResponse> {
      return this.post(resolvePath("marketplace.disputes.create"), body);
    }
  };
}
