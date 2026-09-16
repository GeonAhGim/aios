// spec docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1
// contracts/v1.py(ResearchItem/SourceMeta)·§2.1 domain/entity_link.py
// (EntityLinkResult/UnmappedReason) 1:1 대응 — RD-17(task-2718)은 그 계약을
// 감싸는 API 라우터(src/api/routers/research_data.py, RD-8)·조회 응용
// (application/query.py, RD-7)보다 앞서가는 선행 프론트다(screener.ts와 동일
// 관용, apiRoutes.ts의 researchData.* implemented=false 참조). 라우터가
// 생기면 실제 응답 스키마와 대조해 고친다.
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

export interface ResearchSourceStatusView {
  sourceId: string;
  publisher: string;
  redistribution: RedistributionPolicy;
  licenseRef: string;
  rateLimit: number;
  coverage: string;
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
