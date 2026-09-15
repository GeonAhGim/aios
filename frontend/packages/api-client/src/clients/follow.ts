// task-2699(UX-15): FollowPage.tsx(팔로우 관리·성과 비교)가 쓰는 클라이언트.
// backtests.ts(BT-18, SweepRouteNotImplementedError)와 동일 관용 — apiRoutes.ts의
// follow.subscriptions.*가 유령 경로(implemented=false)라 실제 fetch를 시도하기
// 전에 typed 오류로 단락한다. AiosApiClient 합성(client.ts)에는 얹지 않고
// (positions.ts/marketData.ts/backtests.ts와 동일 관용) 화면이 createFollowClient로
// 직접 만든다 — 라우터가 생기면(src/api/routers/follow.py) apiRoutes.ts의
// implemented만 true로 바꾸면 이 단락 없이 그대로 배선된다. GET/DELETE는
// marketplace.ts(searchListings)와 동일한 resolveEnvelope(route) 삼항 관용으로
// 봉투 분기를 레지스트리에 위임한다(apiPaths.clientsScan.test.ts task-1160 가드가
// this.requestEnvelope/this.request 직접 호출을 하드코딩으로 잡는다 — postEnvelope는
// 그 가드 대상이 아니라 POST는 backtests.ts처럼 곧바로 쓴다).
import type {
  FollowPerformanceComparisonResponse,
  FollowSubscriptionCreateRequest,
  FollowSubscriptionListResponse,
  FollowSubscriptionResponse,
} from "@aios/shared-types";
import { isRouteImplemented, resolveEnvelope, resolvePath, type ApiRouteName } from "../apiPaths";
import { ApiClientBase } from "../http";

type FollowRouteName =
  | "follow.subscriptions.base"
  | "follow.subscriptions.cancel"
  | "follow.subscriptions.performance";

const NOT_IMPLEMENTED_MESSAGE: Record<FollowRouteName, string> = {
  "follow.subscriptions.base": "팔로우 구독 API가 아직 제공되지 않습니다.",
  "follow.subscriptions.cancel": "팔로우 해지 API가 아직 제공되지 않습니다.",
  "follow.subscriptions.performance": "팔로우 성과 비교 API가 아직 제공되지 않습니다.",
};

// sessions.ts(task-1325)의 SessionsRouteNotImplementedError와 동일 패턴 — 호출부
// (FollowPage.tsx)가 문자열 매칭 대신 instanceof/route 필드로 "아직 없는 라우트"를
// 판별할 수 있게 한다.
export class FollowRouteNotImplementedError extends Error {
  readonly route: ApiRouteName;

  constructor(route: FollowRouteName) {
    super(NOT_IMPLEMENTED_MESSAGE[route]);
    this.name = "FollowRouteNotImplementedError";
    this.route = route;
  }
}

function assertImplemented(route: FollowRouteName): void {
  if (!isRouteImplemented(route)) {
    throw new FollowRouteNotImplementedError(route);
  }
}

class FollowApiClient extends ApiClientBase {
  async listSubscriptions(): Promise<FollowSubscriptionListResponse> {
    assertImplemented("follow.subscriptions.base");
    const path = resolvePath("follow.subscriptions.base");
    return resolveEnvelope("follow.subscriptions.base") ? this.requestEnvelope(path) : this.request(path);
  }

  async createSubscription(body: FollowSubscriptionCreateRequest): Promise<FollowSubscriptionResponse> {
    assertImplemented("follow.subscriptions.base");
    return this.postEnvelope(resolvePath("follow.subscriptions.base"), body);
  }

  async cancelSubscription(subscriptionId: number): Promise<void> {
    assertImplemented("follow.subscriptions.cancel");
    const path = resolvePath("follow.subscriptions.cancel").replace(":subscriptionId", String(subscriptionId));
    await (resolveEnvelope("follow.subscriptions.cancel")
      ? this.requestEnvelope<void>(path, { method: "DELETE" })
      : this.request<void>(path, { method: "DELETE" }));
  }

  async getPerformanceComparison(subscriptionId: number): Promise<FollowPerformanceComparisonResponse> {
    assertImplemented("follow.subscriptions.performance");
    const path = resolvePath("follow.subscriptions.performance").replace(
      ":subscriptionId",
      String(subscriptionId),
    );
    return resolveEnvelope("follow.subscriptions.performance") ? this.requestEnvelope(path) : this.request(path);
  }
}

export interface FollowClient {
  listSubscriptions(): Promise<FollowSubscriptionListResponse>;
  createSubscription(body: FollowSubscriptionCreateRequest): Promise<FollowSubscriptionResponse>;
  cancelSubscription(subscriptionId: number): Promise<void>;
  getPerformanceComparison(subscriptionId: number): Promise<FollowPerformanceComparisonResponse>;
}

export function createFollowClient(baseUrl: string, getToken: () => string | null): FollowClient {
  const client = new FollowApiClient(baseUrl, getToken);
  return {
    listSubscriptions: () => client.listSubscriptions(),
    createSubscription: (body) => client.createSubscription(body),
    cancelSubscription: (subscriptionId) => client.cancelSubscription(subscriptionId),
    getPerformanceComparison: (subscriptionId) => client.getPerformanceComparison(subscriptionId),
  };
}
