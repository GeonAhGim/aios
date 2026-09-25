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
import { useTranslation } from "react-i18next";

const VAPID_PUBLIC_KEY = import.meta.env.VITE_VAPID_PUBLIC_KEY ?? "";

// UX-17 DoD(구독·해지·수신): 브라우저 권한 흐름 + device_tokens 등록/해지.
// usePushNotifications의 status를 그대로 문구로 노출 — "denied"는 브라우저 설정
// 안내, "error"는 재시도 가능한 실패임을 구분해서 보여준다.
function PushNotificationCard() {
  const { t } = useTranslation();
  const push = usePushNotifications();

  if (push.status === "unsupported") {
    return (
      <Card>
        <CardTitle>{t("legacy.notificationSettingsPage.t1")}</CardTitle>
        <p className="text-sm text-fg-muted">{t("legacy.notificationSettingsPage.t2")}</p>
      </Card>
    );
  }

  return (
    <Card>
      <CardTitle>{t("legacy.notificationSettingsPage.t3")}</CardTitle>
      <div className="space-y-3">
        {push.status === "subscribed" ? (
          <div className="flex items-center justify-between text-sm text-fg">
            <span>{t("legacy.notificationSettingsPage.t4")}</span>
            <Button variant="secondary" size="sm" loading={push.isDisabling} onClick={() => push.disable()}>
              {t("legacy.notificationSettingsPage.t5")}</Button>
          </div>
        ) : (
          <div className="flex items-center justify-between text-sm text-fg">
            <span>{t("legacy.notificationSettingsPage.t6")}</span>
            <Button
              variant="primary"
              size="sm"
              loading={push.isEnabling}
              onClick={() => push.enable(VAPID_PUBLIC_KEY)}
            >
              {t("legacy.notificationSettingsPage.t7")}</Button>
          </div>
        )}
        {push.status === "denied" && (
          <p className="text-xs text-danger">
            {t("legacy.notificationSettingsPage.t8")}</p>
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
  const { t } = useTranslation();
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
        <PageHeader title={t("legacy.notificationSettingsPage.title9")} />

        <PushNotificationCard />

        <Card>
          <CardTitle>{t("legacy.notificationSettingsPage.t10")}</CardTitle>
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
                {t("legacy.notificationSettingsPage.t11")}</p>
            </div>
          ) : null}
        </Card>

        <Card>
          <CardTitle>{t("legacy.notificationSettingsPage.t12")}</CardTitle>
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
            <EmptyState>{t("legacy.notificationSettingsPage.t13")}</EmptyState>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
