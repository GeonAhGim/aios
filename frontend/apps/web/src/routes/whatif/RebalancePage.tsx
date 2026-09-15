import {
  ApiError,
  createWhatIfClient,
  WhatIfRouteNotImplementedError,
  type WhatIfClient,
} from "@aios/api-client";
import {
  describeRebalanceTargetIssues,
  routeApiError,
  type WhatIfOrderInput,
  type WhatIfRebalanceTargetInput,
  type WhatIfRebalanceTargetValidationIssue,
} from "@aios/shared-types";
import { useAuthStore } from "@aios/shared-hooks";
import { Alert, Button, Card, CardTitle, EmptyState, Field, Input, LoadingState, PageHeader } from "@aios/ui-web";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { WhatIfPanel } from "./WhatIfPanel";

// spec L4_product_experience_and_discovery_v1.0.md UX-12 — RebalancePage.tsx(목표
// 비중 → 리밸런싱 계획). 선행 리프 UX-9/UX-11(src/foundation/whatif/{domain/impact,
// application/rebalance_plan}.py, core/portfolio/rebalance.py 연결)와 이를 감싸는
// API 라우터(src/api/routers/whatif.py)는 아직 없다(apiRoutes.ts의
// whatif.rebalancePlan implemented=false 참조) — ScreenerPage.tsx(UX-8)와 동일
// 관용으로, 라우터가 없어도 화면·상태 처리는 완성해 두고 whatIfClient prop 주입으로
// 테스트한다. 응답 형태(trades/turnoverPct/estCost/skipped)는 이미 있는
// core/portfolio/rebalance.py의 RebalancePlan/TradeLeg를 그대로 따른다.
//
// 각 거래 초안 행의 "영향 미리보기"는 WhatIfPanel.tsx(UX-10 preview_order 선행
// 프론트)를 그대로 재사용한다 — 이 화면이 영향 계산을 다시 구현하지 않는다.
export interface RebalancePageProps {
  whatIfClient?: WhatIfClient;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function defaultWhatIfClient(): WhatIfClient {
  const getToken = () => useAuthStore.getState().token;
  return createWhatIfClient(baseUrl, getToken);
}

interface TargetRow extends WhatIfRebalanceTargetInput {
  id: string;
}

let targetRowSeq = 0;
function nextTargetRowId(): string {
  targetRowSeq += 1;
  return `target-${targetRowSeq}`;
}

function emptyTargetRow(): TargetRow {
  return { id: nextTargetRowId(), symbol: "", targetWeightPct: "" };
}

// WhatIfPanel.tsx의 WhatIfErrorBanner와 동일 관용 — 유령 경로 오류
// (WhatIfRouteNotImplementedError)도 별도 렌더 분기를 만들지 않고 같은
// ErrorMessage로 흘려보내되, 재시도 버튼만 강제로 끈다.
function RebalanceErrorBanner({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const notImplemented = error instanceof WhatIfRouteNotImplementedError;
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

function targetIssueMessage(
  t: TFunction<"translation", undefined>,
  issue: WhatIfRebalanceTargetValidationIssue,
): string {
  switch (issue.code) {
    case "targets_required":
      return t("whatif.targetValidation.targetsRequired");
    case "target_symbol_required":
      return t("whatif.targetValidation.targetSymbolRequired", { position: issue.index + 1 });
    case "target_weight_invalid":
      return t("whatif.targetValidation.targetWeightInvalid", { position: issue.index + 1 });
    case "target_symbol_duplicate":
      return t("whatif.targetValidation.targetSymbolDuplicate", { position: issue.index + 1 });
  }
}

export function RebalancePage({ whatIfClient }: RebalancePageProps) {
  const { t } = useTranslation();
  const client = useMemo(() => whatIfClient ?? defaultWhatIfClient(), [whatIfClient]);

  const [targets, setTargets] = useState<TargetRow[]>([emptyTargetRow()]);
  const [validationIssues, setValidationIssues] = useState<WhatIfRebalanceTargetValidationIssue[]>([]);
  const [submitted, setSubmitted] = useState<WhatIfRebalanceTargetInput[] | null>(null);
  const [previewOrder, setPreviewOrder] = useState<WhatIfOrderInput | null>(null);
  const [previewSeq, setPreviewSeq] = useState(0);

  const query = useQuery({
    queryKey: ["whatif-rebalance-plan", submitted],
    queryFn: () => client.planRebalance(submitted as WhatIfRebalanceTargetInput[]),
    enabled: submitted !== null,
  });

  function updateTarget(id: string, patch: Partial<WhatIfRebalanceTargetInput>) {
    setTargets((rows) => rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }

  function addTarget() {
    setTargets((rows) => [...rows, emptyTargetRow()]);
  }

  function removeTarget(id: string) {
    setTargets((rows) => rows.filter((row) => row.id !== id));
  }

  function handleRun() {
    const definition = targets.map(({ id: _id, ...rest }) => rest);
    const issues = describeRebalanceTargetIssues(definition);
    setValidationIssues(issues);
    if (issues.length > 0) return;
    setSubmitted(definition);
  }

  function handlePreview(order: WhatIfOrderInput) {
    setPreviewOrder(order);
    setPreviewSeq((seq) => seq + 1);
  }

  const trades = query.data?.trades ?? [];

  return (
    <AppShell>
      <div className="max-w-4xl space-y-6">
        <PageHeader title={t("whatif.rebalance.pageTitle")} />

        <Card>
          <CardTitle>{t("whatif.rebalance.targetBuilderTitle")}</CardTitle>
          <div className="space-y-4">
            <div className="space-y-3">
              {targets.map((row, index) => (
                <div
                  key={row.id}
                  data-testid={`rebalance-target-row-${index}`}
                  className="grid grid-cols-2 gap-3 rounded-md border border-border p-3 sm:grid-cols-3"
                >
                  <Field label={t("whatif.rebalance.symbolLabel")}>
                    <Input
                      data-testid={`rebalance-target-${index}-symbol`}
                      value={row.symbol}
                      onChange={(e) => updateTarget(row.id, { symbol: e.target.value })}
                    />
                  </Field>
                  <Field label={t("whatif.rebalance.weightLabel")}>
                    <Input
                      data-testid={`rebalance-target-${index}-weight`}
                      value={row.targetWeightPct}
                      onChange={(e) => updateTarget(row.id, { targetWeightPct: e.target.value })}
                    />
                  </Field>
                  <div className="flex items-end">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      data-testid={`rebalance-target-${index}-remove`}
                      onClick={() => removeTarget(row.id)}
                    >
                      {t("whatif.rebalance.removeTarget")}
                    </Button>
                  </div>
                </div>
              ))}
            </div>

            <Button type="button" variant="secondary" size="sm" data-testid="rebalance-add-target" onClick={addTarget}>
              {t("whatif.rebalance.addTarget")}
            </Button>

            {validationIssues.length > 0 && (
              <div data-testid="rebalance-validation-alert">
                <Alert tone="danger">
                  <ul className="list-disc space-y-1 pl-4">
                    {validationIssues.map((issue, i) => (
                      <li key={i}>{targetIssueMessage(t, issue)}</li>
                    ))}
                  </ul>
                </Alert>
              </div>
            )}

            <Button type="button" data-testid="rebalance-run" onClick={handleRun}>
              {t("whatif.rebalance.run")}
            </Button>
          </div>
        </Card>

        <Card>
          <CardTitle>{t("whatif.rebalance.resultsTitle")}</CardTitle>

          {submitted === null && <EmptyState>{t("whatif.rebalance.beforeRun")}</EmptyState>}
          {submitted !== null && query.isLoading && <LoadingState />}
          {submitted !== null && query.isError && (
            <RebalanceErrorBanner error={query.error} onRetry={() => query.refetch()} />
          )}
          {submitted !== null && !query.isError && !query.isLoading && query.data && (
            <div className="space-y-3">
              <p className="text-sm text-fg-muted" data-testid="rebalance-turnover">
                {t("whatif.rebalance.turnoverLabel", { value: query.data.turnoverPct })}
              </p>
              <p className="text-sm text-fg-muted" data-testid="rebalance-est-cost">
                {t("whatif.rebalance.estCostLabel", { value: query.data.estCost })}
              </p>

              {trades.length === 0 && <EmptyState>{t("whatif.rebalance.resultsEmpty")}</EmptyState>}

              {trades.length > 0 && (
                <div className="divide-y divide-border">
                  {trades.map((trade) => (
                    <div
                      key={trade.symbol}
                      data-testid={`rebalance-trade-${trade.symbol}`}
                      className="flex items-center justify-between gap-4 py-2"
                    >
                      <div className="text-sm">
                        <p className="font-medium text-fg">
                          {trade.symbol} · {trade.side} · {trade.quantity}
                        </p>
                        <p className="text-fg-muted">
                          @{trade.price} · {trade.notional} · Δ{trade.deltaWeightPct}%
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        data-testid={`rebalance-trade-${trade.symbol}-preview`}
                        onClick={() =>
                          handlePreview({ symbol: trade.symbol, side: trade.side, quantity: trade.quantity })
                        }
                      >
                        {t("whatif.rebalance.previewImpact")}
                      </Button>
                    </div>
                  ))}
                </div>
              )}

              {query.data.skipped.length > 0 && (
                <div>
                  <p className="text-sm font-medium text-fg">{t("whatif.rebalance.skippedTitle")}</p>
                  <ul className="list-disc space-y-1 pl-4 text-sm text-fg-muted" data-testid="rebalance-skipped-list">
                    {query.data.skipped.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Card>

        {previewOrder !== null && (
          <WhatIfPanel
            key={`${previewOrder.symbol}-${previewOrder.side}-${previewOrder.quantity}-${previewSeq}`}
            whatIfClient={client}
            initialOrder={previewOrder}
          />
        )}
      </div>
    </AppShell>
  );
}
