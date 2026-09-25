import { useEffect } from "react";

export type PaletteMode = "search" | "help";

export interface UseCommandPaletteShortcutsOptions {
  onOpen: (mode: PaletteMode) => void;
  /** 팔레트가 이미 열려 있을 때는 false로 넘겨 리스너를 끈다 -- 그래야 검색창에
   * "k"나 "?"를 타이핑해도 전역 단축키가 가로채지 않는다(다이얼로그 자체 키보드
   * 처리는 CommandPalette.tsx의 onKeyDown이 맡는다). */
  enabled?: boolean;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable;
}

/** Ctrl/⌘+K로 검색을, "?"로 단축키 도움말을 여는 전역 키보드 리스너(UX-19).
 * input/textarea/contentEditable에 포커스가 있을 때는 무시한다 -- 그래야 다른
 * 화면의 텍스트 입력 중 "k"/"?"를 치는 사용자를 방해하지 않는다. */
export function useCommandPaletteShortcuts({ onOpen, enabled = true }: UseCommandPaletteShortcutsOptions): void {
  useEffect(() => {
    if (!enabled) return undefined;

    function handleKeyDown(event: KeyboardEvent) {
      if (isEditableTarget(event.target)) return;

      const isModK = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k";
      if (isModK) {
        event.preventDefault();
        onOpen("search");
        return;
      }

      if (event.key === "?" && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        onOpen("help");
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onOpen, enabled]);
}
