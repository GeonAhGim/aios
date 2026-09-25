import { afterEach, describe, expect, it, vi } from "vitest";
import { perfBudgetMs } from "../test/perfBudget";
import {
  PushPermissionDeniedError,
  PushSubscriptionFailedError,
  PushUnsupportedError,
  assertPushSupported,
  detectPlatform,
  ensurePermissionGranted,
  isPushSupported,
  requestNotificationPermission,
  subscribeForPush,
  toDeviceTokenRequest,
  urlBase64ToUint8Array,
} from "./push";

const IPHONE_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15";
const ANDROID_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36";
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("detectPlatform: device_tokens VALID_PLATFORMS(iOS/Android)와 1:1", () => {
  it("iPhone UA는 iOS로 판정한다", () => {
    expect(detectPlatform(IPHONE_UA)).toBe("iOS");
  });

  it("Android UA는 Android로 판정한다", () => {
    expect(detectPlatform(ANDROID_UA)).toBe("Android");
  });

  // negative 1: 서버가 거부하는 값을 프론트가 미리 걸러내지 못하면 400
  // VALIDATION_INVALID_FIELD로만 뒤늦게 드러난다 — 데스크톱은 등록을 아예 시도하지 않아야 한다.
  it("negative: 데스크톱 UA는 null(등록 대상 아님)을 반환한다", () => {
    expect(detectPlatform(DESKTOP_UA)).toBeNull();
  });
});

describe("isPushSupported / assertPushSupported", () => {
  it("negative: Notification이 없는 환경(구형 브라우저)에서는 지원하지 않는다고 판정한다", () => {
    vi.stubGlobal("Notification", undefined);
    expect(isPushSupported()).toBe(false);
    expect(() => assertPushSupported()).toThrow(PushUnsupportedError);
  });
});

describe("requestNotificationPermission", () => {
  // negative 2: Notification API 자체가 없는 환경(예: 사설 브라우저)에서 호출하면
  // TypeError로 죽는 대신 우리 자체 에러 타입으로 실패해야 호출부가 분기할 수 있다.
  it("negative: Notification API가 없으면 PushUnsupportedError를 던진다", async () => {
    vi.stubGlobal("Notification", undefined);
    await expect(requestNotificationPermission()).rejects.toThrow(PushUnsupportedError);
  });
});

describe("ensurePermissionGranted", () => {
  // negative 3: 사용자가 권한을 거부하면 구독 시도 자체를 막아야 한다(불필요한
  // pushManager.subscribe 호출·서버 왕복 방지).
  it("negative: denied면 PushPermissionDeniedError를 던진다", () => {
    expect(() => ensurePermissionGranted("denied")).toThrow(PushPermissionDeniedError);
  });

  it("granted면 통과한다", () => {
    expect(() => ensurePermissionGranted("granted")).not.toThrow();
  });
});

describe("subscribeForPush: 실패 주입", () => {
  // 실패 주입 1: 브라우저/네트워크가 pushManager.subscribe를 거부하는 실제 사례
  // (FCM/APNs 연결 불가, AbortError 등)를 모사한다 — 원인이 삼켜지지 않고
  // PushSubscriptionFailedError로 래핑돼 호출부(usePushNotifications)가 사용자에게
  // 보여줄 수 있어야 한다.
  it("pushManager.subscribe가 reject하면 PushSubscriptionFailedError로 래핑한다", async () => {
    const registration = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(null),
        subscribe: vi.fn().mockRejectedValue(new DOMException("no active Service Worker", "AbortError")),
      },
    } as unknown as ServiceWorkerRegistration;

    await expect(subscribeForPush(registration, "AAAA")).rejects.toThrow(PushSubscriptionFailedError);
    await expect(subscribeForPush(registration, "AAAA")).rejects.toThrow(/no active Service Worker/);
  });

  it("이미 구독 중이면 재구독하지 않고 기존 구독을 반환한다", async () => {
    const existingSubscription = { endpoint: "https://push.example/existing" } as PushSubscription;
    const subscribeMock = vi.fn();
    const registration = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(existingSubscription),
        subscribe: subscribeMock,
      },
    } as unknown as ServiceWorkerRegistration;

    const result = await subscribeForPush(registration, "AAAA");

    expect(result).toBe(existingSubscription);
    expect(subscribeMock).not.toHaveBeenCalled();
  });
});

