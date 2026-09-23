import { useAlgoProgress } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
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
  ProgressBar,
} from "@aios/ui-web";
import { useParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { NotFoundState } from "../../components/NotFoundState";
import { useTranslation } from "react-i18next";

function AlgoProgressDisplay({
  parentId,
  onRefresh,
}: {
  parentId: string;
  onRefresh: () => void;
}) {
  const { t } = useTranslation();
  const { data: progress, isLoading, error, refetch } = useAlgoProgress(parentId);

  if (isLoading) return <LoadingState />;

  if (error) {
    if (isResourceNotFound(error)) {
      return (
        <NotFoundState
          title={t("common.notFound")}
          description="알고리즘 주문이 존재하지 않습니다."
        />
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

  if (!progress) {
    return <EmptyState>데이터를 불러올 수 없습니다.</EmptyState>;
  }

  const progressPct =
    progress.totalSlices > 0
      ? ((progress.submittedSlices / progress.totalSlices) * 100).toFixed(1)
      : "0";

  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>집행 진행 상태</CardTitle>
        <div className="space-y-4">
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-fg">{t("common.progress")}</span>
              <span className="text-sm text-fg-muted">{progressPct}%</span>
            </div>
            <ProgressBar value={parseFloat(progressPct)} max={100} />
          </div>

          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">전체 슬라이스</div>
              <div className="text-lg font-semibold text-fg">{progress.totalSlices}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">제출됨</div>
              <div className="text-lg font-semibold text-fg">{progress.submittedSlices}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">대기 중</div>
              <div className="text-lg font-semibold text-fg">{progress.pendingSlices}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">상태</div>
              <div className="text-lg font-semibold text-fg">{progress.status}</div>
            </div>
          </div>

          <div>
            <div className="mb-1 text-sm font-medium text-fg">미체결 수량</div>
            <div className="text-2xl font-bold text-fg">{progress.remainingQty}</div>
          </div>

          {progress.demotedToTwap && (
            <div className="rounded-lg border border-warning-200 bg-warning-50 p-3">
              <div className="text-sm font-medium text-warning-900">TWAP로 강등됨</div>
              {progress.demotionReason && (
                <div className="mt-1 text-sm text-warning-800">{progress.demotionReason}</div>
              )}
            </div>
          )}

          <Button onClick={onRefresh} variant="secondary" className="w-full">
            새로고침
          </Button>
        </div>
      </Card>
    </div>
  );
}

export function ExecutionAlgoPage() {
  const { t } = useTranslation();
  const { parentId } = useParams<{ parentId: string }>();

  if (!parentId) {
    return (
      <AppShell>
        <NotFoundState title={t("common.notFound")} description="주문 ID가 없습니다." />
      </AppShell>
    );
  }

  const { refetch } = useAlgoProgress(parentId);

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title="알고리즘 집행 진행률" />
        <AlgoProgressDisplay
          parentId={parentId}
          onRefresh={() => void refetch()}
        />
      </div>
    </AppShell>
  );
}
