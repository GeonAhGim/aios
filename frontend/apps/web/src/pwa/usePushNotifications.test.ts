import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePushNotifications } from "./usePushNotifications";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return createElement(QueryClientProvider, { client: qc }, children);
}

const registerDeviceToken = vi.fn();
const deactivateDeviceToken = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  apiClient: {
    registerDeviceToken: (...args: unknown[]) => registerDeviceToken(...args),
    deactivateDeviceToken: (...args: unknown[]) => deactivateDeviceToken(...args),
  },
}));

const IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15";

function stubSupportedBrowser(options: {
  permission?: NotificationPermission;
  subscribeImpl?: () => Promise<unknown>;
} = {}) {
  const permission = options.permission ?? "granted";
  vi.stubGlobal("Notification", { requestPermission: vi.fn().mockResolvedValue(permission) });
  vi.stubGlobal("PushManager", function PushManager() {});
  vi.stubGlobal("navigator", {
    userAgent: IPHONE_UA,
    serviceWorker: {
      register: vi.fn().mockResolvedValue({
        pushManager: {
          getSubscription: vi.fn().mockResolvedValue(null),
          subscribe:
            options.subscribeImpl ??
            vi.fn().mockResolvedValue({
              toJSON: () => ({ endpoint: "https://push.example/abc", keys: { p256dh: "k", auth: "a" } }),
            }),
        },
      }),
      getRegistration: vi.fn().mockResolvedValue(null),
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("usePushNotifications: 지원 여부", () => {
  it("Notification/PushManager/serviceWorker가 없으면 unsupported로 시작한다", () => {
    vi.stubGlobal("Notification", undefined);
    const { result } = renderHook(() => usePushNotifications(), { wrapper });
    expect(result.current.status).toBe("unsupported");
  });
});

describe("usePushNotifications.enable", () => {
  it("정상 흐름: 권한 허용 -> 구독 -> device_tokens 등록까지 마치면 subscribed가 된다", async () => {
    stubSupportedBrowser();
    registerDeviceToken.mockResolvedValue({ deviceId: 42, registeredAt: "2026-09-16T00:00:00Z", isActive: true });

    const { result } = renderHook(() => usePushNotifications(), { wrapper });
    await act(async () => {
      await result.current.enable("AAAA");
    });

    await waitFor(() => expect(result.current.status).toBe("subscribed"));
    expect(result.current.deviceId).toBe(42);
    expect(registerDeviceToken).toHaveBeenCalledWith(
      expect.objectContaining({ platform: "iOS" }),
    );
  });

  // negative: 권한이 거부되면 device_tokens 등록을 아예 시도하지 않는다(불필요한
  // 서버 왕복 방지) — 상태도 error가 아니라 denied로 구분돼야 UI가 "설정에서 허용"
  // 안내를 보여줄 수 있다.
  it("negative: 권한 거부 시 denied 상태가 되고 registerDeviceToken을 호출하지 않는다", async () => {
    stubSupportedBrowser({ permission: "denied" });

    const { result } = renderHook(() => usePushNotifications(), { wrapper });
    await act(async () => {
      await expect(result.current.enable("AAAA")).rejects.toThrow();
    });

    expect(result.current.status).toBe("denied");
    expect(registerDeviceToken).not.toHaveBeenCalled();
  });

  // 실패 주입: device_tokens 등록 API가 500으로 실패하는 경우(백엔드 장애·네트워크
  // 단절)를 모사한다 — 에러가 조용히 삼켜지지 않고 error 상태로 드러나며, 구독
  // 성공을 의미하는 deviceId도 세팅되지 않아야 한다(반쪽짜리 성공 상태 방지).
  it("negative(실패 주입): registerDeviceToken이 실패하면 error 상태이고 deviceId는 세팅되지 않는다", async () => {
    stubSupportedBrowser();
    registerDeviceToken.mockRejectedValue(new Error("500 INTERNAL_ERROR"));

    const { result } = renderHook(() => usePushNotifications(), { wrapper });
    await act(async () => {
      await expect(result.current.enable("AAAA")).rejects.toThrow("500 INTERNAL_ERROR");
    });

    expect(result.current.status).toBe("error");
    expect(result.current.deviceId).toBeNull();
    expect(result.current.error?.message).toBe("500 INTERNAL_ERROR");
  });

  // negative: 지원하지 않는 플랫폼(데스크톱 등)에서는 권한 요청 이후에도 등록을
  // 시도하지 않는다 — 서버 VALID_PLATFORMS(iOS/Android) 400 왕복을 막는다.
  it("negative: 데스크톱 UA에서는 registerDeviceToken을 호출하지 않고 error 상태가 된다", async () => {
    stubSupportedBrowser();
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      serviceWorker: {
        register: vi.fn(),
        getRegistration: vi.fn().mockResolvedValue(null),
      },
    });

    const { result } = renderHook(() => usePushNotifications(), { wrapper });
    await act(async () => {
      await expect(result.current.enable("AAAA")).rejects.toThrow();
    });

    expect(result.current.status).toBe("error");
    expect(registerDeviceToken).not.toHaveBeenCalled();
  });
});

describe("usePushNotifications.disable", () => {
  // negative(실패 주입): 해지 API가 실패하면 로컬 상태를 낙관적으로 idle로
  // 되돌리지 않는다 — 서버는 여전히 이 디바이스로 발송 중인데 UI만 "꺼짐"으로
  // 보이면 사용자가 실제로 계속 알림을 받고도 설정을 신뢰하지 못하게 된다.
  it("negative(실패 주입): deactivateDeviceToken이 실패하면 deviceId를 유지한다(낙관적 해제 금지)", async () => {
    stubSupportedBrowser();
    registerDeviceToken.mockResolvedValue({ deviceId: 7, registeredAt: "2026-09-16T00:00:00Z", isActive: true });
    deactivateDeviceToken.mockRejectedValue(new Error("503 UNAVAILABLE"));

    const { result } = renderHook(() => usePushNotifications(), { wrapper });
    await act(async () => {
      await result.current.enable("AAAA");
    });
    await waitFor(() => expect(result.current.status).toBe("subscribed"));

    await act(async () => {
      await expect(result.current.disable()).rejects.toThrow("503 UNAVAILABLE");
    });

    expect(result.current.deviceId).toBe(7);
    expect(result.current.status).toBe("subscribed");
    expect(result.current.error?.message).toBe("503 UNAVAILABLE");
  });

  it("정상 흐름: 해지에 성공하면 idle로 돌아가고 deviceId가 지워진다", async () => {
    stubSupportedBrowser();
    registerDeviceToken.mockResolvedValue({ deviceId: 7, registeredAt: "2026-09-16T00:00:00Z", isActive: true });
    deactivateDeviceToken.mockResolvedValue({ deviceId: "7", status: "deactivated" });

    const { result } = renderHook(() => usePushNotifications(), { wrapper });
    await act(async () => {
      await result.current.enable("AAAA");
    });
    await waitFor(() => expect(result.current.status).toBe("subscribed"));

    await act(async () => {
      await result.current.disable();
    });

    expect(result.current.status).toBe("idle");
    expect(result.current.deviceId).toBeNull();
  });
});
