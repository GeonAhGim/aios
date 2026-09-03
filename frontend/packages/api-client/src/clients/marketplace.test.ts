import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
import { ApiClientBase } from "../http";
import { withMarketplace } from "./marketplace";

class MarketplaceTestClient extends withMarketplace(ApiClientBase) {}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): MarketplaceTestClient {
  return new MarketplaceTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

function idempotencyKeyHeader(init: RequestInit): string | null {
  return new Headers(init.headers).get("Idempotency-Key");
}

const listingResponseBody = {
  id: 1,
  strategy_id: "s1",
  strategy_version: "v1",
  seller_user_id: "u1",
  seller_type: "USER",
  price: "10.00",
  status: "DRAFT",
  created_at: "2026-09-03T00:00:00Z",
};

const purchaseResponseBody = {
  purchase_id: 9,
  status: "COMPLETED",
  risk_warning: false,
  risk_warning_reason: null,
  platform_commission_amount: "1.00",
  seller_payout_amount: "9.00",
};

// task-1106 decision: PLT-17(d92ad68)·PLT-18(28b666c)이 raw HTTPException을
// 도메인 예외로 이관했을 뿐, marketplace.py 모듈 docstring이 명시하듯
// ApiResponse 봉투화는 mount_v1 배선 전까지 보류(needs_decision, task-1009)다.
// apiPaths.ts 레지스트리가 이 사실과 어긋나면(누군가 envelope:true로 잘못
// 바꾸면) resolveEnvelope가 marketplace 클라이언트를 조용히 requestEnvelope로
// 돌려버려 실제로는 봉투가 없는 응답을 파싱 실패시킨다 — 그 회귀를 여기서 고정한다.
describe("marketplace apiPaths 레지스트리: 봉투 미적용 상태를 고정한다(PLT-17/18은 raw HTTPException 제거만 완료, ApiResponse 이관은 보류)", () => {
  it.each([
    "marketplace.listings.base",
    "marketplace.listings.submitVerification",
    "marketplace.listings.verify",
    "marketplace.listings.purchase",
    "marketplace.strategies.get",
    "marketplace.listings.reviews",
    "marketplace.disputes.create",
  ] as const)("%s: envelope=false", (routeName) => {
    expect(API_ROUTES[routeName].envelope).toBe(false);
  });
});

describe("withMarketplace", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("createListing: POST /marketplace/listings로 body를 snake_case로 보낸다", async () => {
    const fetchMock = stubFetch(listingResponseBody, 201);

    const result = await makeClient().createListing({ strategyId: "s1", strategyVersion: "v1", price: "10.00" });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/marketplace/listings");
    expect(JSON.parse(init.body as string)).toEqual({
      strategy_id: "s1",
      strategy_version: "v1",
      price: "10.00",
    });
    expect(result.sellerUserId).toBe("u1");
  });

  it("searchListings: 쿼리 파라미터를 snake_case로 붙이고 undefined는 생략한다", async () => {
    const fetchMock = stubFetch({ items: [], total: 0, page: 1, page_size: 20 });

    await makeClient().searchListings({ assetClass: "CRYPTO", sortBy: "SHARPE_RATIO", page: 2 });

    const { url } = requestOf(fetchMock);
    expect(url).toBe(
      "https://api.example.test/marketplace/listings?asset_class=CRYPTO&sort_by=SHARPE_RATIO&page=2",
    );
  });

  it("purchaseListing: postIdempotent로 Idempotency-Key 헤더를 싣고, PurchaseCreateRequest는 별도 alias 없이 그대로 보낸다", async () => {
    const fetchMock = stubFetch(purchaseResponseBody, 201);

    const result = await makeClient().purchaseListing(
      42,
      { riskWarningAcknowledged: true },
      "caller-supplied-key-0001",
    );

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/marketplace/listings/42/purchase");
    expect(idempotencyKeyHeader(init)).toBe("caller-supplied-key-0001");
    expect(JSON.parse(init.body as string)).toEqual({ risk_warning_acknowledged: true });
    expect(result.purchaseId).toBe(9);
  });

  it("verifyListing: :listingId를 치환하고 응답을 camelCase로 변환한다", async () => {
    const fetchMock = stubFetch({ listing_id: 7, status: "VERIFIED", rejection_reason: null });

    const result = await makeClient().verifyListing(7, { decision: "APPROVE" });

    const { url } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/marketplace/listings/7/verify");
    expect(result).toEqual({ listingId: 7, status: "VERIFIED", rejectionReason: null });
  });

  it("listReviews: 경로는 apiPaths.ts 레지스트리에만 정의되어 있다(하드코딩 금지)", async () => {
    stubFetch({ reviews: [], review_count: 0, average_rating: null });

    await makeClient().listReviews(3);

    expect(API_ROUTES["marketplace.listings.reviews"].legacyPath).toBe(
      "/marketplace/listings/:listingId/reviews",
    );
  });

  it("submitDispute: POST /marketplace/disputes로 body를 snake_case로 보낸다", async () => {
    const fetchMock = stubFetch({ dispute_id: 5, status: "OPEN", created_at: "2026-09-03T00:00:00Z" });

    await makeClient().submitDispute({ purchaseId: 42, reason: "환불 요청" });

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/marketplace/disputes");
    expect(JSON.parse(init.body as string)).toEqual({ purchase_id: 42, reason: "환불 요청" });
  });
});
