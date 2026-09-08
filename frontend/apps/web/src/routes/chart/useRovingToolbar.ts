import type { KeyboardEvent } from "react";
import { useRef, useState } from "react";

export interface ButtonSpec {
  readonly id: string;
  readonly disabled: boolean;
}

/** WAI-ARIA toolbar 패턴: 그룹 내 버튼은 하나만 tabIndex=0(roving), 화살표로 이동한다. */
export function useRovingToolbar(buttons: readonly ButtonSpec[]) {
  const enabledIds = buttons.filter((b) => !b.disabled).map((b) => b.id);
  const [activeId, setActiveId] = useState<string>(enabledIds[0] ?? "");
  const groupRef = useRef<HTMLDivElement>(null);

  function tabIndexFor(id: string): 0 | -1 {
    if (!enabledIds.includes(id)) return -1;
    const active = enabledIds.includes(activeId) ? activeId : enabledIds[0];
    return id === active ? 0 : -1;
  }

  function onFocusButton(id: string): void {
    if (enabledIds.includes(id)) setActiveId(id);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const container = groupRef.current;
    if (!container) return;
    const focusable = Array.from(container.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
    if (focusable.length === 0) return;
    const current = focusable.indexOf(document.activeElement as HTMLButtonElement);
    let next = current;
    if (event.key === "ArrowRight") next = (current + 1 + focusable.length) % focusable.length;
    else if (event.key === "ArrowLeft") next = (current - 1 + focusable.length) % focusable.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = focusable.length - 1;
    else return;
    event.preventDefault();
    focusable[next]?.focus();
  }

  return { groupRef, tabIndexFor, onFocusButton, onKeyDown };
}
