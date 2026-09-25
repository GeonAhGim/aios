// spec L4_product_experience_and_discovery_v1.0.md §2.4 `src/foundation/follow/
// contracts/v1.py`(FollowSubscription{follower_portfolio, source_listing,
// sizing_policy, max_notional, paper_only=True}) 1:1 대응 — 그 계약 파일과
// 이를 감싸는 API 라우터(src/api/routers/follow.py)는 아직 없다(task-2699 UX-15는
// 프론트만 선행, apiRoutes.ts의 follow.subscriptions.* implemented=false 참조).
// 라우터가 생기면 실제 응답 스키마와 대조해 고친다.

export type FollowSizingPolicy = "MIRROR_PERCENTAGE" | "FIXED_NOTIONAL";

export type FollowSubscriptionStatus = "ACTIVE" | "PAUSED" | "CANCELLED";

export interface FollowSubscriptionCreateRequest {
  sourceListingId: number;
  sizingPolicy: FollowSizingPolicy;
  /** Decimal 문자열(marketplace.ts ListingResponse.price와 동일 관용) — 로컬 반올림 금지. */
  maxNotional: string;
}

export interface FollowSubscriptionResponse {
  id: number;
  followerPortfolioId: string;
  sourceListingId: number;
  sizingPolicy: FollowSizingPolicy;
  maxNotional: string;
  /** 계약상 항상 true — LIVE 전환은 별도 ADR 없이는 불가(spec §3 "팔로우: paper_only=True 불변"). */
  paperOnly: true;
  status: FollowSubscriptionStatus;
  createdAt: string;
}

export interface FollowSubscriptionListResponse {
  items: FollowSubscriptionResponse[];
  total: number;
}

export interface FollowPerformancePoint {
  asOf: string;
  sourceReturnPct: string;
  followerReturnPct: string;
}

// 원본 리스팅과 팔로워 포트폴리오의 성과를 나란히 비교한다(spec UX-15 "성과 비교").
// trackingDifferencePct는 서버가 계산해 주는 값을 그대로 표시하고 프론트에서
// 재계산하지 않는다(A5류 불변조건과 동일하게, 화면이 추론·요약을 만들어내지 않는다).
export interface FollowPerformanceComparisonResponse {
  subscriptionId: number;
  sourceListingId: number;
  trackingDifferencePct: string;
  points: FollowPerformancePoint[];
}
