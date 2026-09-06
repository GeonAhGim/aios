// ChartToolbar.tsx와 동일한 이유로 배럴 대신 vendor에 의존하지 않는
// indicators/overlayRegistry 서브모듈만 직접 불러온다.
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { Button } from "@aios/ui-web";
import type { KeyboardEvent } from "react";
import { useEffect, useRef, useState } from "react";

// CH-6a: chart-engine CH-3 overlayRegistry가 내려주는 지표 카탈로그(id·placement)
// 중 어떤 지표를 화면에 켤지만 고르는 로컬 선택 상태다. 값 계산은 백엔드
// charting API(CH-5, 아직 없음) 소관이라 여기서는 호출·계산을 전혀 하지
// 않는다 — 선택된 id 목록만 부모(ChartPage)에 올려보낸다(6b가 서버에 저장).

interface IndicatorPickerProps {
  available: readonly OverlayEntry[];
  selectedIds: readonly string[];
  onToggle: (id: string) => void;
}

const LISTBOX_ID = "indicator-picker-listbox";

export function IndicatorPicker({ available, selectedIds, onToggle }: IndicatorPickerProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (open) listRef.current?.focus();
  }, [open]);

  function close(): void {
    setOpen(false);
    containerRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }

  function moveActive(delta: number): void {
    if (available.length === 0) return;
    setActiveIndex((prev) => (prev + delta + available.length) % available.length);
  }

  function onListKeyDown(event: KeyboardEvent<HTMLUListElement>): void {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        moveActive(1);
        break;
      case "ArrowUp":
        event.preventDefault();
        moveActive(-1);
        break;
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        event.preventDefault();
        setActiveIndex(Math.max(0, available.length - 1));
        break;
      case "Enter":
      case " ": {
        event.preventDefault();
        const entry = available[activeIndex];
        if (entry) onToggle(entry.id);
        break;
      }
      case "Escape":
        event.preventDefault();
        close();
        break;
      default:
        break;
    }
  }

  return (
    <div className="relative inline-block" data-testid="indicator-picker" ref={containerRef}>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={LISTBOX_ID}
        onClick={() => setOpen((prev) => !prev)}
      >
        지표 선택 ({selectedIds.length})
      </Button>

      {open && (
        <ul
          id={LISTBOX_ID}
          role="listbox"
          aria-multiselectable="true"
          aria-label="지표 목록"
          tabIndex={0}
          className="absolute z-10 mt-1 max-h-64 w-56 overflow-auto rounded-md border border-border bg-surface p-1 shadow-lg"
          onKeyDown={onListKeyDown}
          ref={listRef}
        >
          {available.map((entry, index) => {
            const selected = selectedIds.includes(entry.id);
            return (
              <li
                key={entry.id}
                id={`indicator-option-${entry.id}`}
                role="option"
                aria-selected={selected}
                data-active={index === activeIndex || undefined}
                className={
                  "cursor-pointer rounded px-2 py-1.5 text-sm " +
                  (index === activeIndex ? "bg-surface-hover" : "") +
                  (selected ? " font-medium text-accent" : " text-fg")
                }
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => onToggle(entry.id)}
              >
                {selected ? "✓ " : ""}
                {entry.id}
                <span className="ml-1 text-xs text-fg-muted">
                  ({entry.placement === "main-overlay" ? "메인" : "서브패널"})
                </span>
              </li>
            );
          })}
          {available.length === 0 && <li className="px-2 py-1.5 text-sm text-fg-muted">지표가 없습니다.</li>}
        </ul>
      )}

      {selectedIds.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="선택된 지표">
          {selectedIds.map((id) => (
            <li key={id}>
              <Button type="button" variant="ghost" size="sm" onClick={() => onToggle(id)}>
                {id} ✕
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
