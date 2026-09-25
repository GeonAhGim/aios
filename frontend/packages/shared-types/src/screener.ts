// spec L4_product_experience_and_discovery_v1.0.md §2.2/§3 스크리너 계약
// (`ScreenDefinition{filters: tuple[Filter,...], universe, sort, columns}`,
// `Filter = IndicatorFilter|FundamentalFilter|ResearchFilter|BacktestStatFilter`)
// 1:1 대응 — 그 계약(src/foundation/screener/contracts/v1.py, UX-5)과 실행
// (application/run_screen.py, UX-6)·이를 감싸는 API 라우터(src/api/routers/
// screener.py)는 아직 없다(task-2692 UX-8은 프론트만 선행, apiRoutes.ts의
// screener.run implemented=false 참조). 라우터가 생기면 실제 응답 스키마와
// 대조해 고친다.
import type { Venue } from "./candleSeries";

export type ScreenerOperator = "gt" | "gte" | "lt" | "lte" | "eq" | "neq";

export type ScreenerFilterKind = "indicator" | "fundamental" | "research" | "backtest_stat";

export interface ScreenerFilterInput {
  kind: ScreenerFilterKind;
  field: string;
  operator: ScreenerOperator;
  value: string;
  /** kind==="research"일 때만 의미가 있다 — spec §3 "ResearchFilter는 as_of 없이
   * 실행 불가(누수 차단, RD-A1 상속)". */
  asOf?: string;
}

export interface ScreenerSortInput {
  field: string;
  direction: "asc" | "desc";
}

export interface ScreenDefinitionInput {
  universe: string;
  filters: ScreenerFilterInput[];
  sort: ScreenerSortInput | null;
  columns: string[];
}

export interface ScreenResultRowView {
  instrumentId: string;
  symbol: string;
  venue: Venue;
  values: Record<string, string>;
}

export interface ScreenRunResponse {
  rows: ScreenResultRowView[];
  total: number;
  /** spec §3 "결과 상한 1,000행" 도달 여부 — 서버가 계산해 주는 값을 그대로
   * 표시한다(UX-A5류 불변조건과 동일하게 프론트가 재계산하지 않는다). */
  truncated: boolean;
}

/** describeScreenDefinitionIssues가 돌려주는 코드 — ScreenerPage.tsx가 이 코드를
 * catalog.ko의 screener.validation.* 키로 매핑해 보여준다(shared-types는 apps/web의
 * i18n 카탈로그에 의존할 수 없으므로 문자열 대신 코드를 반환한다). */
export type ScreenerValidationIssue =
  | { code: "universe_required" }
  | { code: "filters_required" }
  | { code: "filter_incomplete"; index: number }
  | { code: "filter_research_as_of_required"; index: number };

// 순수 검증(§3 "ResearchFilter는 as_of 없이 실행 불가" 계약 위반을 서버 호출 전에
// 막는다) — 서버 query_plan.py(UX-5)가 아직 없어도 이 누수 차단만은 프론트에서
// 먼저 강제한다. ScreenerPage.tsx의 "실행" 버튼과 screener.test.ts가 함께 참조한다.
export function describeScreenDefinitionIssues(
  definition: ScreenDefinitionInput,
): ScreenerValidationIssue[] {
  const issues: ScreenerValidationIssue[] = [];
  if (definition.universe.trim() === "") {
    issues.push({ code: "universe_required" });
  }
  if (definition.filters.length === 0) {
    issues.push({ code: "filters_required" });
  }
  definition.filters.forEach((filter, index) => {
    if (filter.field.trim() === "" || filter.value.trim() === "") {
      issues.push({ code: "filter_incomplete", index });
      return;
    }
    if (filter.kind === "research" && (!filter.asOf || filter.asOf.trim() === "")) {
      issues.push({ code: "filter_research_as_of_required", index });
    }
  });
  return issues;
}
