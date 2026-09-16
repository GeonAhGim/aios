import { useNotificationHistory } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Card, CardTitle, EmptyState, LoadingState, PageHeader, Select, Stat, StatusBadge } from "@aios/ui-web";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { computeDailyDigest } from "../../notifications/digest";

const CHANNEL_FILTERS = ["ALL", "EMAIL", "PUSH", "IN_APP"] as const;
type ChannelFilter = (typeof CHANNEL_FILTERS)[number];

// spec §3.3 에러 taxonomy: 이력 조회 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 403/그 외를 각각 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(NotificationSettingsPage.tsx NotificationError와 동일 관용).
function HistoryError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
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

// UX-18 DoD(이력·요약): 알림 이력(FD-17.3)을 날짜별 "일간 다이제스트"로 묶어
// 보여준다 — 실제 이메일/푸시 다이제스트 발송은 백엔드(FD-17.1/17.2 gateway) 몫이고,
// 여기서는 사용자가 그 발송 결과를 이력에서 확인·요약할 수 있게 한다.
export function NotificationCenterPage() {
  const { t } = useTranslation();
  const [channel, setChannel] = useState<ChannelFilter>("ALL");
  const { data: history, isError, error, refetch } = useNotificationHistory();

  const filtered = useMemo(
    () => (channel === "ALL" ? (history ?? []) : (history ?? []).filter((h) => h.channel === channel)),
    [history, channel],
  );
  const digest = useMemo(() => computeDailyDigest(filtered), [filtered]);

  return (
    <AppShell>
      <div className="max-w-3xl space-y-6">
        <PageHeader title={t("notificationCenter.title")} />

        <Card>
          <div className="flex items-center justify-between gap-4">
            <CardTitle className="mb-0">{t("notificationCenter.filterLabel")}</CardTitle>
            <Select
              value={channel}
              onChange={(e) => setChannel(e.target.value as ChannelFilter)}
              className="w-auto"
              aria-label={t("notificationCenter.filterLabel")}
            >
              {CHANNEL_FILTERS.map((c) => (
                <option key={c} value={c}>
                  {t(`notificationCenter.channel.${c}`)}
                </option>
              ))}
            </Select>
          </div>
        </Card>

        {isError ? (
          <HistoryError error={error} onRetry={() => refetch()} />
        ) : !history ? (
          <LoadingState />
        ) : digest.length === 0 ? (
          <EmptyState>{t("notificationCenter.empty")}</EmptyState>
        ) : (
          <div className="space-y-4">
            {digest.map((group) => (
              <Card key={group.date}>
                <div className="flex items-center justify-between">
                  <CardTitle>
                    {group.date === "unknown" ? t("notificationCenter.unknownDate") : group.date}
                  </CardTitle>
                  <span className="text-sm text-fg-muted">
                    {t("notificationCenter.totalCount", { count: group.total })}
                  </span>
                </div>
                <div className="mb-4 grid grid-cols-3 gap-3">
                  <Stat label={t("notificationCenter.stat.total")} value={group.total} />
                  <Stat label={t("notificationCenter.stat.sent")} value={group.sentCount} tone="success" />
                  <Stat label={t("notificationCenter.stat.failed")} value={group.failedCount} tone="danger" />
                </div>
                <ul className="divide-y divide-border text-sm">
                  {group.entries.map((entryItem, i) => (
                    <li key={i} className="flex items-center justify-between py-2">
                      <span className="text-fg">{entryItem.eventType}</span>
                      <span className="flex items-center gap-2 text-fg-muted">
                        {t(`notificationCenter.channel.${entryItem.channel}`, { defaultValue: entryItem.channel })}
                        <StatusBadge status={entryItem.status} />
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
