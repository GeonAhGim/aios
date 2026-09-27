// spec docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1
// contracts/v1.py(ResearchItem/SourceMeta)·§2.1 domain/entity_link.py
// (EntityLinkResult/UnmappedReason) 1:1 대응 — RD-17(task-2718)이 앞서
// 정의한 뷰를 task-7775가 실제 라우터(src/api/routers/research_data.py의
// search_router, POST /v1/foundation/research-data/search·GET .../sources)
// 응답 스키마(src/api/schemas/research_data.py ResearchItemView/
// ResearchSearchResponseView)와 맞췄다.
export type ResearchItemKind = "filing" | "news" | "macro" | "alt";

export type RedistributionPolicy = "store_full" | "store_excerpt" | "link_only";

/** domain/entity_link.py `UnmappedReason`과 1:1 대응(RD-5). */
export type ResearchUnmappedReason = "no_deterministic_key" | "not_found";

export interface ResearchSearchInput {
  query: string;
  kinds: ResearchItemKind[];
  instrumentId?: string;
  /** RD-A1 point-in-time 필터. 비우면 서버가 "현재"로 취급한다(spec §2.4 query.py). */
  asOf?: string;
}

export interface ResearchItemView {
  itemId: string;
  sourceId: string;
  kind: ResearchItemKind;
  title: string;
  url: string;
  publishedAt: string;
  knownAt: string;
  /** RD-5 `EntityLinkResult.instrument_id` — 미매핑이면 null(추측 금지, RD-A4). */
  instrumentId: string | null;
  unmappedReason: ResearchUnmappedReason | null;
}

export interface ResearchSearchResponse {
  items: ResearchItemView[];
  total: number;
  truncated: boolean;
}

/** RD-18 소스 헬스 — "degraded"는 수집이 계속되지만 지연/오류율이 높은 상태,
 * "down"은 최근 수집 시도가 전부 실패한 소스 장애 상태(ingest_job.py 재시도
 * 소진 판정과 1:1은 아니고, 대시보드가 알림을 띄울지 결정하는 표시용 축이다). */
export type ResearchSourceHealth = "ok" | "degraded" | "down";

export interface ResearchSourceStatusView {
  sourceId: string;
  publisher: string;
  redistribution: RedistributionPolicy;
  licenseRef: string;
  rateLimit: number;
  coverage: string;
  /** 이 소스가 마지막으로 성공 적재한 시각(UTC ISO) — RD-8 라우터가 아직 없어
   * optional로 둔다(RD-17 ResearchSourceStatusView 사용처와 하위호환).
   * 없으면 CoverageFreshnessPanel이 "신선도 판정 불가"로 표시한다. */
  lastIngestedAt?: string | null;
  /** 없으면 "ok"로 취급하지 않고 판정 불가로 표시한다(RD-A4 추측 금지와 동일
   * 원칙 — 헬스를 모르는 소스를 "정상"으로 침묵 처리하지 않는다). */
  health?: ResearchSourceHealth;
}

/** describeResearchSearchIssues가 돌려주는 코드 — ResearchPage.tsx가 이 코드를
 * catalog.ko의 research.validation.* 키로 매핑해 보여준다(shared-types는
 * apps/web의 i18n 카탈로그에 의존할 수 없으므로 문자열 대신 코드를 반환한다,
 * screener.ts ScreenerValidationIssue와 동일 관용). */
export type ResearchValidationIssue = { code: "query_required" };

// 순수 검증(빈 검색어로 서버를 호출하지 않는다) — screener.ts
// describeScreenDefinitionIssues와 동일 관용. ResearchPage.tsx의 "검색"
// 버튼과 researchData.test.ts가 함께 참조한다.
export function describeResearchSearchIssues(
  input: ResearchSearchInput,
): ResearchValidationIssue[] {
  const issues: ResearchValidationIssue[] = [];
  if (input.query.trim() === "") {
    issues.push({ code: "query_required" });
  }
  return issues;
}
