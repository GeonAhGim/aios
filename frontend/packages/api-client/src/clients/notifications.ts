import type {
  DeviceTokenRecord,
  DeviceTokenRegisterRequest,
  NotificationHistoryEntry,
  NotificationPreferences,
  PreferenceUpdateResult,
} from "@aios/shared-types";
import type { AnyConstructor } from "../http";

// FD-17 알림 / FD-21 디바이스 토큰 — 두 라우터 모두 봉투 미적용, 기존 경로 유지.
export function withNotifications<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getNotificationHistory(eventType?: string): Promise<NotificationHistoryEntry[]> {
      return this.request(this.withQuery("/notifications/history", { event_type: eventType }));
    }

    async getNotificationPreferences(): Promise<NotificationPreferences> {
      return this.request("/notifications/preferences");
    }

    async updateNotificationPreferences(
      changes: NotificationPreferences,
    ): Promise<PreferenceUpdateResult> {
      return this.put("/notifications/preferences", changes);
    }

    async registerDeviceToken(body: DeviceTokenRegisterRequest): Promise<DeviceTokenRecord> {
      return this.post("/device-tokens", body);
    }

    async deactivateDeviceToken(deviceId: number): Promise<{ deviceId: string; status: string }> {
      return this.del(`/device-tokens/${deviceId}`);
    }
  };
}
