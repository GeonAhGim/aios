import { act, renderHook } from "@testing-library/react";
import type { KeyboardEvent } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRovingToolbar, type ButtonSpec } from "./useRovingToolbar";

// WAI-ARIA roving-tabindex 툴바 패턴: 그룹 내 정확히 하나의 버튼만 tabIndex=0이고,
// 화살표/Home/End로 실제 DOM 포커스를 이동시킨다. `groupRef`는 실 DOM 컨테이너를
// 요구하므로, jsdom에 버튼들을 직접 붙이고 그 컨테이너를 훅의 ref에 연결한다.

function mountButtons(ids: readonly string[]): { container: HTMLDivElement; buttons: HTMLButtonElement[] } {
  const container = document.createElement("div");
  const buttons = ids.map((id) => {
    const button = document.createElement("button");
    button.id = id;
    container.appendChild(button);
    return button;
  });
  document.body.appendChild(container);
  return { container, buttons };
}

function keyEvent(key: string): KeyboardEvent<HTMLDivElement> {
  return { key, preventDefault: vi.fn() } as unknown as KeyboardEvent<HTMLDivElement>;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("useRovingToolbar — 초기 activeId/tabIndexFor", () => {
  it("첫 번째 활성화된 버튼이 초기 activeId가 되어 tabIndex 0을 받는다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }, { id: "b", disabled: false }];
    const { result } = renderHook(() => useRovingToolbar(buttons));

    expect(result.current.tabIndexFor("a")).toBe(0);
    expect(result.current.tabIndexFor("b")).toBe(-1);
  });

  it("첫 버튼이 disabled면 처음으로 활성화된 버튼이 초기 activeId가 된다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: true }, { id: "b", disabled: false }];
    const { result } = renderHook(() => useRovingToolbar(buttons));

    expect(result.current.tabIndexFor("b")).toBe(0);
    expect(result.current.tabIndexFor("a")).toBe(-1);
  });

  it("disabled 버튼은 activeId가 무엇이든 항상 tabIndex -1이다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }, { id: "b", disabled: true }];
    const { result } = renderHook(() => useRovingToolbar(buttons));

    expect(result.current.tabIndexFor("b")).toBe(-1);
  });
});

describe("useRovingToolbar — onFocusButton", () => {
  it("활성화된 버튼으로 포커스가 옮겨오면 그 버튼이 새 activeId가 된다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }, { id: "b", disabled: false }];
    const { result } = renderHook(() => useRovingToolbar(buttons));

    act(() => result.current.onFocusButton("b"));

    expect(result.current.tabIndexFor("b")).toBe(0);
    expect(result.current.tabIndexFor("a")).toBe(-1);
  });

  it("disabled 버튼으로의 포커스 이벤트는 activeId를 바꾸지 않는다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }, { id: "b", disabled: true }];
    const { result } = renderHook(() => useRovingToolbar(buttons));

    act(() => result.current.onFocusButton("b"));

    expect(result.current.tabIndexFor("a")).toBe(0);
  });
});

describe("useRovingToolbar — onKeyDown 화살표/Home/End 네비게이션", () => {
  it("ArrowRight는 다음 버튼으로, 마지막에서는 첫 버튼으로 순환하며 실제 focus()를 호출한다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }, { id: "b", disabled: false }, { id: "c", disabled: false }];
    const { result } = renderHook(() => useRovingToolbar(buttons));
    const { container, buttons: els } = mountButtons(["a", "b", "c"]);
    result.current.groupRef.current = container;
    els[0]!.focus();

    result.current.onKeyDown(keyEvent("ArrowRight"));
    expect(document.activeElement).toBe(els[1]);

    els[2]!.focus();
    result.current.onKeyDown(keyEvent("ArrowRight"));
    expect(document.activeElement).toBe(els[0]);
  });

  it("ArrowLeft는 이전 버튼으로, 첫 버튼에서는 마지막 버튼으로 순환한다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }, { id: "b", disabled: false }, { id: "c", disabled: false }];
    const { result } = renderHook(() => useRovingToolbar(buttons));
    const { container, buttons: els } = mountButtons(["a", "b", "c"]);
    result.current.groupRef.current = container;
    els[0]!.focus();

    result.current.onKeyDown(keyEvent("ArrowLeft"));
    expect(document.activeElement).toBe(els[2]);
  });

  it("Home은 첫 버튼, End는 마지막 버튼으로 이동한다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }, { id: "b", disabled: false }, { id: "c", disabled: false }];
    const { result } = renderHook(() => useRovingToolbar(buttons));
    const { container, buttons: els } = mountButtons(["a", "b", "c"]);
    result.current.groupRef.current = container;
    els[1]!.focus();

    result.current.onKeyDown(keyEvent("End"));
    expect(document.activeElement).toBe(els[2]);

    result.current.onKeyDown(keyEvent("Home"));
    expect(document.activeElement).toBe(els[0]);
  });

  it("disabled 버튼은 querySelectorAll(':not(:disabled)')에서 제외되어 이동 대상이 아니다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }, { id: "b", disabled: false }, { id: "c", disabled: false }];
    const { result } = renderHook(() => useRovingToolbar(buttons));
    const { container, buttons: els } = mountButtons(["a", "b", "c"]);
    els[1]!.disabled = true;
    result.current.groupRef.current = container;
    els[0]!.focus();

    result.current.onKeyDown(keyEvent("ArrowRight"));
    expect(document.activeElement).toBe(els[2]);
  });

  it("관련 없는 키는 preventDefault를 호출하지 않고 포커스도 바꾸지 않는다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }, { id: "b", disabled: false }];
    const { result } = renderHook(() => useRovingToolbar(buttons));
    const { container, buttons: els } = mountButtons(["a", "b"]);
    result.current.groupRef.current = container;
    els[0]!.focus();

    const event = keyEvent("Tab");
    result.current.onKeyDown(event);

    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(els[0]);
  });

  it("groupRef.current가 아직 없으면(마운트 전) 조용히 아무 일도 하지 않는다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: false }];
    const { result } = renderHook(() => useRovingToolbar(buttons));

    expect(() => result.current.onKeyDown(keyEvent("ArrowRight"))).not.toThrow();
  });

  it("포커스 가능한 버튼이 하나도 없으면(전부 disabled) 조용히 아무 일도 하지 않는다", () => {
    const buttons: ButtonSpec[] = [{ id: "a", disabled: true }];
    const { result } = renderHook(() => useRovingToolbar(buttons));
    const { container } = mountButtons(["a"]);
    container.querySelector("button")!.disabled = true;
    result.current.groupRef.current = container;

    expect(() => result.current.onKeyDown(keyEvent("ArrowRight"))).not.toThrow();
  });
});
