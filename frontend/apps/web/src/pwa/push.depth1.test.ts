import { afterEach, describe, expect, it, vi } from "vitest";
import { perfBudgetMs } from "../test/perfBudget";
import {
  PushPermissionDeniedError,
  PushPlatformUnsupportedError,
  PushSubscriptionFailedError,
  PushUnsupportedError,
  assertPushSupported,
  detectPlatform,
  ensurePermissionGranted,
  requestNotificationPermission,
  subscribeForPush,
  toDeviceTokenRequest,
  unsubscribeFromPush,
  urlBase64ToUint8Array,
} from "./push";

afterEach(() => {
  vi.unstubAllGlobals();
});

function fakeRegistration(overrides: Record<string, unknown>): ServiceWorkerRegistration {
  return { pushManager: overrides } as unknown as ServiceWorkerRegistration;
}

describe("push depth1: negative", () => {
  it("negative: 지원 API가 없으면 assertPushSupported가 toThrow한다", () => {
    vi.stubGlobal("Notification", undefined);
    expect(() => assertPushSupported()).toThrow(PushUnsupportedError);
  });

  it("negative: 거부/기본 권한은 ensurePermissionGranted가 toThrow한다", () => {
    expect(() => ensurePermissionGranted("denied")).toThrow(PushPermissionDeniedError);
    expect(() => ensurePermissionGranted("default")).toThrow(PushPermissionDeniedError);
  });

  it("negative: Notification이 없으면 권한 요청이 rejects한다", async () => {
    vi.stubGlobal("Notification", undefined);
    await expect(requestNotificationPermission()).rejects.toThrow(PushUnsupportedError);
  });

  it("negative: 데스크톱 UA는 등록 대상이 아니다", () => {
    expect(detectPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBeNull();
    expect(new PushPlatformUnsupportedError().name).toBe("PushPlatformUnsupportedError");
  });
});

describe("push depth1: failure injection", () => {
  it("failure injection: subscribe가 mockRejectedValue로 실패하면 PushSubscriptionFailedError로 감싼다", async () => {
    const reg = fakeRegistration({
      getSubscription: vi.fn().mockResolvedValue(null),
      subscribe: vi.fn().mockRejectedValue(new Error("networkError")),
    });
    const promise = subscribeForPush(reg, "AQAB");
    await expect(promise).rejects.toThrow(PushSubscriptionFailedError);
    await expect(promise).rejects.toThrow(/networkError/);
  });

  it("failure injection: getSubscription 실패도 구독 실패로 전파된다", async () => {
    const reg = fakeRegistration({
      getSubscription: vi.fn().mockRejectedValue(new Error("boom")),
    });
    await expect(subscribeForPush(reg, "AQAB")).rejects.toThrow(PushSubscriptionFailedError);
  });

  it("failure injection: unsubscribe 실패는 그대로 rejects한다", async () => {
    const reg = fakeRegistration({
      getSubscription: vi.fn().mockResolvedValue({
        unsubscribe: vi.fn().mockRejectedValue(new Error("networkError")),
      }),
    });
    await expect(unsubscribeFromPush(reg)).rejects.toThrow("networkError");
  });
});

describe("push depth1: performance", () => {
  // 로컬 실측: 변환 n=20000 약 1ms 미만, 토큰 직렬화 1000회 약 2ms. 예산 50ms는
  // 실측의 30배 이상 여유이며 O(n^2) 회귀(수백 ms 이상)만 잡는다.
  const BASE_MS = 50;
  const big = "A".repeat(20000);

  function slowUnshift(input: string): number[] {
    const raw = atob(input);
    const out: number[] = [];
    for (let i = 0; i < raw.length; i += 1) out.unshift(raw.charCodeAt(i));
    return out;
  }

  it("urlBase64ToUint8Array(n=20000)는 예산 안에 끝난다", () => {
    const start = performance.now();
    const out = urlBase64ToUint8Array(big);
    const elapsed = performance.now() - start;
    expect(out.length).toBe(15000);
    expect(elapsed).toBeLessThan(perfBudgetMs(BASE_MS));
  });

  it("toDeviceTokenRequest 1000회 직렬화는 예산 안에 끝난다", () => {
    const sub = { toJSON: () => ({ endpoint: "https://x", keys: { p256dh: "a", auth: "b" } }) };
    const start = performance.now();
    for (let i = 0; i < 1000; i += 1) toDeviceTokenRequest(sub as unknown as PushSubscription, "iOS");
    const elapsed = performance.now() - start;
    expect(elapsed).toBeLessThan(perfBudgetMs(BASE_MS));
  });

  it("gate red: 입력을 키운 O(n^2) 회귀 구현은 같은 예산을 초과해 단언이 실패한다", () => {
    const budget = perfBudgetMs(BASE_MS);
    const start = performance.now();
    slowUnshift("A".repeat(400000));
    const regressed = performance.now() - start;
    expect(regressed).toBeGreaterThan(budget);
    expect(() => expect(regressed).toBeLessThan(budget)).toThrow();
  });
});
