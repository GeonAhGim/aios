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
}: {
  parentId: string;
  onComputeTca: (request: ComputeTcaRequest) => void;
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
          <EmptyState>TCA 데이터가 아직 계산되지 않았습니다.</EmptyState>
          <Button onClick={() => setShowComputeForm(true)} className="w-full">
            TCA 계산 시작
          </Button>
          {showComputeForm && (
            <ComputeTcaForm
              parentId={parentId}
              onSubmit={onComputeTca}
              onClose={() => setShowComputeForm(false)}
              isLoading={computeMutation.isPending}
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
    return <EmptyState>TCA 데이터가 없습니다.</EmptyState>;
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>TCA 분석 결과</CardTitle>
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
            계산 시간: {new Date(tcaResult.computedAt).toLocaleString()}
          </div>

          <Button
            onClick={() => setShowComputeForm(!showComputeForm)}
            variant="secondary"
            className="w-full"
          >
            {showComputeForm ? "닫기" : "다시 계산"}
          </Button>

          {showComputeForm && (
            <ComputeTcaForm
              parentId={parentId}
              onSubmit={onComputeTca}
              onClose={() => setShowComputeForm(false)}
              isLoading={computeMutation.isPending}
            />
          )}
        </div>
      </Card>
    </div>
  );
}

function ComputeTcaForm({
  parentId,
  onSubmit,
  onClose,
  isLoading,
}: {
  parentId: string;
  onSubmit: (request: ComputeTcaRequest) => void;
  onClose: () => void;
  isLoading: boolean;
}) {
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [priceAtArrival, setPriceAtArrival] = useState("100");
  const [spreadCost, setSpreadCost] = useState("0");
  const [fees, setFees] = useState("0");
  const [totalCost, setTotalCost] = useState("0");
  const [revision, setRevision] = useState("1");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const request: ComputeTcaRequest = {
      side,
      fills: [],
      priceAtArrivalTs: priceAtArrival,
      bars: [],
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
      <CardTitle>TCA 재계산</CardTitle>
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
        <div className="flex gap-2">
          <Button
            type="submit"
            loading={isLoading}
            className="flex-1"
          >
            계산
          </Button>
          <Button
            type="button"
            onClick={onClose}
            variant="secondary"
            className="flex-1"
          >
            취소
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

  if (!parentId) {
    return (
      <AppShell>
        <NotFoundState title={t("common.notFound")} description="주문 ID가 없습니다." />
      </AppShell>
    );
  }

  async function handleComputeTca(request: ComputeTcaRequest) {
    try {
      await computeMutation.mutateAsync({ parentId, request });
    } catch (err) {
      console.error("Failed to compute TCA:", err);
    }
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title="거래비용분석(TCA)" />
        <TcaResultDisplay
          parentId={parentId}
          onComputeTca={handleComputeTca}
        />
      </div>
    </AppShell>
  );
}
