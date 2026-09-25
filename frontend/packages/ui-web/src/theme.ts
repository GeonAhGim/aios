import { useCallback, useSyncExternalStore } from "react";

export type ThemeMode = "light" | "dark";

const STORAGE_KEY = "aios-theme";
const DEFAULT_THEME: ThemeMode = "dark";
const VALID_MODES: ReadonlySet<string> = new Set<ThemeMode>(["light", "dark"]);

export function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === "string" && VALID_MODES.has(value);
}

/**
 * 저장된 값이 없거나 손상됐으면(다른 타입·오타·구버전 값) 앱 기본값인 dark로
 * 되돌아간다 — 시스템 라이트 선호는 의도적으로 무시한다: 이 앱은 트레이딩
 * 터미널로 다크가 1급 시민이고(index.css 상단 주석), 라이트는 사용자가
 * 명시적으로 고르는 옵션이다.
 */
export function resolveInitialTheme(stored: string | null): ThemeMode {
  return isThemeMode(stored) ? stored : DEFAULT_THEME;
}

export interface ThemeStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface ThemeRoot {
  setAttribute(qualifiedName: string, value: string): void;
}

export interface ThemeStoreOptions {
  storage?: ThemeStorage | null;
  root?: ThemeRoot | null;
}

export function applyThemeAttribute(root: ThemeRoot, theme: ThemeMode): void {
  root.setAttribute("data-theme", theme);
}

export interface ThemeStore {
  getSnapshot(): ThemeMode;
  subscribe(listener: () => void): () => void;
  setTheme(next: ThemeMode): void;
}

/**
 * DI로 storage/root를 받는다(기본값은 실제 브라우저 전역) — 순수 함수만으로는
 * 표현할 수 없는 "구독자에게 통지 + localStorage 영속 + DOM 속성 반영" 3가지
 * 부수효과를 노드 환경(ui-web의 vitest 기본 environment, jsdom 없음)에서도
 * 가짜 storage/root 객체로 완전히 검증하기 위함이다.
 */
export function createThemeStore(options: ThemeStoreOptions = {}): ThemeStore {
  const storage =
    options.storage !== undefined
      ? options.storage
      : typeof window === "undefined"
        ? null
        : window.localStorage;
  const root =
    options.root !== undefined
      ? options.root
      : typeof document === "undefined"
        ? null
        : document.documentElement;

  let theme: ThemeMode = DEFAULT_THEME;
  let hydrated = false;
  const listeners = new Set<() => void>();

  function readStored(): string | null {
    if (!storage) return null;
    try {
      return storage.getItem(STORAGE_KEY);
    } catch {
      return null; // 프라이빗 모드 등 storage 접근 차단 -- dark 폴백으로 계속 진행
    }
  }

  function hydrate(): void {
    if (hydrated) return;
    hydrated = true;
    theme = resolveInitialTheme(readStored());
    if (root) applyThemeAttribute(root, theme);
  }

  function getSnapshot(): ThemeMode {
    hydrate();
    return theme;
  }

  function subscribe(listener: () => void): () => void {
    hydrate();
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function setTheme(next: ThemeMode): void {
    hydrate();
    if (next === theme) return;
    theme = next;
    if (root) applyThemeAttribute(root, theme);
    if (storage) {
      try {
        storage.setItem(STORAGE_KEY, theme);
      } catch {
        // 세션 상태는 이미 반영됐다 -- 영속 실패는 조용히 무시(다음 로드는 dark로)
      }
    }
    for (const listener of listeners) listener();
  }

  return { getSnapshot, subscribe, setTheme };
}

export const themeStore: ThemeStore = createThemeStore();

export interface UseThemeResult {
  theme: ThemeMode;
  setTheme: (next: ThemeMode) => void;
  toggleTheme: () => void;
}

export function useTheme(): UseThemeResult {
  const theme = useSyncExternalStore(themeStore.subscribe, themeStore.getSnapshot, () => DEFAULT_THEME);
  const toggleTheme = useCallback(() => {
    themeStore.setTheme(theme === "dark" ? "light" : "dark");
  }, [theme]);
  return { theme, setTheme: themeStore.setTheme, toggleTheme };
}
