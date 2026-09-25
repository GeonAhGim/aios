import {
  ApiError,
  createScreenerClient,
  ScreenerRouteNotImplementedError,
  type ScreenerClient,
} from "@aios/api-client";
import {
  describeScreenDefinitionIssues,
  routeApiError,
  type ScreenDefinitionInput,
  type ScreenerFilterInput,
  type ScreenerFilterKind,
  type ScreenerOperator,
  type ScreenerValidationIssue,
} from "@aios/shared-types";
import { useAuthStore } from "@aios/shared-hooks";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardTitle,
  EmptyState,
  Field,
  Input,
  LoadingState,
  PageHeader,
  Select,
} from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";

// spec L4_product_experience_and_discovery_v1.0.md UX-8 — ScreenerPage.tsx(필터
// 빌더·결과 표·차트/백테스트 연결). 선행 리프 UX-5/UX-6(src/foundation/screener/*)와
// 이를 감싸는 API 라우터(src/api/routers/screener.py)는 아직 없다(apiRoutes.ts의
// screener.run implemented=false 참조) — 이 화면은 FollowPage.tsx(UX-15)·
// AiStudioPage.tsx(AI-22)와 동일 관용으로, 라우터가 없어도 화면·상태 처리(로딩/오류/
// 빈 목록/유령 경로 단락/검증)는 완성해 두고 screenerClient prop 주입으로 테스트한다.
// 라우터가 생기면 apiRoutes.ts의 implemented만 true로 바꾸면 그대로 배선된다.
// "차트/백테스트 연결"은 결과 행마다 ChartPage(BT-13 BacktestPanel이 이미 얹혀 있는
// /chart 라우트)로 이동하는 링크로 만족한다 — 이 화면이 백테스트를 다시 구현하지
// 않는다.
export interface ScreenerPageProps {
  screenerClient?: ScreenerClient;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function defaultScreenerClient(): ScreenerClient {
  const getToken = () => useAuthStore.getState().token;
  return createScreenerClient(baseUrl, getToken);
}

const FILTER_KINDS: ScreenerFilterKind[] = ["indicator", "fundamental", "research", "backtest_stat"];
const OPERATORS: ScreenerOperator[] = ["gt", "gte", "lt", "lte", "eq", "neq"];
const OPERATOR_SYMBOL: Record<ScreenerOperator, string> = {
  gt: ">",
  gte: ">=",
  lt: "<",
  lte: "<=",
  eq: "=",
  neq: "!=",
};

interface FilterRow extends ScreenerFilterInput {
  id: string;
}

let filterRowSeq = 0;
function nextFilterRowId(): string {
  filterRowSeq += 1;
  return `filter-${filterRowSeq}`;
}

function emptyFilterRow(): FilterRow {
  return { id: nextFilterRowId(), kind: "indicator", field: "", operator: "gt", value: "" };
}

// t()는 CustomTypeOptions(i18next.d.ts)로 catalog.ko의 리터럴 키만 받는다 — 동적
// 템플릿 키(`screener.filterBuilder.kind.${kind}`) 대신 switch로 리터럴 키를
// 고정해 컴파일 타임 검증을 유지한다.
function filterKindLabel(t: TFunction<"translation", undefined>, kind: ScreenerFilterKind): string {
  switch (kind) {
    case "indicator":
      return t("screener.filterBuilder.kind.indicator");
    case "fundamental":
      return t("screener.filterBuilder.kind.fundamental");
    case "research":
      return t("screener.filterBuilder.kind.research");
    case "backtest_stat":
      return t("screener.filterBuilder.kind.backtest_stat");
  }
}

// SweepResultsPage.tsx(BT-18)의 SweepErrorBanner와 동일 관용 — 유령 경로 오류
// (ScreenerRouteNotImplementedError)도 별도 렌더 분기를 만들지 않고 같은
// ErrorMessage로 흘려보내되, 재시도 버튼만 강제로 끈다(라우터가 없다는 사실은
// 재시도로 안 바뀐다).
function ScreenerErrorBanner({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const notImplemented = error instanceof ScreenerRouteNotImplementedError;
  const routed = routeApiError(error);
  const canRetry = !notImplemented && (routed.kind === "refetch_retry" || routed.kind === "backoff_retry");
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={canRetry ? onRetry : undefined}
    />
  );
}

function issueMessage(t: TFunction<"translation", undefined>, issue: ScreenerValidationIssue): string {
  switch (issue.code) {
    case "universe_required":
      return t("screener.validation.universeRequired");
    case "filters_required":
      return t("screener.validation.filtersRequired");
    case "filter_incomplete":
      return t("screener.validation.filterIncomplete", { position: issue.index + 1 });
    case "filter_research_as_of_required":
      return t("screener.validation.filterResearchAsOfRequired", { position: issue.index + 1 });
  }
}

export function ScreenerPage({ screenerClient }: ScreenerPageProps) {
  const { t } = useTranslation();
  const client = useMemo(() => screenerClient ?? defaultScreenerClient(), [screenerClient]);

  const [universe, setUniverse] = useState("");
  const [filters, setFilters] = useState<FilterRow[]>([emptyFilterRow()]);
  const [sortField, setSortField] = useState("");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [validationIssues, setValidationIssues] = useState<ScreenerValidationIssue[]>([]);
  const [submitted, setSubmitted] = useState<ScreenDefinitionInput | null>(null);

  const query = useQuery({
    queryKey: ["screener-run", submitted],
    queryFn: () => client.runScreen(submitted as ScreenDefinitionInput),
    enabled: submitted !== null,
  });

  function updateFilter(id: string, patch: Partial<ScreenerFilterInput>) {
    setFilters((rows) => rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }

  function addFilter() {
    setFilters((rows) => [...rows, emptyFilterRow()]);
  }

  function removeFilter(id: string) {
    setFilters((rows) => rows.filter((row) => row.id !== id));
  }

  function buildDefinition(): ScreenDefinitionInput {
    const columns = Array.from(new Set(["symbol", ...filters.map((f) => f.field).filter((f) => f !== "")]));
    return {
      universe,
      filters: filters.map(({ id: _id, ...rest }) => rest),
      sort: sortField.trim() === "" ? null : { field: sortField, direction: sortDirection },
      columns,
    };
  }

  function handleRun() {
    const definition = buildDefinition();
    const issues = describeScreenDefinitionIssues(definition);
    setValidationIssues(issues);
    if (issues.length > 0) return;
    setSubmitted(definition);
  }

  const rows = query.data?.rows ?? [];

  return (
    <AppShell>
      <div className="max-w-4xl space-y-6">
        <PageHeader title={t("screener.pageTitle")} />

        <Card>
          <CardTitle>{t("screener.filterBuilder.title")}</CardTitle>
          <div className="space-y-4">
            <Field label={t("screener.filterBuilder.universeLabel")}>
              <Input
                data-testid="screener-universe"
                value={universe}
                placeholder={t("screener.filterBuilder.universePlaceholder")}
                onChange={(e) => setUniverse(e.target.value)}
              />
            </Field>

            <div className="space-y-3">
              {filters.map((row, index) => (
                <div
                  key={row.id}
                  data-testid={`screener-filter-row-${index}`}
                  className="grid grid-cols-2 gap-3 rounded-md border border-border p-3 sm:grid-cols-5"
                >
                  <Field label={t("screener.filterBuilder.kindLabel")}>
                    <Select
                      data-testid={`screener-filter-${index}-kind`}
                      value={row.kind}
                      onChange={(e) => updateFilter(row.id, { kind: e.target.value as ScreenerFilterKind })}
                    >
                      {FILTER_KINDS.map((kind) => (
                        <option key={kind} value={kind}>
                          {filterKindLabel(t, kind)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label={t("screener.filterBuilder.fieldLabel")}>
                    <Input
                      data-testid={`screener-filter-${index}-field`}
                      value={row.field}
                      onChange={(e) => updateFilter(row.id, { field: e.target.value })}
                    />
                  </Field>
                  <Field label={t("screener.filterBuilder.operatorLabel")}>
                    <Select
                      data-testid={`screener-filter-${index}-operator`}
                      value={row.operator}
                      onChange={(e) => updateFilter(row.id, { operator: e.target.value as ScreenerOperator })}
                    >
                      {OPERATORS.map((op) => (
                        <option key={op} value={op}>
                          {OPERATOR_SYMBOL[op]}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label={t("screener.filterBuilder.valueLabel")}>
                    <Input
                      data-testid={`screener-filter-${index}-value`}
                      value={row.value}
                      onChange={(e) => updateFilter(row.id, { value: e.target.value })}
                    />
                  </Field>
                  {row.kind === "research" ? (
                    <Field label={t("screener.filterBuilder.asOfLabel")}>
                      <Input
                        data-testid={`screener-filter-${index}-asof`}
                        value={row.asOf ?? ""}
                        onChange={(e) => updateFilter(row.id, { asOf: e.target.value })}
                      />
                    </Field>
                  ) : (
                    <div />
                  )}
                  <div className="col-span-2 sm:col-span-5">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      data-testid={`screener-filter-${index}-remove`}
                      onClick={() => removeFilter(row.id)}
                    >
                      {t("screener.filterBuilder.removeFilter")}
                    </Button>
                  </div>
                </div>
              ))}
            </div>

            <Button type="button" variant="secondary" size="sm" data-testid="screener-add-filter" onClick={addFilter}>
              {t("screener.filterBuilder.addFilter")}
            </Button>

            <div className="grid grid-cols-2 gap-3">
              <Field label={t("screener.filterBuilder.sortFieldLabel")}>
                <Input
                  data-testid="screener-sort-field"
                  value={sortField}
                  onChange={(e) => setSortField(e.target.value)}
                />
              </Field>
              <Field label={t("screener.filterBuilder.sortDirection.asc")}>
                <Select value={sortDirection} onChange={(e) => setSortDirection(e.target.value as "asc" | "desc")}>
                  <option value="asc">{t("screener.filterBuilder.sortDirection.asc")}</option>
                  <option value="desc">{t("screener.filterBuilder.sortDirection.desc")}</option>
                </Select>
              </Field>
            </div>

            {validationIssues.length > 0 && (
              <div data-testid="screener-validation-alert">
                <Alert tone="danger">
                  <ul className="list-disc space-y-1 pl-4">
                    {validationIssues.map((issue, i) => (
                      <li key={i}>{issueMessage(t, issue)}</li>
                    ))}
                  </ul>
                </Alert>
              </div>
            )}

            <Button type="button" data-testid="screener-run" onClick={handleRun}>
              {t("screener.filterBuilder.run")}
            </Button>
          </div>
        </Card>

        <Card>
          <CardTitle>{t("screener.results.title")}</CardTitle>

          {submitted === null && <EmptyState>{t("screener.results.beforeRun")}</EmptyState>}
          {submitted !== null && query.isLoading && <LoadingState />}
          {submitted !== null && query.isError && (
            <ScreenerErrorBanner error={query.error} onRetry={() => query.refetch()} />
          )}
          {submitted !== null && !query.isError && !query.isLoading && rows.length === 0 && (
            <EmptyState>{t("screener.results.empty")}</EmptyState>
          )}
          {rows.length > 0 && (
            <div className="space-y-3">
              <p className="text-sm text-fg-muted">
                {t("screener.results.totalLabel", { total: query.data?.total ?? rows.length })}
              </p>
              {query.data?.truncated && <Alert tone="warning">{t("screener.results.truncated")}</Alert>}
              <div className="divide-y divide-border">
                {rows.map((row) => (
                  <div
                    key={row.instrumentId}
                    data-testid={`screener-row-${row.instrumentId}`}
                    className="flex items-center justify-between gap-4 py-2"
                  >
                    <div>
                      <p className="text-sm font-medium text-fg">{row.symbol}</p>
                      <Badge tone="neutral">{row.venue}</Badge>
                    </div>
                    <Link to={`/chart?instrument_id=${encodeURIComponent(row.instrumentId)}`} className="text-sm underline">
                      {t("screener.results.viewInChart")}
                    </Link>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
