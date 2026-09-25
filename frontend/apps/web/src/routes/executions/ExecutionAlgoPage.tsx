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

function AlgoProgressDisplay({ parentId }: { parentId: string }) {
  const { t } = useTranslation();
  const { data: progress, isLoading, error, refetch } = useAlgoProgress(parentId);

  if (isLoading) return <LoadingState />;

  if (error) {
    if (isResourceNotFound(error)) {
      return (
        <NotFoundState
          title={t("common.notFound")}
          description={t("executionAlgoPage.notFoundDescription")}
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
    return <EmptyState>{t("executionAlgoPage.unavailable")}</EmptyState>;
  }

  const progressPct =
    progress.totalSlices > 0
      ? ((progress.submittedSlices / progress.totalSlices) * 100).toFixed(1)
      : "0";

  return (
    <div className="space-y-4">
      <Card>
        <CardTitle>{t("executionAlgoPage.cardTitle")}</CardTitle>
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
              <div className="text-xs font-medium text-fg-muted">{t("executionAlgoPage.totalSlices")}</div>
              <div className="text-lg font-semibold text-fg">{progress.totalSlices}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">{t("executionAlgoPage.submittedSlices")}</div>
              <div className="text-lg font-semibold text-fg">{progress.submittedSlices}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">{t("executionAlgoPage.pendingSlices")}</div>
              <div className="text-lg font-semibold text-fg">{progress.pendingSlices}</div>
            </div>
            <div className="rounded border border-border bg-bg-secondary p-3">
              <div className="text-xs font-medium text-fg-muted">{t("executionAlgoPage.status")}</div>
              <div className="text-lg font-semibold text-fg">{progress.status}</div>
            </div>
          </div>

          <div>
            <div className="mb-1 text-sm font-medium text-fg">{t("executionAlgoPage.remainingQty")}</div>
            <div className="text-2xl font-bold text-fg">{progress.remainingQty}</div>
          </div>

          {progress.demotedToTwap && (
            <div className="rounded-lg border border-warning-200 bg-warning-50 p-3">
              <div className="text-sm font-medium text-warning-900">{t("executionAlgoPage.demotedToTwap")}</div>
              {progress.demotionReason && (
                <div className="mt-1 text-sm text-warning-800">{progress.demotionReason}</div>
              )}
            </div>
          )}

          <Button onClick={() => void refetch()} variant="secondary" className="w-full">
            {t("executionAlgoPage.refresh")}
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
        <NotFoundState title={t("common.notFound")} description={t("executionAlgoPage.missingOrderId")} />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title={t("executionAlgoPage.pageTitle")} />
        <AlgoProgressDisplay parentId={parentId} />
      </div>
    </AppShell>
  );
}
