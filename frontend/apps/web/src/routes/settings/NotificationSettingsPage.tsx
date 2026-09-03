import {
  useNotificationHistory,
  useNotificationPreferences,
  useUpdateNotificationPreferences,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Card, CardTitle, EmptyState, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// spec §3.3 에러 taxonomy: 조회·변경 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 403/그 외를 각각 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(task-1155).
function NotificationError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  const canRetry = Boolean(onRetry) && (routed.kind === "refetch_retry" || routed.kind === "backoff_retry");
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

export function NotificationSettingsPage() {
  const {
    data: preferences,
    isLoading,
    isError: preferencesIsError,
    error: preferencesError,
    refetch: refetchPreferences,
  } = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();
  const {
    data: history,
    isError: historyIsError,
    error: historyError,
    refetch: refetchHistory,
  } = useNotificationHistory();

  return (
    <AppShell>
      <div className="max-w-2xl space-y-8">
        <PageHeader title="알림 설정" />

        <Card>
          <CardTitle>수신 설정</CardTitle>
          {preferencesIsError ? (
            <NotificationError error={preferencesError} onRetry={() => refetchPreferences()} />
          ) : isLoading ? (
            <LoadingState />
          ) : preferences ? (
            <div className="space-y-3">
              {Object.entries(preferences).map(([key, value]) => (
                <label key={key} className="flex items-center justify-between text-sm text-fg">
                  <span>{key}</span>
                  <input
                    type="checkbox"
                    checked={value}
                    onChange={(e) => update.mutate({ [key]: e.target.checked })}
                    className="accent-accent"
                  />
                </label>
              ))}
              {update.isError && <NotificationError error={update.error} />}
              <p className="text-xs text-fg-muted">
                강제 알림(승인요청, 안전장치 경고 등)은 여기서 끌 수 없습니다.
              </p>
            </div>
          ) : null}
        </Card>

        <Card>
          <CardTitle>알림 이력</CardTitle>
          {historyIsError ? (
            <NotificationError error={historyError} onRetry={() => refetchHistory()} />
          ) : history && history.length > 0 ? (
            <ul className="divide-y divide-border text-sm">
              {history.map((h, i) => (
                <li key={i} className="flex items-center justify-between py-2">
                  <span className="text-fg">{h.eventType}</span>
                  <span className="flex items-center gap-2 text-fg-muted">
                    {h.channel} <StatusBadge status={h.status} />
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>알림 이력이 없습니다.</EmptyState>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
