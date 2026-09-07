// CH-4b (task-2012): drawings restore/save error banners — same shape as
// ChartLayoutErrorBanners.tsx, but for useChartDrawings' own status machine.
// Two of its outcomes ("not_found" on restore, "conflict"/"not_found" on save)
// are already classified by chart-engine's loadDrawings/saveDrawings (which
// route through routeApiError internally) — those render with a fixed,
// deterministic errorCode and no retry (matches classifyRetry's own gating for
// those codes). Only genuinely unexpected failures carry a raw error, which is
// routed again here via routeApiError, exactly like the layout banner.
import { ApiError } from "@aios/api-client";
import { routeApiError } from "@aios/shared-types";
import { ErrorMessage } from "../../components/ErrorMessage";
import type { UseChartDrawingsResult } from "./useChartDrawings";

export interface ChartDrawingsErrorBannersProps {
  readonly drawings: UseChartDrawingsResult;
}

export function ChartDrawingsErrorBanners({ drawings }: ChartDrawingsErrorBannersProps) {
  const restoreRouted = drawings.restoreStatus === "restore_failed" ? routeApiError(drawings.restoreError) : null;
  const saveRouted = drawings.saveStatus === "error" ? routeApiError(drawings.saveError) : null;

  return (
    <>
      {drawings.restoreStatus === "not_found" && <ErrorMessage errorCode="RESOURCE_NOT_FOUND" />}
      {drawings.restoreStatus === "restore_failed" && (
        <ErrorMessage
          errorCode={drawings.restoreError instanceof ApiError ? drawings.restoreError.errorCode : undefined}
          message={drawings.restoreError instanceof Error ? drawings.restoreError.message : undefined}
          traceId={drawings.restoreError instanceof ApiError ? drawings.restoreError.traceId : undefined}
          retryAfterSec={restoreRouted?.kind === "backoff_retry" ? restoreRouted.afterSec : undefined}
          onRetry={
            restoreRouted?.kind === "refetch_retry" || restoreRouted?.kind === "backoff_retry"
              ? drawings.retryRestore
              : undefined
          }
        />
      )}
      {drawings.saveStatus === "conflict" && <ErrorMessage errorCode="STATE_CONCURRENCY_CONFLICT" />}
      {drawings.saveStatus === "not_found" && <ErrorMessage errorCode="RESOURCE_NOT_FOUND" />}
      {saveRouted && (
        <ErrorMessage
          errorCode={drawings.saveError instanceof ApiError ? drawings.saveError.errorCode : undefined}
          message={drawings.saveError instanceof Error ? drawings.saveError.message : undefined}
          traceId={drawings.saveError instanceof ApiError ? drawings.saveError.traceId : undefined}
          retryAfterSec={saveRouted.kind === "backoff_retry" ? saveRouted.afterSec : undefined}
        />
      )}
    </>
  );
}
