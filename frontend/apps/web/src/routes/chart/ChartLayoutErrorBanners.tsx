// CH-6a 순수 이동(task-2011): ChartPage.tsx의 CH-8 복원/저장 에러 배너 JSX를 그대로
// 옮긴다 — 로직 변경 없음.
import { ApiError } from "@aios/api-client";
import { routeApiError } from "@aios/shared-types";
import { ErrorMessage } from "../../components/ErrorMessage";
import type { UseChartLayoutResult } from "./useChartLayout";

export interface ChartLayoutErrorBannersProps {
  readonly layout: UseChartLayoutResult;
}

export function ChartLayoutErrorBanners({ layout }: ChartLayoutErrorBannersProps) {
  const restoreRouted = layout.restoreError ? routeApiError(layout.restoreError) : null;
  const saveRouted = layout.saveStatus === "error" ? routeApiError(layout.saveError) : null;

  return (
    <>
      {layout.status === "restore_failed" && (
        <ErrorMessage
          errorCode={layout.restoreError instanceof ApiError ? layout.restoreError.errorCode : undefined}
          message={layout.restoreError instanceof Error ? layout.restoreError.message : undefined}
          traceId={layout.restoreError instanceof ApiError ? layout.restoreError.traceId : undefined}
          retryAfterSec={restoreRouted?.kind === "backoff_retry" ? restoreRouted.afterSec : undefined}
          onRetry={
            restoreRouted?.kind === "refetch_retry" || restoreRouted?.kind === "backoff_retry"
              ? layout.retryRestore
              : undefined
          }
        />
      )}
      {saveRouted && (
        <ErrorMessage
          errorCode={layout.saveError instanceof ApiError ? layout.saveError.errorCode : undefined}
          message={layout.saveError instanceof Error ? layout.saveError.message : undefined}
          traceId={layout.saveError instanceof ApiError ? layout.saveError.traceId : undefined}
          retryAfterSec={saveRouted.kind === "backoff_retry" ? saveRouted.afterSec : undefined}
          onRetry={saveRouted.kind === "refetch_retry" || saveRouted.kind === "backoff_retry" ? layout.save : undefined}
        />
      )}
    </>
  );
}
