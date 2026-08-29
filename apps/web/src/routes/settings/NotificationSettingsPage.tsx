import {
  useNotificationHistory,
  useNotificationPreferences,
  useUpdateNotificationPreferences,
} from "@aios/shared-hooks";
import { AppShell } from "../../components/layout/AppShell";

export function NotificationSettingsPage() {
  const { data: preferences, isLoading } = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();
  const { data: history } = useNotificationHistory();

  return (
    <AppShell>
      <div className="max-w-2xl space-y-8">
        <h1 className="text-2xl font-semibold text-slate-100">알림 설정</h1>

        <section className="rounded-lg border border-slate-800 p-6">
          <h2 className="mb-4 text-lg font-medium text-slate-100">수신 설정</h2>
          {isLoading ? (
            <p className="text-slate-500">불러오는 중...</p>
          ) : preferences ? (
            <div className="space-y-2">
              {Object.entries(preferences).map(([key, value]) => (
                <label key={key} className="flex items-center justify-between text-slate-200">
                  <span className="text-sm">{key}</span>
                  <input
                    type="checkbox"
                    checked={value}
                    onChange={(e) => update.mutate({ [key]: e.target.checked })}
                  />
                </label>
              ))}
              <p className="text-xs text-slate-500">
                강제 알림(승인요청, 안전장치 경고 등)은 여기서 끌 수 없습니다.
              </p>
            </div>
          ) : null}
        </section>

        <section className="rounded-lg border border-slate-800 p-6">
          <h2 className="mb-4 text-lg font-medium text-slate-100">알림 이력</h2>
          {history && history.length > 0 ? (
            <ul className="divide-y divide-slate-800 text-sm">
              {history.map((h, i) => (
                <li key={i} className="flex items-center justify-between py-2">
                  <span className="text-slate-200">{h.eventType}</span>
                  <span className="text-slate-500">
                    {h.channel} ·{" "}
                    <span className={h.status === "SENT" ? "text-emerald-400" : "text-red-400"}>
                      {h.status}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-500">알림 이력이 없습니다.</p>
          )}
        </section>
      </div>
    </AppShell>
  );
}
