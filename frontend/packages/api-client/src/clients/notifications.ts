import type {
  DeviceTokenRecord,
  DeviceTokenRegisterRequest,
  NotificationHistoryEntry,
  NotificationPreferences,
  PreferenceUpdateResult,
} from "@aios/shared-types";
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-17 알림 / FD-21 디바이스 토큰 — 두 라우터 모두 봉투 미적용, 기존 경로 유지.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
// task-1160: getNotificationPreferences(치환·쿼리 없는 단순 조회)는 requestByRoute로,
// getNotificationHistory(쿼리 1건)는 resolvePath로 경로를 만들고 resolveEnvelope(route)로
// request/requestEnvelope 분기만 apiPaths.ts 레지스트리 단일 출처로 이관했다
// (admin.ts task-1159 선례와 동일 관용) — 분기 결과 자체는 바꾸지 않는다.
export function withNotifications<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getNotificationHistory(eventType?: string): Promise<NotificationHistoryEntry[]> {
      const path = this.withQuery(resolvePath("notifications.history"), { event_type: eventType });
      return resolveEnvelope("notifications.history") ? this.requestEnvelope(path) : this.request(path);
    }

    async getNotificationPreferences(): Promise<NotificationPreferences> {
      return this.requestByRoute("notifications.preferences");
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
