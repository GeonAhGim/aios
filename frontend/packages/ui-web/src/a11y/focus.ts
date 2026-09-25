import { useEffect, type RefObject } from "react";

const FOCUSABLE_SELECTOR =
  'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export interface FocusQueryable<T> {
  querySelectorAll<E = T>(selector: string): ArrayLike<E>;
}

export function getFocusableElements<T>(container: FocusQueryable<T>): T[] {
  return Array.from(container.querySelectorAll<T>(FOCUSABLE_SELECTOR));
}

export interface TrapNavigationEvent {
  key: string;
  shiftKey: boolean;
}

/**
 * Tab이 트랩 경계(첫/끝 포커스 가능 요소)를 넘으려 하면 반대쪽 끝 요소를,
 * 개입할 필요가 없으면 null을 반환한다. AlertFromChart.tsx(CH-9)에 있던
 * 인라인 트랩 로직에서 DOM 의존 없이 뽑아낸 순수 함수 -- 호출부가
 * `event.preventDefault()`/`.focus()`를 실제로 수행한다.
 */
export function computeFocusTrapTarget<T>(
  event: TrapNavigationEvent,
  elements: readonly T[],
  activeElement: T | null,
): T | null {
  if (event.key !== "Tab" || elements.length === 0) return null;
  const first = elements[0]!;
  const last = elements[elements.length - 1]!;
  if (event.shiftKey && activeElement === first) return last;
  if (!event.shiftKey && activeElement === last) return first;
  return null;
}

export interface UseDialogFocusTrapOptions {
  isOpen: boolean;
  onClose?: () => void;
  containerRef: RefObject<HTMLElement | null>;
}

/**
 * 모달/다이얼로그 공용 포커스 트랩: 열릴 때 첫 포커스 가능 요소로 이동하고,
 * Tab/Shift+Tab을 컨테이너 안에 가두고, Escape로 닫으며(onClose 제공 시),
 * 닫힌 뒤 트리거로 포커스를 되돌린다. AlertFromChart.tsx(CH-9)의 원본 구현을
 * 그대로 추출한 것이라 동작은 이전과 동일하다(UX-4 task-2688).
 */
export function useDialogFocusTrap({ isOpen, onClose, containerRef }: UseDialogFocusTrapOptions): void {
  useEffect(() => {
    if (!isOpen) return undefined;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const container = containerRef.current;
    getFocusableElements<HTMLElement>(container ?? document.body)[0]?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && onClose) {
        event.preventDefault();
        onClose();
        return;
      }
      if (!container) return;
      const items = getFocusableElements<HTMLElement>(container);
      const target = computeFocusTrapTarget(event, items, document.activeElement as HTMLElement | null);
      if (target) {
        event.preventDefault();
        target.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, onClose]);
}
