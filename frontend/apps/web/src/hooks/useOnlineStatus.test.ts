import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useOnlineStatus } from "./useOnlineStatus";

function setNavigatorOnLine(value: boolean) {
  Object.defineProperty(navigator, "onLine", { value, configurable: true });
}

afterEach(() => {
  setNavigatorOnLine(true);
});

describe("useOnlineStatus", () => {
  it("초기값은 navigator.onLine을 따른다", () => {
    setNavigatorOnLine(false);
    const { result } = renderHook(() => useOnlineStatus());
    expect(result.current).toBe(false);
  });

  it("negative: offline 이벤트를 받으면 false로 전환된다", () => {
    setNavigatorOnLine(true);
    const { result } = renderHook(() => useOnlineStatus());
    expect(result.current).toBe(true);

    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(result.current).toBe(false);
  });

  it("negative: online 이벤트를 받으면 다시 true로 전환된다", () => {
    setNavigatorOnLine(false);
    const { result } = renderHook(() => useOnlineStatus());
    expect(result.current).toBe(false);

    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(result.current).toBe(true);
  });

  it("negative: 언마운트 후에는 online/offline 이벤트가 더 이상 상태를 바꾸지 않는다(리스너 해제)", () => {
    const { result, unmount } = renderHook(() => useOnlineStatus());
    expect(result.current).toBe(true);

    unmount();
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    // 언마운트된 훅의 result.current는 마지막 렌더 값을 유지한다(true) — 리스너가
    // 해제되지 않았다면 콘솔 경고나 이후 렌더에서의 상태 오염으로 드러난다.
    expect(result.current).toBe(true);
  });
});
