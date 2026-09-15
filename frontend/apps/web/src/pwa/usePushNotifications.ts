import { apiClient } from "@aios/shared-hooks";
import { useMutation } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import {
  PushPlatformUnsupportedError,
  assertPushSupported,
  detectPlatform,
  ensurePermissionGranted,
  isPushSupported,
  requestNotificationPermission,
  subscribeForPush,
  toDeviceTokenRequest,
  unsubscribeFromPush,
} from "./push";

export type PushStatus = "unsupported" | "idle" | "denied" | "subscribed" | "error";

const SERVICE_WORKER_URL = "/sw.js";

// UX-17 DoD(구독·해지·수신) 중 구독/해지 흐름 — push.ts(순수 브라우저 API 래핑)를
// device_tokens 등록/해지 API(useAccountSettings.ts의 useNotification* 자매 함수)에
// 이어붙인다. "수신"은 public/sw.js의 push 이벤트 리스너(서비스워커 스코프, React 밖)가
// 담당한다. UX-16(task-2700)이 이미 "/sw.js"를 스코프 "/"에 등록해 오프라인 셸을
// 서빙 중이므로, 여기서도 같은 스크립트를 등록한다 — 같은 스코프에 다른 스크립트를
// 등록하면 나중 등록이 이전 것을 교체해(register()는 스코프당 활성 워커 1개) 오프라인
// 셸의 fetch 핸들러가 사라진다. register()는 동일 스크립트+스코프에 대해 멱등이라
// 여기서 다시 불러도 중복 설치가 일어나지 않는다. push.ts가 spec 아키텍처 표
// (apps/web/src/pwa/*)에 묶여 있어 packages/shared-hooks가 아닌 여기(apps/web)에
// 둔다 — shared-hooks는 apps/web을 참조하지 않는 방향으로만 의존해야 한다.
export function usePushNotifications() {
  const [status, setStatus] = useState<PushStatus>(() => (isPushSupported() ? "idle" : "unsupported"));
  const [error, setError] = useState<Error | null>(null);
  const [deviceId, setDeviceId] = useState<number | null>(null);

  const register = useMutation({
    mutationFn: (body: Parameters<typeof apiClient.registerDeviceToken>[0]) =>
      apiClient.registerDeviceToken(body),
  });
  const deactivate = useMutation({
    mutationFn: (id: number) => apiClient.deactivateDeviceToken(id),
  });

  const enable = useCallback(
    async (vapidPublicKey: string) => {
      setError(null);
      try {
        assertPushSupported();
        const permission = await requestNotificationPermission();
        if (permission !== "granted") {
          setStatus("denied");
          ensurePermissionGranted(permission);
        }
        const platform = detectPlatform(navigator.userAgent);
        if (!platform) throw new PushPlatformUnsupportedError();

        const registration = await navigator.serviceWorker.register(SERVICE_WORKER_URL);
        const subscription = await subscribeForPush(registration, vapidPublicKey);
        const record = await register.mutateAsync(toDeviceTokenRequest(subscription, platform));

        setDeviceId(record.deviceId);
        setStatus("subscribed");
      } catch (err) {
        const asError = err instanceof Error ? err : new Error(String(err));
        setStatus((prev) => (prev === "denied" ? prev : "error"));
        setError(asError);
        throw asError;
      }
    },
    [register],
  );

  const disable = useCallback(async () => {
    if (deviceId == null) return;
    setError(null);
    try {
      await deactivate.mutateAsync(deviceId);
      try {
        const registration = await navigator.serviceWorker.getRegistration(SERVICE_WORKER_URL);
        if (registration) await unsubscribeFromPush(registration);
      } catch {
        // 브라우저 측 구독 해제 실패는 device_tokens 해지(위에서 이미 성공)를
        // 되돌리지 않는다 — 서버가 더 이상 이 토큰으로 발송하지 않는 것이 본질이다.
      }
      setDeviceId(null);
      setStatus("idle");
    } catch (err) {
      const asError = err instanceof Error ? err : new Error(String(err));
      setError(asError);
      throw asError;
    }
  }, [deviceId, deactivate]);

  return {
    status,
    error,
    deviceId,
    enable,
    disable,
    isEnabling: register.isPending,
    isDisabling: deactivate.isPending,
  };
}
