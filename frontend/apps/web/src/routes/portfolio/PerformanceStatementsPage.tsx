import { ApiError } from "@aios/api-client";
import { apiClient } from "@aios/shared-hooks";
import type {
  ComponentBreakdown,
  ComputeStatementRequest,
  CorrectStatementRequest,
  GetPerformanceStatementParams,
  ListPerformanceStatementsParams,
  MoneyValue,
  PerformanceStatementListResponse,
  PerformanceStatementView,
  StatementScope,
} from "@aios/shared-types";
import { classifyServerError, isResourceNotFound, routeApiError } from "@aios/shared-types";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardTitle,
  EmptyState,
  Input,
  LoadingState,
  PageHeader,
  Select,
  Textarea,
} from "@aios/ui-web";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { NotFoundState } from "../../components/NotFoundState";

// task-5805(FE-OPS-6): src/api/routers/foundation/performance.py의 compute·목록·
// 상세·correct 4라우트(apiPaths.ts "performanceStatements.*", task-5803/5804에서
// 이미 클라이언트·타입 배선 완료)를 다루는 첫 화면. 새 파서·새 클라이언트 메서드는
// 만들지 않고 apiClient(performance.ts mixin)만 그대로 호출한다.
export type ComputeStatementFn = (body: ComputeStatementRequest) => Promise<PerformanceStatementView>;
export type ListStatementsFn = (
  params: ListPerformanceStatementsParams,
) => Promise<PerformanceStatementListResponse>;
export type GetStatementFn = (
  statementId: string,
  params: GetPerformanceStatementParams,
) => Promise<PerformanceStatementView>;
export type CorrectStatementFn = (
  statementId: string,
  body: CorrectStatementRequest,
) => Promise<PerformanceStatementView>;

const computeStatementDefault: ComputeStatementFn = (body) => apiClient.computePerformanceStatement(body);
const listStatementsDefault: ListStatementsFn = (params) => apiClient.listPerformanceStatements(params);
const getStatementDefault: GetStatementFn = (statementId, params) =>
  apiClient.getPerformanceStatement(statementId, params);
const correctStatementDefault: CorrectStatementFn = (statementId, body) =>
  apiClient.correctPerformanceStatement(statementId, body);

// amount:null은 대사 미완료(PENDING)를 뜻한다(shared-types/performance.ts 주석) —
// 0으로 대체하지 않고 그대로 "—"만 보여준다("never assume zero").
function MoneyCell({ value }: { value: MoneyValue }) {
  const { t } = useTranslation();
  return (
    <span className="tabular">
      {value.amount ?? "—"} {value.currency}
      {value.state === "ESTIMATED" && (
        <Badge tone="warning" className="ml-1">
          {t("performanceStatementsPage.estimated")}
        </Badge>
      )}
    </span>
  );
}

const COMPONENT_ROWS = [
  { key: "grossPnl", labelKey: "performanceStatementsPage.grossPnl" },
  { key: "fees", labelKey: "performanceStatementsPage.fees" },
  { key: "slippage", labelKey: "performanceStatementsPage.slippage" },
  { key: "funding", labelKey: "performanceStatementsPage.funding" },
  { key: "fx", labelKey: "performanceStatementsPage.fx" },
  { key: "cashflowsNet", labelKey: "performanceStatementsPage.cashflowsNet" },
  { key: "estimatedTax", labelKey: "performanceStatementsPage.estimatedTax" },
  { key: "netPnl", labelKey: "performanceStatementsPage.netPnl" },
] as const satisfies ReadonlyArray<{ key: keyof ComponentBreakdown; labelKey: string }>;

function stateTone(state: PerformanceStatementView["state"]): "neutral" | "success" | "warning" {
  if (state === "FINAL") return "success";
  if (state === "CORRECTED") return "warning";
  return "neutral";
}

