// task-2692(UX-8): ScreenerPage.tsx(필터 빌더·결과 표·차트/백테스트 연결)가 쓰는
// 클라이언트. backtests.ts(BT-18, SweepRouteNotImplementedError)와 동일 관용 —
// apiRoutes.ts의 screener.run이 유령 경로(implemented=false)라 실제 fetch를
// 시도하기 전에 typed 오류로 단락한다. AiosApiClient 합성(client.ts)에는 얹지
// 않고(positions.ts/marketData.ts/backtests.ts와 동일 관용) 화면이
// createScreenerClient로 직접 만든다 — 라우터가 생기면(src/api/routers/screener.py)
// apiRoutes.ts의 implemented만 true로 바꾸면 이 단락 없이 그대로 배선된다.
import type { ScreenDefinitionInput, ScreenRunResponse } from "@aios/shared-types";
import { isRouteImplemented, resolvePath, type ApiRouteName } from "../apiPaths";
import { ApiClientBase } from "../http";

const SCREENER_RUN_ROUTE: ApiRouteName = "screener.run";

// sessions.ts(task-1325) SessionsRouteNotImplementedError와 동일 패턴 — 호출부
// (ScreenerPage.tsx)가 문자열 매칭 대신 instanceof/route 필드로 "아직 없는
// 라우트"를 판별할 수 있게 한다.
export class ScreenerRouteNotImplementedError extends Error {
  readonly route: ApiRouteName = SCREENER_RUN_ROUTE;

  constructor() {
    super("스크리너 실행 API가 아직 제공되지 않습니다.");
    this.name = "ScreenerRouteNotImplementedError";
  }
}

class ScreenerApiClient extends ApiClientBase {
  async runScreen(definition: ScreenDefinitionInput): Promise<ScreenRunResponse> {
    if (!isRouteImplemented(SCREENER_RUN_ROUTE)) {
      throw new ScreenerRouteNotImplementedError();
    }
    return this.postEnvelope(resolvePath(SCREENER_RUN_ROUTE), definition);
  }
}

export interface ScreenerClient {
  runScreen(definition: ScreenDefinitionInput): Promise<ScreenRunResponse>;
}

export function createScreenerClient(baseUrl: string, getToken: () => string | null): ScreenerClient {
  const client = new ScreenerApiClient(baseUrl, getToken);
  return {
    runScreen: (definition) => client.runScreen(definition),
  };
}
