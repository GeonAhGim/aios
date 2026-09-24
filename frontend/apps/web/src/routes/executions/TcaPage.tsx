import { useLatestTca, useComputeTca } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import type { ComputeTcaRequest } from "@aios/shared-types";
import {
  classifyServerError,
  isResourceNotFound,
  routeApiError,
} from "@aios/shared-types";
import {
  Button,
  Card,
  CardTitle,
  EmptyState,
  LoadingState,
  PageHeader,
} from "@aios/ui-web";
import { useParams } from "react-router-dom";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { NotFoundState } from "../../components/NotFoundState";
import { useTranslation } from "react-i18next";

function TcaResultDisplay({
  parentId,
  onComputeTca,
  computeError,
}: {
  parentId: string;
  onComputeTca: (request: ComputeTcaRequest) => void;
  computeError: Error | null;
}) {
  const { t } = useTranslation();
  const { data: tcaResult, isLoading, error, refetch } = useLatestTca(parentId);
  const computeMutation = useComputeTca();
  const [showComputeForm, setShowComputeForm] = useState(false);

  if (isLoading) return <LoadingState />;

  if (error) {
    if (isResourceNotFound(error)) {
      return (
        <div className="space-y-4">
          <EmptyState>{t("tcaPage.notComputedYet")}</EmptyState>
          <Button onClick={() => setShowComputeForm(true)} className="w-full">
            {t("tcaPage.startCompute")}
          </Button>
          {showComputeForm && (
            <ComputeTcaForm
              parentId={parentId}
              onSubmit={onComputeTca}
              onClose={() => setShowComputeForm(false)}
              isLoading={computeMutation.isPending}
              computeError={computeError}
            />
          )}
        </div>
      );
    }
    const routed = routeApiError(error);
    const serverError = classifyServerError(error);
    return (
      <ErrorMessage
        errorCode={error instanceof ApiError ? error.errorCode : undefined}
        message={error instanceof Error ? error.message : undefined}
        traceId={error instanceof ApiError ? error.traceId : undefined}
        retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
        onRetry={serverError.kind === "retryable" ? () => void refetch() : undefined}
      />
    );
  }

  if (!tcaResult) {
    return <EmptyState>{t("tcaPage.noData")}</EmptyState>;
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>{t("tcaPage.resultTitle")}</CardTitle>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">Arrival BPS</div>
              <div className="text-lg font-semibold text-fg">{tcaResult.result.arrivalBps}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">VWAP BPS</div>
              <div className="text-lg font-semibold text-fg">{tcaResult.result.vwapBps}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">Impact BPS</div>
              <div className="text-lg font-semibold text-fg">{tcaResult.result.impactBps}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">Fees BPS</div>
              <div className="text-lg font-semibold text-fg">{tcaResult.result.feesBps}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">Opportunity BPS</div>
              <div className="text-lg font-semibold text-fg">{tcaResult.result.opportunityBps}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">Revision</div>
              <div className="text-lg font-semibold text-fg">{tcaResult.revision}</div>
            </div>
          </div>

          <div className="text-sm text-fg-muted">
            {t("tcaPage.computedAt", { time: new Date(tcaResult.computedAt).toLocaleString() })}
          </div>

          <Button
            onClick={() => setShowComputeForm(!showComputeForm)}
            variant="secondary"
            className="w-full"
          >
            {showComputeForm ? t("tcaPage.close") : t("tcaPage.recompute")}
          </Button>

          {showComputeForm && (
            <ComputeTcaForm
              parentId={parentId}
              onSubmit={onComputeTca}
              onClose={() => setShowComputeForm(false)}
              isLoading={computeMutation.isPending}
              computeError={computeError}
            />
          )}
        </div>
      </Card>
    </div>
  );
}

