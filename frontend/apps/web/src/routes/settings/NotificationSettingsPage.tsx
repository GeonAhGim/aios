import {
  useNotificationHistory,
  useNotificationPreferences,
  useUpdateNotificationPreferences,
} from "@aios/shared-hooks";
import { Card, CardTitle, EmptyState, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { AppShell } from "../../components/layout/AppShell";

export function NotificationSettingsPage() {
  const { data: preferences, isLoading } = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();
  const { data: history } = useNotificationHistory();

  return (
    <AppShell>
      <div className="max-w-2xl space-y-8">
        <PageHeader title="알림 설정" />

        <Card>
          <CardTitle>수신 설정</CardTitle>
          {isLoading ? (
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
              <p className="text-xs text-fg-muted">
                강제 알림(승인요청, 안전장치 경고 등)은 여기서 끌 수 없습니다.
              </p>
            </div>
          ) : null}
        </Card>

        <Card>
          <CardTitle>알림 이력</CardTitle>
          {history && history.length > 0 ? (
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
