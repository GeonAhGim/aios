// task-2718(RD-17): ResearchPage.tsx(검색·소스 상태·종목 연결 표시)가 쓰는
// 클라이언트. task-7775로 apiRoutes.ts의 researchData.*가 implemented=true로
// 바뀌었다(src/api/routers/research_data.py의 search_router,
// POST /v1/foundation/research-data/search·GET .../sources) — 아래
// isRouteImplemented 분기는 라우트가 다시 내려가는 배포 순서 사고를 막는
// 방어선으로 유지한다(screener.ts와 동일 관용). AiosApiClient 합성
// (client.ts)에는 얹지 않고(positions.ts/marketData.ts/screener.ts와 동일
// 관용) 화면이 createResearchDataClient로 직접 만든다.
import type {
  ResearchSearchInput,
  ResearchSearchResponse,
  ResearchSourceStatusView,
} from "@aios/shared-types";
import { isRouteImplemented, resolveEnvelope, resolvePath, type ApiRouteName } from "../apiPaths";
import { ApiClientBase } from "../http";

const RESEARCH_SEARCH_ROUTE: ApiRouteName = "researchData.search";
const RESEARCH_SOURCES_ROUTE: ApiRouteName = "researchData.sources.list";

// sessions.ts(task-1325) SessionsRouteNotImplementedError와 동일 패턴 — 호출부
// (ResearchPage.tsx)가 문자열 매칭 대신 instanceof/route 필드로 "아직 없는
// 라우트"를 판별할 수 있게 한다.
export class ResearchDataRouteNotImplementedError extends Error {
  readonly route: ApiRouteName;

  constructor(route: ApiRouteName) {
    super("리서치 데이터 API가 아직 제공되지 않습니다.");
    this.name = "ResearchDataRouteNotImplementedError";
    this.route = route;
  }
}

class ResearchDataApiClient extends ApiClientBase {
  async search(input: ResearchSearchInput): Promise<ResearchSearchResponse> {
    if (!isRouteImplemented(RESEARCH_SEARCH_ROUTE)) {
      throw new ResearchDataRouteNotImplementedError(RESEARCH_SEARCH_ROUTE);
    }
    return this.postEnvelope(resolvePath(RESEARCH_SEARCH_ROUTE), input);
  }

  async listSources(): Promise<ResearchSourceStatusView[]> {
    if (!isRouteImplemented(RESEARCH_SOURCES_ROUTE)) {
      throw new ResearchDataRouteNotImplementedError(RESEARCH_SOURCES_ROUTE);
    }
    // apiPaths.clientsScan.test.ts(task-1160)의 봉투 분기 가드 — resolveEnvelope(route)
    // 삼항 경유만 인정한다(charting.ts fetchByRoute와 동일 관용).
    const path = resolvePath(RESEARCH_SOURCES_ROUTE);
    return resolveEnvelope(RESEARCH_SOURCES_ROUTE)
      ? this.requestEnvelope<ResearchSourceStatusView[]>(path)
      : this.request<ResearchSourceStatusView[]>(path);
  }
}

export interface ResearchDataClient {
  search(input: ResearchSearchInput): Promise<ResearchSearchResponse>;
  listSources(): Promise<ResearchSourceStatusView[]>;
}

export function createResearchDataClient(
  baseUrl: string,
  getToken: () => string | null,
): ResearchDataClient {
  const client = new ResearchDataApiClient(baseUrl, getToken);
  return {
    search: (input) => client.search(input),
    listSources: () => client.listSources(),
  };
}
