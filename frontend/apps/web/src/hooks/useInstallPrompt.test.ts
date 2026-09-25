import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useInstallPrompt } from "./useInstallPrompt";

function fireBeforeInstallPrompt(opts: { outcome?: "accepted" | "dismissed" } = {}) {
  const event = new Event("beforeinstallprompt", { cancelable: true }) as Event & {
    prompt: () => Promise<void>;
    userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
  };
  event.prompt = vi.fn(async () => undefined);
  event.userChoice = Promise.resolve({ outcome: opts.outcome ?? "accepted", platform: "web" });
  act(() => {
    window.dispatchEvent(event);
  });
  return event;
}

describe("useInstallPrompt", () => {
  it("beforeinstallprompt를 받기 전에는 설치할 수 없다", () => {
    const { result } = renderHook(() => useInstallPrompt());
    expect(result.current.canInstall).toBe(false);
  });

  it("negative: 설치 가능 상태가 아닐 때 promptInstall을 호출해도 null을 반환하고 예외를 던지지 않는다", async () => {
    const { result } = renderHook(() => useInstallPrompt());
    await expect(result.current.promptInstall()).resolves.toBeNull();
  });

  it("beforeinstallprompt를 받으면 기본 동작을 막고(preventDefault) canInstall이 true가 된다", () => {
    const { result } = renderHook(() => useInstallPrompt());
    const event = fireBeforeInstallPrompt();
    expect(event.defaultPrevented).toBe(true);
    expect(result.current.canInstall).toBe(true);
  });

  it("promptInstall이 수락되면 이벤트의 prompt()를 호출하고 이후 canInstall이 다시 false가 된다", async () => {
    const { result } = renderHook(() => useInstallPrompt());
    const event = fireBeforeInstallPrompt({ outcome: "accepted" });

    let outcome: string | null = null;
    await act(async () => {
      outcome = await result.current.promptInstall();
    });

    expect(event.prompt).toHaveBeenCalledTimes(1);
    expect(outcome).toBe("accepted");
    expect(result.current.canInstall).toBe(false);
  });

  it("negative: 사용자가 거부(dismissed)해도 canInstall이 false로 정리된다(같은 프롬프트 재사용 불가)", async () => {
    const { result } = renderHook(() => useInstallPrompt());
    fireBeforeInstallPrompt({ outcome: "dismissed" });

    let outcome: string | null = null;
    await act(async () => {
      outcome = await result.current.promptInstall();
    });

    expect(outcome).toBe("dismissed");
    expect(result.current.canInstall).toBe(false);
  });

  it("negative: appinstalled 이벤트를 받으면 installed=true, canInstall=false로 고정된다", () => {
    const { result } = renderHook(() => useInstallPrompt());
    fireBeforeInstallPrompt();
    expect(result.current.canInstall).toBe(true);

    act(() => {
      window.dispatchEvent(new Event("appinstalled"));
    });
    expect(result.current.installed).toBe(true);
    expect(result.current.canInstall).toBe(false);
  });
});