describe("toDeviceTokenRequest", () => {
  it("PushSubscription을 DeviceTokenRegisterRequest로 직렬화한다", () => {
    const subscription = {
      toJSON: () => ({ endpoint: "https://push.example/abc", keys: { p256dh: "k", auth: "a" } }),
    } as unknown as PushSubscription;

    const body = toDeviceTokenRequest(subscription, "Android");

    expect(body.platform).toBe("Android");
    expect(JSON.parse(body.deviceToken)).toEqual({
      endpoint: "https://push.example/abc",
      keys: { p256dh: "k", auth: "a" },
    });
  });
});

// DEPTH_UX 감사 기준(ADR-2026-09-09-C, D2 완료 하한)의 나머지 두 축:
// 수치 성능 단언 + 게이트 적색 재현. urlBase64ToUint8Array는 매 구독 시도마다
// VAPID applicationServerKey를 변환하는 실제 핫패스(push.ts)다.
describe("urlBase64ToUint8Array의 수치 성능(spec 아키텍처 표 apps/web/src/pwa/push.ts)", () => {
  function bigBase64Url(n: number): string {
    // 표준 base64url 알파벳만 사용 -- atob가 실패하지 않도록 4의 배수 길이로 맞춘다.
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let out = "";
    for (let i = 0; i < n; i += 1) out += alphabet[i % alphabet.length];
    return out;
  }

  // 실 배포 VAPID 키(약 87자)보다 훨씬 큰 입력(n=200000)으로 O(n) 상한을 못박는다 —
  // 예산 초과는 이 함수가 charCodeAt 순회 밖에서 배열을 반복 재구성하는 패턴으로
  // 퇴행했다는 신호다.
  it("대량 입력(n=200000)도 50ms 예산 안에서 끝난다", () => {
    const input = bigBase64Url(200000);

    const start = performance.now();
    const result = urlBase64ToUint8Array(input);
    const elapsedMs = performance.now() - start;

    expect(result.length).toBeGreaterThan(0);
    expect(elapsedMs).toBeLessThan(perfBudgetMs(50));
  });

  // 게이트 적색 재현: 실제 구현(charCodeAt을 인덱스에 직접 대입, O(n))과 달리
  // 매 문자를 배열 맨 앞에 끼워 넣는(unshift, 매 호출 O(n)) 회귀 구현을 같은
  // 입력으로 대조한다 -- 실제 구현이 예산 안에 끝나고 회귀 구현은 초과하도록
  // 단언해, urlBase64ToUint8Array가 이 패턴으로 퇴행하면 이 테스트가 즉시
  // 실패로 드러남을 보증한다.
  function o2RegressionUrlBase64ToUint8Array(base64String: string): Uint8Array {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    const output: number[] = [];
    for (let i = 0; i < rawData.length; i += 1) {
      output.unshift(rawData.charCodeAt(i));
    }
    return new Uint8Array(output.reverse());
  }

  it("실제 구현은 예산 안에 끝나지만, O(n^2) 회귀 구현은 같은 입력에서 예산을 초과한다(게이트 적색)", () => {
    const input = bigBase64Url(200000);
    const budget = perfBudgetMs(50);

    const realStart = performance.now();
    urlBase64ToUint8Array(input);
    const realElapsedMs = performance.now() - realStart;

    const regressionStart = performance.now();
    o2RegressionUrlBase64ToUint8Array(input);
    const regressionElapsedMs = performance.now() - regressionStart;

    expect(realElapsedMs).toBeLessThan(budget);
    expect(regressionElapsedMs).toBeGreaterThan(budget);
  });
});