function StatementDetail({
  statement,
  correctStatement,
  onCorrected,
}: {
  statement: PerformanceStatementView;
  correctStatement: CorrectStatementFn;
  onCorrected: () => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const correct = useMutation({
    mutationFn: () => correctStatement(statement.id, { reason }),
    onSuccess: () => {
      setReason("");
      onCorrected();
    },
  });

  return (
    <Card data-testid="performance-statement-detail">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <CardTitle>
            {statement.scope} · {statement.periodStart} ~ {statement.periodEnd}
          </CardTitle>
          <p className="text-xs text-fg-muted">
            {t("performanceStatementsPage.asOf", { asOf: statement.asOf })} · rev {statement.revisionNo}
          </p>
        </div>
        <Badge tone={stateTone(statement.state)}>{statement.state}</Badge>
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-fg-muted">
            <tr>
              <th className="pb-2 font-normal">{t("performanceStatementsPage.component")}</th>
              <th className="pb-2 font-normal">{t("performanceStatementsPage.amount")}</th>
            </tr>
          </thead>
          <tbody className="text-fg">
            {COMPONENT_ROWS.map(({ key, labelKey }) => (
              <tr key={key} className="border-t border-border">
                <td className="py-2">{t(labelKey)}</td>
                <td className="py-2">
                  <MoneyCell value={statement.components[key]} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {statement.returns.length > 0 && (
        <div className="mt-3 space-y-1 text-sm">
          <p className="font-medium text-fg">{t("performanceStatementsPage.returns")}</p>
          {statement.returns.map((r, i) => (
            <p key={i} className="text-fg-muted">
              {r.method}/{r.basis}: {r.valuePct ?? "—"}%
              {r.annualized ? ` (${t("performanceStatementsPage.annualized")})` : ""}
            </p>
          ))}
        </div>
      )}

      {!statement.identityOk && (
        <div className="mt-3">
          <Alert tone="warning">
            {t("performanceStatementsPage.identityMismatch", { residual: statement.identityResidual ?? "—" })}
          </Alert>
        </div>
      )}

      {statement.limitations.length > 0 && (
        <p className="mt-3 text-xs text-fg-muted">{statement.limitations.join(", ")}</p>
      )}

      <div className="mt-4 space-y-2 border-t border-border pt-3" data-testid="performance-statement-correct">
        <Textarea
          aria-label={t("performanceStatementsPage.correctReasonLabel")}
          placeholder={t("performanceStatementsPage.correctReasonPlaceholder")}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
        />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          loading={correct.isPending}
          disabled={!reason.trim()}
          onClick={() => correct.mutate()}
        >
          {t("performanceStatementsPage.correctSubmit")}
        </Button>
        {correct.isError && (
          <ErrorMessage
            errorCode={correct.error instanceof ApiError ? correct.error.errorCode : undefined}
            message={correct.error instanceof Error ? correct.error.message : undefined}
            traceId={correct.error instanceof ApiError ? correct.error.traceId : undefined}
          />
        )}
        {correct.isSuccess && <Alert tone="success">{t("performanceStatementsPage.correctSuccess")}</Alert>}
      </div>
    </Card>
  );
}

function ComputeForm({ computeStatement, onComputed }: { computeStatement: ComputeStatementFn; onComputed: (id: string) => void }) {
  const { t } = useTranslation();
  const [scope, setScope] = useState<StatementScope>("PAPER");
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [methodologyVersion, setMethodologyVersion] = useState("");

  const compute = useMutation({
    mutationFn: () =>
      computeStatement({
        scope,
        periodStart,
        periodEnd,
        methodologyVersion: methodologyVersion.trim() || undefined,
      }),
    onSuccess: (statement) => onComputed(statement.id),
  });

  return (
    <Card data-testid="performance-statement-compute">
      <CardTitle>{t("performanceStatementsPage.computeTitle")}</CardTitle>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div>
          <label htmlFor="perf-compute-scope" className="block text-sm font-medium text-fg">
            {t("performanceStatementsPage.scope")}
          </label>
          <Select id="perf-compute-scope" value={scope} onChange={(e) => setScope(e.target.value as StatementScope)}>
            <option value="PAPER">PAPER</option>
            <option value="LIVE">LIVE</option>
          </Select>
        </div>
        <div>
          <label htmlFor="perf-compute-start" className="block text-sm font-medium text-fg">
            {t("performanceStatementsPage.periodStart")}
          </label>
          <Input id="perf-compute-start" type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
        </div>
        <div>
          <label htmlFor="perf-compute-end" className="block text-sm font-medium text-fg">
            {t("performanceStatementsPage.periodEnd")}
          </label>
          <Input id="perf-compute-end" type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
        </div>
        <div>
          <label htmlFor="perf-compute-methodology" className="block text-sm font-medium text-fg">
            {t("performanceStatementsPage.methodologyVersion")}
          </label>
          <Input
            id="perf-compute-methodology"
            placeholder={t("performanceStatementsPage.methodologyVersionPlaceholder")}
            value={methodologyVersion}
            onChange={(e) => setMethodologyVersion(e.target.value)}
          />
        </div>
      </div>
      <Button
        type="button"
        className="mt-3"
        loading={compute.isPending}
        disabled={!periodStart || !periodEnd}
        onClick={() => compute.mutate()}
      >
        {t("performanceStatementsPage.computeSubmit")}
      </Button>
      {compute.isError && (
        <ErrorMessage
          errorCode={compute.error instanceof ApiError ? compute.error.errorCode : undefined}
          message={compute.error instanceof Error ? compute.error.message : undefined}
          traceId={compute.error instanceof ApiError ? compute.error.traceId : undefined}
        />
      )}
    </Card>
  );
}

export interface PerformanceStatementsPageProps {
  computeStatement?: ComputeStatementFn;
  listStatements?: ListStatementsFn;
  getStatement?: GetStatementFn;
  correctStatement?: CorrectStatementFn;
}

export function PerformanceStatementsPage({
  computeStatement = computeStatementDefault,
  listStatements = listStatementsDefault,
  getStatement = getStatementDefault,
  correctStatement = correctStatementDefault,
}: PerformanceStatementsPageProps = {}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [scopeFilter, setScopeFilter] = useState<StatementScope | "">("");
  const [portfolioId, setPortfolioId] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const listParams: ListPerformanceStatementsParams = {
    scope: scopeFilter || undefined,
    portfolioId: portfolioId.trim() || undefined,
  };

  const listQuery = useQuery({
    queryKey: ["performanceStatements", listParams.scope, listParams.portfolioId],
    queryFn: () => listStatements(listParams),
  });

  const detailQuery = useQuery({
    queryKey: ["performanceStatement", selectedId, listParams.portfolioId],
    queryFn: () => getStatement(selectedId as string, { portfolioId: listParams.portfolioId }),
    enabled: selectedId !== null,
  });

  function refreshAfterMutation(id: string) {
    setSelectedId(id);
    void queryClient.invalidateQueries({ queryKey: ["performanceStatements"] });
    void queryClient.invalidateQueries({ queryKey: ["performanceStatement", id] });
  }

  const listRouted = listQuery.error ? routeApiError(listQuery.error) : null;
  const listCanRetry = listRouted?.kind === "refetch_retry" || listRouted?.kind === "backoff_retry";

  return (
    <AppShell>
      <div className="max-w-4xl space-y-6">
        <PageHeader title={t("performanceStatementsPage.title")} />

        <ComputeForm computeStatement={computeStatement} onComputed={refreshAfterMutation} />

        <Card data-testid="performance-statement-list">
          <CardTitle>{t("performanceStatementsPage.listTitle")}</CardTitle>
          <div className="flex flex-wrap gap-3">
            <div>
              <label htmlFor="perf-filter-scope" className="block text-sm font-medium text-fg">
                {t("performanceStatementsPage.scope")}
              </label>
              <Select
                id="perf-filter-scope"
                value={scopeFilter}
                onChange={(e) => setScopeFilter(e.target.value as StatementScope | "")}
              >
                <option value="">{t("performanceStatementsPage.scopeAll")}</option>
                <option value="PAPER">PAPER</option>
                <option value="LIVE">LIVE</option>
              </Select>
            </div>
            <div>
              <label htmlFor="perf-filter-portfolio" className="block text-sm font-medium text-fg">
                {t("performanceStatementsPage.portfolioId")}
              </label>
              <Input
                id="perf-filter-portfolio"
                value={portfolioId}
                onChange={(e) => setPortfolioId(e.target.value)}
                placeholder={t("performanceStatementsPage.portfolioIdPlaceholder")}
              />
            </div>
          </div>

          {listQuery.isError ? (
            <ErrorMessage
              errorCode={listQuery.error instanceof ApiError ? listQuery.error.errorCode : undefined}
              message={listQuery.error instanceof Error ? listQuery.error.message : undefined}
              traceId={listQuery.error instanceof ApiError ? listQuery.error.traceId : undefined}
              retryAfterSec={listRouted?.kind === "backoff_retry" ? listRouted.afterSec : undefined}
              onRetry={listCanRetry ? () => listQuery.refetch() : undefined}
            />
          ) : listQuery.isLoading ? (
            <LoadingState />
          ) : (listQuery.data?.statements.length ?? 0) === 0 ? (
            <EmptyState>{t("performanceStatementsPage.listEmpty")}</EmptyState>
          ) : (
            <ul className="mt-3 divide-y divide-border" data-testid="performance-statement-items">
              {listQuery.data!.statements.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(s.id)}
                    className="flex w-full items-center justify-between gap-2 py-2 text-left hover:bg-surface-hover"
                    aria-current={selectedId === s.id}
                  >
                    <span>
                      {s.scope} · {s.periodStart} ~ {s.periodEnd}
                    </span>
                    <Badge tone={stateTone(s.state)}>{s.state}</Badge>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {selectedId !== null &&
          (detailQuery.isLoading ? (
            <LoadingState />
          ) : detailQuery.isError ? (
            isResourceNotFound(detailQuery.error) ? (
              <NotFoundState
                title={t("performanceStatementsPage.notFoundTitle")}
                description={t("performanceStatementsPage.notFoundDescription")}
              />
            ) : (
              <ErrorMessage
                errorCode={detailQuery.error instanceof ApiError ? detailQuery.error.errorCode : undefined}
                message={detailQuery.error instanceof Error ? detailQuery.error.message : undefined}
                traceId={detailQuery.error instanceof ApiError ? detailQuery.error.traceId : undefined}
                onRetry={
                  classifyServerError(detailQuery.error).kind === "retryable" ? () => detailQuery.refetch() : undefined
                }
              />
            )
          ) : detailQuery.data ? (
            <StatementDetail
              statement={detailQuery.data}
              correctStatement={correctStatement}
              onCorrected={() => refreshAfterMutation(detailQuery.data!.id)}
            />
          ) : null)}
      </div>
    </AppShell>
  );
}
