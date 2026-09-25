// i18next.d.ts가 t()의 키 인자를 catalog.ko 구조의 리터럴 유니온으로만 받게 강제하므로
// (task-2685), descriptionKey를 문자열 dot-path로 두면 동적 조회가 타입 에러가 된다.
// 그래서 짧은 id만 여기 두고, CommandPalette.tsx가 리터럴 t() 호출을 담은
// Record<ShortcutId, string>로 옮겨 찾는다.
export type ShortcutId = "openSearch" | "openHelp" | "navigate" | "select" | "close";

export interface ShortcutEntry {
  readonly keys: string;
  readonly id: ShortcutId;
}

// 단축키 맵(UX-19) -- 값 자체는 여기, 문구는 catalog.ko/en의 commandPalette.shortcut.*에.
export const SHORTCUT_ENTRIES: readonly ShortcutEntry[] = [
  { keys: "Ctrl/⌘ K", id: "openSearch" },
  { keys: "?", id: "openHelp" },
  { keys: "↑ / ↓", id: "navigate" },
  { keys: "Enter", id: "select" },
  { keys: "Esc", id: "close" },
];
