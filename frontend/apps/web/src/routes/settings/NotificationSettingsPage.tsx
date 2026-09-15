import {
  useNotificationHistory,
  useNotificationPreferences,
  useUpdateNotificationPreferences,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Button, Card, CardTitle, EmptyState, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { usePushNotifications } from "../../pwa/usePushNotifications";

const VAPID_PUBLIC_KEY = import.meta.env.VITE_VAPID_PUBLIC_KEY ?? "";

// UX-17 DoD(구독·해지·수신): 브라우저 권한 흐름 + device_tokens 등록/해지.
// usePushNotifications의 status를 그대로 문구로 노출 — "denied"는 브라우저 설정
// 안내, "error"는 재시도 가능한 실패임을 구분해서 보여준다.
function PushNotificationCard() {
  const push = usePushNotifications();

  if (push.status === "unsupported") {
    return (
      <Card>
        <CardTitle>푸시 알림</CardTitle>
        <p className="text-sm text-fg-muted">이 브라우저/기기는 푸시 알림을 지원하지 않습니다.</p>
      </Card>
    );
  }

  return (
    <Card>
      <CardTitle>푸시 알림</CardTitle>
      <div className="space-y-3">
        {push.status === "subscribed" ? (
          <div className="flex items-center justify-between text-sm text-fg">
            <span>이 기기로 푸시 알림을 받는 중입니다.</span>
            <Button variant="secondary" size="sm" loading={push.isDisabling} onClick={() => push.disable()}>
              구독 해지
            </Button>
          </div>
        ) : (
          <div className="flex items-center justify-between text-sm text-fg">
            <span>이 기기에서 푸시 알림을 받으려면 권한을 허용하세요.</span>
            <Button
              variant="primary"
              size="sm"
              loading={push.isEnabling}
              onClick={() => push.enable(VAPID_PUBLIC_KEY)}
            >
              알림 켜기
            </Button>
          </div>
        )}
        {push.status === "denied" && (
          <p className="text-xs text-danger">
            브라우저 알림 권한이 거부되었습니다. 브라우저 설정에서 이 사이트의 알림 권한을 허용한 뒤 다시 시도하세요.
          </p>
        )}
        {push.status === "error" && push.error && <ErrorMessage message={push.error.message} />}
      </div>
    </Card>
  );
}

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

        <PushNotificationCard />

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
