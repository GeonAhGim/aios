// task-2696(UX-12): WhatIfPanel.tsx·RebalancePage.tsx가 쓰는 클라이언트.
// screener.ts(UX-8, ScreenerRouteNotImplementedError)와 동일 관용 — apiRoutes.ts의
// whatif.previewOrder·whatif.rebalancePlan이 유령 경로(implemented=false)라 실제
// fetch를 시도하기 전에 typed 오류로 단락한다. AiosApiClient 합성(client.ts)에는
// 얹지 않고(screener.ts·follow.ts와 동일 관용) 화면이 createWhatIfClient로 직접
// 만든다 — 라우터가 생기면(src/api/routers/whatif.py) apiRoutes.ts의 implemented만
// true로 바꾸면 이 단락 없이 그대로 배선된다.
import type {
  WhatIfImpactResponse,
  WhatIfOrderInput,
  WhatIfRebalancePlanResponse,
  WhatIfRebalanceTargetInput,
} from "@aios/shared-types";
import { isRouteImplemented, resolvePath, type ApiRouteName } from "../apiPaths";
import { ApiClientBase } from "../http";

type WhatIfRouteName = "whatif.previewOrder" | "whatif.rebalancePlan";

const NOT_IMPLEMENTED_MESSAGE: Record<WhatIfRouteName, string> = {
  "whatif.previewOrder": "가상 주문 영향 미리보기 API가 아직 제공되지 않습니다.",
  "whatif.rebalancePlan": "리밸런싱 계획 API가 아직 제공되지 않습니다.",
};

// sessions.ts(task-1325) SessionsRouteNotImplementedError와 동일 패턴 — 호출부
// (WhatIfPanel.tsx·RebalancePage.tsx)가 문자열 매칭 대신 instanceof/route 필드로
// "아직 없는 라우트"를 판별할 수 있게 한다.
export class WhatIfRouteNotImplementedError extends Error {
  readonly route: ApiRouteName;

  constructor(route: WhatIfRouteName) {
    super(NOT_IMPLEMENTED_MESSAGE[route]);
    this.name = "WhatIfRouteNotImplementedError";
    this.route = route;
  }
}

function assertImplemented(route: WhatIfRouteName): void {
  if (!isRouteImplemented(route)) {
    throw new WhatIfRouteNotImplementedError(route);
  }
}

class WhatIfApiClient extends ApiClientBase {
  async previewOrder(order: WhatIfOrderInput): Promise<WhatIfImpactResponse> {
    assertImplemented("whatif.previewOrder");
    return this.postEnvelope(resolvePath("whatif.previewOrder"), order);
  }

  async planRebalance(targets: WhatIfRebalanceTargetInput[]): Promise<WhatIfRebalancePlanResponse> {
    assertImplemented("whatif.rebalancePlan");
    return this.postEnvelope(resolvePath("whatif.rebalancePlan"), { targets });
  }
}

export interface WhatIfClient {
  previewOrder(order: WhatIfOrderInput): Promise<WhatIfImpactResponse>;
  planRebalance(targets: WhatIfRebalanceTargetInput[]): Promise<WhatIfRebalancePlanResponse>;
}

export function createWhatIfClient(baseUrl: string, getToken: () => string | null): WhatIfClient {
  const client = new WhatIfApiClient(baseUrl, getToken);
  return {
    previewOrder: (order) => client.previewOrder(order),
    planRebalance: (targets) => client.planRebalance(targets),
  };
}
