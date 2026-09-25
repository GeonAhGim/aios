// docs/specs/L4_product_experience_and_discovery_v1.0.md §2.1 아키텍처 표:
// "apps/web/src/pwa/{manifest,serviceWorker,push}.ts — 설치·오프라인 셸·푸시(device_tokens 연동)".
// UX-17 DoD(구독·해지·수신)의 "구독/해지" 절반: 브라우저 Notification/Push API 권한
// 흐름과 device_tokens 등록 페이로드 변환을 담당한다. React/react-query 의존은
// usePushNotifications.ts(shared-hooks)로 분리 — 이 파일은 순수 브라우저 API 래핑만 한다.
import type { DeviceTokenRegisterRequest } from "@aios/shared-types";

export class PushUnsupportedError extends Error {
  constructor() {
    super("이 브라우저는 푸시 알림을 지원하지 않습니다.");
    this.name = "PushUnsupportedError";
  }
}

export class PushPlatformUnsupportedError extends Error {
  constructor() {
    super("iOS/Android 기기에서만 푸시 알림을 등록할 수 있습니다.");
    this.name = "PushPlatformUnsupportedError";
  }
}

export class PushPermissionDeniedError extends Error {
  constructor() {
    super("알림 권한이 거부되었습니다.");
    this.name = "PushPermissionDeniedError";
  }
}

export class PushSubscriptionFailedError extends Error {
  constructor(cause: unknown) {
    super(`푸시 구독에 실패했습니다: ${cause instanceof Error ? cause.message : String(cause)}`);
    this.name = "PushSubscriptionFailedError";
  }
}

/** `Notification`/`serviceWorker`/`PushManager` 중 하나라도 없으면 구독 자체가 불가능하다. */
export function isPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.Notification !== "undefined" &&
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    typeof window.PushManager !== "undefined"
  );
}

export function assertPushSupported(): void {
  if (!isPushSupported()) throw new PushUnsupportedError();
}

// device_token_service.py VALID_PLATFORMS = ("iOS", "Android") — 서버가 이 두 값만
// 받는다(그 외는 400 VALIDATION_INVALID_FIELD). 데스크톱 브라우저 등 그 외 UA는
// null을 반환해 호출부가 등록을 시도하기 전에 막는다.
export function detectPlatform(userAgent: string): "iOS" | "Android" | null {
  if (/iPad|iPhone|iPod/i.test(userAgent)) return "iOS";
  if (/Android/i.test(userAgent)) return "Android";
  return null;
}

export async function requestNotificationPermission(): Promise<NotificationPermission> {
  if (typeof window === "undefined" || typeof window.Notification === "undefined") {
    throw new PushUnsupportedError();
  }
  return Notification.requestPermission();
}

export function ensurePermissionGranted(permission: NotificationPermission): void {
  if (permission !== "granted") throw new PushPermissionDeniedError();
}

// VAPID applicationServerKey는 base64url 문자열로 배포되지만 PushManager.subscribe는
// Uint8Array를 요구한다 — MDN 권장 변환. atob 결과를 한 번만 순회하는 O(n) 구현이어야
// 한다(회귀 시 게이트 적색: push.test.ts의 O(n^2) 대조 참고).
export function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export async function subscribeForPush(
  registration: ServiceWorkerRegistration,
  vapidPublicKey: string,
): Promise<PushSubscription> {
  try {
    const existing = await registration.pushManager.getSubscription();
    if (existing) return existing;
    return await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapidPublicKey) as BufferSource,
    });
  } catch (err) {
    throw new PushSubscriptionFailedError(err);
  }
}

export async function unsubscribeFromPush(registration: ServiceWorkerRegistration): Promise<void> {
  const existing = await registration.pushManager.getSubscription();
  if (existing) await existing.unsubscribe();
}

export function toDeviceTokenRequest(
  subscription: PushSubscription,
  platform: "iOS" | "Android",
): DeviceTokenRegisterRequest {
  return {
    deviceToken: JSON.stringify(subscription.toJSON()),
    platform,
  };
}
