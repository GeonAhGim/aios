import {
  ApiError,
  createResearchDataClient,
  ResearchDataRouteNotImplementedError,
  type ResearchDataClient,
} from "@aios/api-client";
import {
  describeResearchSearchIssues,
  routeApiError,
  type RedistributionPolicy,
  type ResearchItemKind,
  type ResearchItemView,
  type ResearchSearchInput,
  type ResearchValidationIssue,
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
} from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";

// spec docs/specs/L4_research_data_and_market_ecosystem_v1.0.md RD-17 —
// ResearchPage.tsx(검색·소스 상태·종목 연결 표시). 선행 리프 RD-7(application/
// query.py, as_of PIT 필터)·RD-8(entitlement 연동 + src/api/routers/
// research_data.py)은 아직 없다(apiRoutes.ts의 researchData.* implemented=false
// 참조) — 이 화면은 ScreenerPage.tsx(UX-8)·FollowPage.tsx(UX-15)와 동일 관용으로,
// 라우터가 없어도 화면·상태 처리(로딩/오류/빈 목록/유령 경로 단락/검증)는 완성해
// 두고 researchDataClient prop 주입으로 테스트한다. 라우터가 생기면
// apiRoutes.ts의 implemented만 true로 바꾸면 그대로 배선된다.
export interface ResearchPageProps {
  researchDataClient?: ResearchDataClient;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function defaultResearchDataClient(): ResearchDataClient {
  const getToken = () => useAuthStore.getState().token;
  return createResearchDataClient(baseUrl, getToken);
}

const KINDS: ResearchItemKind[] = ["filing", "news", "macro", "alt"];

// t()는 CustomTypeOptions(i18next.d.ts)로 catalog.ko의 리터럴 키만 받는다 —
// 동적 템플릿 키 대신 switch로 리터럴 키를 고정한다(ScreenerPage.tsx
// filterKindLabel과 동일 관용).
function kindLabel(t: TFunction<"translation", undefined>, kind: ResearchItemKind): string {
  switch (kind) {
    case "filing":
      return t("research.kind.filing");
    case "news":
      return t("research.kind.news");
    case "macro":
      return t("research.kind.macro");
    case "alt":
      return t("research.kind.alt");
  }
}

function redistributionLabel(
  t: TFunction<"translation", undefined>,
  policy: RedistributionPolicy,
): string {
  switch (policy) {
    case "store_full":
      return t("research.sources.redistribution.storeFull");
    case "store_excerpt":
      return t("research.sources.redistribution.storeExcerpt");
    case "link_only":
      return t("research.sources.redistribution.linkOnly");
  }
}

function issueMessage(t: TFunction<"translation", undefined>, issue: ResearchValidationIssue): string {
  switch (issue.code) {
    case "query_required":
      return t("research.validation.queryRequired");
  }
}

// ScreenerErrorBanner(UX-8)와 동일 관용 — 유령 경로 오류
// (ResearchDataRouteNotImplementedError)도 별도 렌더 분기를 만들지 않고 같은
// ErrorMessage로 흘려보내되, 재시도 버튼만 강제로 끈다(라우터가 없다는 사실은
// 재시도로 안 바뀐다).
function ResearchErrorBanner({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const notImplemented = error instanceof ResearchDataRouteNotImplementedError;
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

// RD-5 종목 연결 표시 — 매핑됐으면 /chart로 이동하는 링크, 미매핑이면 사유
// 배지(추측 금지, RD-A4). ScreenerPage.tsx의 "차트/백테스트에서 보기" 링크와
// 동일 관용이다.
function InstrumentLinkCell({ item, t }: { item: ResearchItemView; t: TFunction<"translation", undefined> }) {
  if (item.instrumentId) {
    return (
      <Link
        to={`/chart?instrument_id=${encodeURIComponent(item.instrumentId)}`}
        className="whitespace-nowrap text-sm underline"
      >
        {t("research.results.viewInChart")}
      </Link>
    );
  }
  const key =
    item.unmappedReason === "not_found"
      ? "research.results.unmappedNotFound"
      : "research.results.unmappedNoKey";
  return (
    <Badge tone="warning" data-testid={`research-item-${item.itemId}-unmapped`}>
      {t(key)}
    </Badge>
  );
}

export function ResearchPage({ researchDataClient }: ResearchPageProps) {
  const { t } = useTranslation();
  const client = useMemo(() => researchDataClient ?? defaultResearchDataClient(), [researchDataClient]);

  const [queryText, setQueryText] = useState("");
  const [selectedKinds, setSelectedKinds] = useState<ResearchItemKind[]>([]);
  const [instrumentId, setInstrumentId] = useState("");
  const [asOf, setAsOf] = useState("");
  const [validationIssues, setValidationIssues] = useState<ResearchValidationIssue[]>([]);
  const [submitted, setSubmitted] = useState<ResearchSearchInput | null>(null);

  const searchQuery = useQuery({
    queryKey: ["research-search", submitted],
    queryFn: () => client.search(submitted as ResearchSearchInput),
    enabled: submitted !== null,
  });

  const sourcesQuery = useQuery({
    queryKey: ["research-sources"],
    queryFn: () => client.listSources(),
  });

  function toggleKind(kind: ResearchItemKind) {
    setSelectedKinds((kinds) => (kinds.includes(kind) ? kinds.filter((k) => k !== kind) : [...kinds, kind]));
  }

  function buildInput(): ResearchSearchInput {
    return {
      query: queryText,
      kinds: selectedKinds,
      instrumentId: instrumentId.trim() === "" ? undefined : instrumentId.trim(),
      asOf: asOf.trim() === "" ? undefined : asOf.trim(),
    };
  }

  function handleSearch() {
    const input = buildInput();
    const issues = describeResearchSearchIssues(input);
    setValidationIssues(issues);
    if (issues.length > 0) return;
    setSubmitted(input);
  }

  const items = searchQuery.data?.items ?? [];
  const sources = sourcesQuery.data ?? [];

  return (
    <AppShell>
      <div className="max-w-4xl space-y-6">
        <PageHeader title={t("research.pageTitle")} />

        <Card>
          <CardTitle>{t("research.searchForm.title")}</CardTitle>
          <div className="space-y-4">
            <Field label={t("research.searchForm.queryLabel")}>
              <Input
                data-testid="research-query"
                value={queryText}
                onChange={(e) => setQueryText(e.target.value)}
              />
            </Field>

            <div className="flex flex-wrap gap-4" data-testid="research-kind-checkboxes">
              {KINDS.map((kind) => (
                <label key={kind} className="flex items-center gap-2 text-sm text-fg-muted">
                  <input
                    type="checkbox"
                    id={`research-kind-checkbox-${kind}`}
                    className="accent-accent"
                    data-testid={`research-kind-${kind}`}
                    checked={selectedKinds.includes(kind)}
                    onChange={() => toggleKind(kind)}
                  />
                  {kindLabel(t, kind)}
                </label>
              ))}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Field label={t("research.searchForm.instrumentLabel")}>
                <Input
                  data-testid="research-instrument"
                  value={instrumentId}
                  onChange={(e) => setInstrumentId(e.target.value)}
                />
              </Field>
              <Field label={t("research.searchForm.asOfLabel")}>
                <Input data-testid="research-asof" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
              </Field>
            </div>

            {validationIssues.length > 0 && (
              <div data-testid="research-validation-alert">
                <Alert tone="danger">
                  <ul className="list-disc space-y-1 pl-4">
                    {validationIssues.map((issue, i) => (
                      <li key={i}>{issueMessage(t, issue)}</li>
                    ))}
                  </ul>
                </Alert>
              </div>
            )}

            <Button type="button" data-testid="research-search-run" onClick={handleSearch}>
              {t("research.searchForm.run")}
            </Button>
          </div>
        </Card>

        <Card>
          <CardTitle>{t("research.results.title")}</CardTitle>

          {submitted === null && <EmptyState>{t("research.results.beforeRun")}</EmptyState>}
          {submitted !== null && searchQuery.isLoading && <LoadingState />}
          {submitted !== null && searchQuery.isError && (
            <ResearchErrorBanner error={searchQuery.error} onRetry={() => searchQuery.refetch()} />
          )}
          {submitted !== null && !searchQuery.isError && !searchQuery.isLoading && items.length === 0 && (
            <EmptyState>{t("research.results.empty")}</EmptyState>
          )}
          {items.length > 0 && (
            <div className="space-y-3">
              <p className="text-sm text-fg-muted">
                {t("research.results.totalLabel", { total: searchQuery.data?.total ?? items.length })}
              </p>
              {searchQuery.data?.truncated && <Alert tone="warning">{t("research.results.truncated")}</Alert>}
              <div className="divide-y divide-border">
                {items.map((item) => (
                  <div
                    key={item.itemId}
                    data-testid={`research-item-${item.itemId}`}
                    className="flex items-center justify-between gap-4 py-2"
                  >
                    <div>
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sm font-medium text-fg underline"
                      >
                        {item.title}
                      </a>
                      <div className="mt-1 flex gap-2">
                        <Badge tone="neutral">{kindLabel(t, item.kind)}</Badge>
                        <Badge tone="neutral">{item.sourceId}</Badge>
                      </div>
                    </div>
                    <InstrumentLinkCell item={item} t={t} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>

        <Card>
          <CardTitle>{t("research.sources.title")}</CardTitle>

          {sourcesQuery.isLoading && <LoadingState />}
          {sourcesQuery.isError && (
            <ResearchErrorBanner error={sourcesQuery.error} onRetry={() => sourcesQuery.refetch()} />
          )}
          {!sourcesQuery.isLoading && !sourcesQuery.isError && sources.length === 0 && (
            <EmptyState>{t("research.sources.empty")}</EmptyState>
          )}
          {sources.length > 0 && (
            <div className="divide-y divide-border">
              {sources.map((source) => (
                <div
                  key={source.sourceId}
                  data-testid={`research-source-${source.sourceId}`}
                  className="flex items-center justify-between gap-4 py-2"
                >
                  <div>
                    <p className="text-sm font-medium text-fg">{source.publisher}</p>
                    <p className="text-xs text-fg-muted">{source.coverage}</p>
                  </div>
                  <Badge tone={source.redistribution === "link_only" ? "warning" : "neutral"}>
                    {redistributionLabel(t, source.redistribution)}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
