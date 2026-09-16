import { cn, useDialogFocusTrap } from "@aios/ui-web";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { ADMIN_NAV_ITEMS, MAIN_NAV_ITEMS, SETTINGS_NAV_ITEMS } from "../components/layout/navItems";
import { buildNavCommands } from "./commands";
import { matchCommands, type Command } from "./matchCommands";
import { SHORTCUT_ENTRIES, type ShortcutId } from "./shortcuts";
import { useCommandPaletteShortcuts, type PaletteMode } from "./useCommandPaletteShortcuts";

const INPUT_CLASS =
  "w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg placeholder:text-fg-muted outline-none transition-colors focus:border-accent focus:ring-1 focus:ring-accent";

const TITLE_ID = "command-palette-title";

export interface CommandPaletteProps {
  /** ADMIN_NAV_ITEMS를 명령 목록에 포함할지 -- AppShell과 동일하게 me.isPlatformAdmin으로 게이팅한다. */
  isAdmin: boolean;
}

// UX-19: 명령 팔레트(Ctrl/⌘+K) + 단축키 도움말("?") 단일 다이얼로그. MAIN/SETTINGS/
// (관리자면 ADMIN)_NAV_ITEMS를 그대로 명령 목록으로 써서, nav에 링크가 있는 모든
// 화면이 곧 이 팔레트로 키보드만으로 도달 가능한 목록이 되게 한다(DoD "키보드 전 경로").
export function CommandPalette({ isAdmin }: CommandPaletteProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [isOpen, setIsOpen] = useState(false);
  const [mode, setMode] = useState<PaletteMode>("search");
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const commands = useMemo(() => {
    const items = [...MAIN_NAV_ITEMS, ...SETTINGS_NAV_ITEMS, ...(isAdmin ? ADMIN_NAV_ITEMS : [])];
    return buildNavCommands(items).map(cmd => ({
      ...cmd,
      label: t(cmd.label as any),
    }));
  }, [isAdmin, t]);

  const results = useMemo(() => matchCommands(commands, query), [commands, query]);

  // t()'s key argument only accepts the literal union derived from catalog.ko's
  // shape (i18next.d.ts) -- collect the literal calls here so SHORTCUT_ENTRIES.id
  // can still look labels up dynamically.
  // (English comment: a Korean comment sitting between the `>` above and the `<`
  // in `Record<...>` below false-triggers scripts/check_i18n_literals.mjs's
  // JSX-text heuristic, which doesn't respect line breaks -- task-2704 UX-20.)
  const shortcutLabels: Record<ShortcutId, string> = {
    openSearch: t("commandPalette.shortcut.openSearch"),
    openHelp: t("commandPalette.shortcut.openHelp"),
    navigate: t("commandPalette.shortcut.navigate"),
    select: t("commandPalette.shortcut.select"),
    close: t("commandPalette.shortcut.close"),
  };

  function open(nextMode: PaletteMode) {
    setMode(nextMode);
    setQuery("");
    setActiveIndex(0);
    setIsOpen(true);
  }

  function close() {
    setIsOpen(false);
  }

  useCommandPaletteShortcuts({ onOpen: open, enabled: !isOpen });
  useDialogFocusTrap({ isOpen, onClose: close, containerRef: dialogRef });

  // useDialogFocusTrap이 열릴 때 컨테이너의 첫 포커스 가능 요소(제목 옆 토글
  // 버튼)로 이동시킨 뒤, search 모드에서는 검색창으로 다시 옮긴다 -- 선언 순서상
  // 이 effect가 트랩의 effect보다 나중에 실행돼 마지막 값이 이긴다.
  useEffect(() => {
    if (isOpen && mode === "search") inputRef.current?.focus();
  }, [isOpen, mode]);

  function runCommand(command: Command) {
    close();
    navigate(command.to);
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, Math.max(results.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const selected = results[activeIndex];
      if (selected) runCommand(selected);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => open("search")}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border-strong px-3 text-sm text-fg-secondary transition-colors hover:bg-surface-hover hover:text-fg"
      >
        <span aria-hidden="true">⌘K</span>
        <span>{t("commandPalette.trigger")}</span>
      </button>

      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-4 pt-24">
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={TITLE_ID}
            className="w-full max-w-lg space-y-3 rounded-xl border border-border bg-surface p-4 shadow-lg"
          >
            <div className="flex items-center justify-between">
              <h2 id={TITLE_ID} className="text-sm font-semibold text-fg">
                {mode === "search" ? t("commandPalette.searchTitle") : t("commandPalette.helpTitle")}
              </h2>
              <button
                type="button"
                onClick={() => setMode(mode === "search" ? "help" : "search")}
                className="text-xs text-accent underline"
              >
                {mode === "search" ? t("commandPalette.showHelp") : t("commandPalette.showSearch")}
              </button>
            </div>

            {mode === "search" ? (
              <>
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setActiveIndex(0);
                  }}
                  onKeyDown={handleInputKeyDown}
                  placeholder={t("commandPalette.placeholder")}
                  aria-label={t("commandPalette.searchTitle")}
                  className={INPUT_CLASS}
                />
                {results.length === 0 ? (
                  <p className="py-4 text-center text-sm text-fg-muted">{t("commandPalette.empty")}</p>
                ) : (
                  <ul role="listbox" className="max-h-72 space-y-0.5 overflow-y-auto">
                    {results.map((command, index) => (
                      <li key={command.id}>
                        <button
                          type="button"
                          role="option"
                          aria-selected={index === activeIndex}
                          onMouseEnter={() => setActiveIndex(index)}
                          onClick={() => runCommand(command)}
                          className={cn(
                            "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm",
                            index === activeIndex
                              ? "bg-accent-muted text-accent-hover"
                              : "text-fg hover:bg-surface-hover",
                          )}
                        >
                          <span>{command.label}</span>
                          <span className="text-xs text-fg-muted">{command.to}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <table className="w-full text-sm">
                <tbody>
                  {SHORTCUT_ENTRIES.map((entry) => (
                    <tr key={entry.keys} className="border-b border-border last:border-0">
                      <td className="py-2 pr-4 font-mono text-xs text-fg-secondary">{entry.keys}</td>
                      <td className="py-2 text-fg">{shortcutLabels[entry.id]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </>
  );
}
