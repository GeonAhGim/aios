import type {
  DeviceTokenRecord,
  DeviceTokenRegisterRequest,
  NotificationHistoryEntry,
  NotificationPreferences,
  PreferenceUpdateResult,
} from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-17 알림 / FD-21 디바이스 토큰 — 두 라우터 모두 봉투 미적용, 기존 경로 유지.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
export function withNotifications<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getNotificationHistory(eventType?: string): Promise<NotificationHistoryEntry[]> {
      return this.request(this.withQuery(resolvePath("notifications.history"), { event_type: eventType }));
    }

    async getNotificationPreferences(): Promise<NotificationPreferences> {
      return this.request(resolvePath("notifications.preferences"));
    }

    async updateNotificationPreferences(
      changes: NotificationPreferences,
    ): Promise<PreferenceUpdateResult> {
      return this.put(resolvePath("notifications.preferences"), changes);
    }

    async registerDeviceToken(body: DeviceTokenRegisterRequest): Promise<DeviceTokenRecord> {
      return this.post(resolvePath("deviceTokens.register"), body);
    }

    async deactivateDeviceToken(deviceId: number): Promise<{ deviceId: string; status: string }> {
      return this.del(resolvePath("deviceTokens.deactivate").replace(":deviceId", String(deviceId)));
    }
  };
}
