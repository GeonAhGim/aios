import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useCommandPaletteShortcuts } from "./useCommandPaletteShortcuts";

function fireKeyDown(init: KeyboardEventInit, target: EventTarget = document) {
  const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("useCommandPaletteShortcuts: negative", () => {
  it("Ctrl+K는 search 모드로 onOpen을 호출한다", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteShortcuts({ onOpen }));

    fireKeyDown({ key: "k", ctrlKey: true });

    expect(onOpen).toHaveBeenCalledWith("search");
  });

  it("⌘K(metaKey)도 search 모드로 onOpen을 호출한다", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteShortcuts({ onOpen }));

    fireKeyDown({ key: "k", metaKey: true });

    expect(onOpen).toHaveBeenCalledWith("search");
  });

  it("'?'는 help 모드로 onOpen을 호출한다", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteShortcuts({ onOpen }));

    fireKeyDown({ key: "?" });

    expect(onOpen).toHaveBeenCalledWith("help");
  });

  it("input에 포커스가 있을 때는 Ctrl+K/'?' 모두 무시한다(타이핑 방해 금지)", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteShortcuts({ onOpen }));
    const input = document.createElement("input");
    document.body.appendChild(input);

    fireKeyDown({ key: "k", ctrlKey: true }, input);
    fireKeyDown({ key: "?" }, input);

    expect(onOpen).not.toHaveBeenCalled();
  });

  it("enabled=false면 리스너 자체가 등록되지 않는다(다이얼로그가 이미 열려 있을 때)", () => {
    const onOpen = vi.fn();
    renderHook(() => useCommandPaletteShortcuts({ onOpen, enabled: false }));

    fireKeyDown({ key: "k", ctrlKey: true });

    expect(onOpen).not.toHaveBeenCalled();
  });

  it("unmount 후에는 리스너가 남지 않는다(메모리 누수·중복 트리거 방지)", () => {
    const onOpen = vi.fn();
    const { unmount } = renderHook(() => useCommandPaletteShortcuts({ onOpen }));
    unmount();

    fireKeyDown({ key: "k", ctrlKey: true });

    expect(onOpen).not.toHaveBeenCalled();
  });
});