function ComputeTcaForm({
  parentId: _parentId,
  onSubmit,
  onClose,
  isLoading,
  computeError,
}: {
  parentId: string;
  onSubmit: (request: ComputeTcaRequest) => void;
  onClose: () => void;
  isLoading: boolean;
  computeError: Error | null;
}) {
  const { t } = useTranslation();
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [priceAtArrival, setPriceAtArrival] = useState("100");
  const [fillsJson, setFillsJson] = useState('[{"price": "100", "qty": "10"}]');
  const [barsJson, setBarsJson] = useState('[{"close": "100", "volume": "1000"}]');
  const [spreadCost, setSpreadCost] = useState("0");
  const [fees, setFees] = useState("0");
  const [totalCost, setTotalCost] = useState("0");
  const [revision, setRevision] = useState("1");
  const [formError, setFormError] = useState<string | null>(null);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);

    let fills: unknown;
    let bars: unknown;
    try {
      fills = JSON.parse(fillsJson);
      bars = JSON.parse(barsJson);
    } catch {
      setFormError(t("tcaPage.invalidJson"));
      return;
    }
    if (!Array.isArray(fills) || fills.length === 0 || !Array.isArray(bars) || bars.length === 0) {
      setFormError(t("tcaPage.emptyFillsOrBars"));
      return;
    }

    const request: ComputeTcaRequest = {
      side,
      fills,
      priceAtArrivalTs: priceAtArrival,
      bars,
      spreadCost,
      fees,
      totalCost,
      computedAt: new Date().toISOString(),
      revision: parseInt(revision, 10) || 1,
    };
    onSubmit(request);
  }

  return (
    <Card className="border-primary-200 bg-primary-50">
      <CardTitle>{t("tcaPage.recomputeTitle")}</CardTitle>
      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="tca-compute-side" className="block text-sm font-medium text-fg">
              Side
            </label>
            <select
              id="tca-compute-side"
              value={side}
              onChange={(e) => setSide(e.target.value as "BUY" | "SELL")}
              className="w-full rounded border border-border px-2 py-1 text-sm"
            >
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </div>
          <div>
            <label htmlFor="tca-compute-revision" className="block text-sm font-medium text-fg">
              Revision
            </label>
            <input
              id="tca-compute-revision"
              type="number"
              value={revision}
              onChange={(e) => setRevision(e.target.value)}
              className="w-full rounded border border-border px-2 py-1 text-sm"
              min="1"
            />
          </div>
          <div>
            <label htmlFor="tca-compute-price-at-arrival" className="block text-sm font-medium text-fg">
              Price at Arrival
            </label>
            <input
              id="tca-compute-price-at-arrival"
              type="text"
              value={priceAtArrival}
              onChange={(e) => setPriceAtArrival(e.target.value)}
              className="w-full rounded border border-border px-2 py-1 text-sm"
            />
          </div>
          <div>
            <label htmlFor="tca-compute-spread-cost" className="block text-sm font-medium text-fg">
              Spread Cost
            </label>
            <input
              id="tca-compute-spread-cost"
              type="text"
              value={spreadCost}
              onChange={(e) => setSpreadCost(e.target.value)}
              className="w-full rounded border border-border px-2 py-1 text-sm"
            />
          </div>
          <div>
            <label htmlFor="tca-compute-fees" className="block text-sm font-medium text-fg">
              Fees
            </label>
            <input
              id="tca-compute-fees"
              type="text"
              value={fees}
              onChange={(e) => setFees(e.target.value)}
              className="w-full rounded border border-border px-2 py-1 text-sm"
            />
          </div>
          <div className="col-span-2">
            <label htmlFor="tca-compute-fills" className="block text-sm font-medium text-fg">
              {t("tcaPage.fillsLabel")}
            </label>
            <textarea
              id="tca-compute-fills"
              value={fillsJson}
              onChange={(e) => setFillsJson(e.target.value)}
              rows={2}
              className="w-full rounded border border-border px-2 py-1 font-mono text-sm"
            />
          </div>
          <div className="col-span-2">
            <label htmlFor="tca-compute-bars" className="block text-sm font-medium text-fg">
              {t("tcaPage.barsLabel")}
            </label>
            <textarea
              id="tca-compute-bars"
              value={barsJson}
              onChange={(e) => setBarsJson(e.target.value)}
              rows={2}
              className="w-full rounded border border-border px-2 py-1 font-mono text-sm"
            />
          </div>
          <div>
            <label htmlFor="tca-compute-total-cost" className="block text-sm font-medium text-fg">
              Total Cost
            </label>
            <input
              id="tca-compute-total-cost"
              type="text"
              value={totalCost}
              onChange={(e) => setTotalCost(e.target.value)}
              className="w-full rounded border border-border px-2 py-1 text-sm"
            />
          </div>
        </div>
        {formError && (
          <p role="alert" className="text-sm text-danger">
            {formError}
          </p>
        )}
        {!formError && computeError && (
          <ErrorMessage
            errorCode={computeError instanceof ApiError ? computeError.errorCode : undefined}
            message={computeError.message}
          />
        )}
        <div className="flex gap-2">
          <Button
            type="submit"
            loading={isLoading}
            className="flex-1"
          >
            {t("tcaPage.compute")}
          </Button>
          <Button
            type="button"
            onClick={onClose}
            variant="secondary"
            className="flex-1"
          >
            {t("tcaPage.cancel")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

export function TcaPage() {
  const { t } = useTranslation();
  const { parentId } = useParams<{ parentId: string }>();
  const computeMutation = useComputeTca();
  const [computeError, setComputeError] = useState<Error | null>(null);

  if (!parentId) {
    return (
      <AppShell>
        <NotFoundState title={t("common.notFound")} description={t("tcaPage.missingOrderId")} />
      </AppShell>
    );
  }

  async function handleComputeTca(request: ComputeTcaRequest) {
    setComputeError(null);
    try {
      // parentId is validated non-empty by the `if (!parentId)` return above; TS
      // control-flow narrowing doesn't carry into this nested function declaration.
      await computeMutation.mutateAsync({ parentId: parentId!, request });
    } catch (err) {
      console.error("Failed to compute TCA:", err);
      setComputeError(err instanceof Error ? err : new Error(String(err)));
    }
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title={t("tcaPage.pageTitle")} />
        <TcaResultDisplay
          parentId={parentId}
          onComputeTca={handleComputeTca}
          computeError={computeError}
        />
      </div>
    </AppShell>
  );
}
