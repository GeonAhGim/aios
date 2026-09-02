import { useCallback, useState } from "react";
import { createCursorNavigator, type CursorNavigatorMeta } from "../lib/cursorPagination";

export interface UseCursorPageResult {
  /** 목록 조회 쿼리 파라미터로 그대로 넘길 값. 첫 페이지면 undefined. */
  cursor: string | undefined;
  hasPrev: boolean;
  /** 마지막으로 전달한 meta.next_cursor 유무. meta가 아직 없으면 false. */
  hasNext: boolean;
  /** 다음 페이지로 이동한다. hasNext가 false면 아무 것도 하지 않는다. */
  next: () => void;
  /** 이전 페이지로 이동한다. hasPrev가 false면 아무 것도 하지 않는다. */
  prev: () => void;
  reset: () => void;
}

/**
 * 커서 방식 목록(total=null, spec §3.3 PageMeta)의 내비게이션 상태를 React 컴포넌트에
 * 연결한다. total 기반 페이지 수는 pagination.ts(derivePageState)가 이미 담당하므로
 * 이 훅은 "총 N페이지"를 계산하거나 노출하지 않는다 — cursor/hasNext/hasPrev만 준다.
 *
 * meta는 가장 최근에 받은 목록 조회 응답의 page meta(예: data.meta.page)를 매 렌더마다
 * 그대로 전달한다. next()는 그 meta를 이용해 이동하므로 별도 인자를 받지 않는다.
 */
export function useCursorPage(
  meta: CursorNavigatorMeta | null | undefined,
): UseCursorPageResult {
  const [navigator] = useState(() => createCursorNavigator());
  const [, bump] = useState(0);
  const rerender = useCallback(() => bump((n) => n + 1), []);

  const next = useCallback(() => {
    if (!meta || meta.next_cursor == null) return;
    navigator.next(meta);
    rerender();
  }, [meta, navigator, rerender]);

  const prev = useCallback(() => {
    if (!navigator.getState().hasPrev) return;
    navigator.prev();
    rerender();
  }, [navigator, rerender]);

  const reset = useCallback(() => {
    navigator.reset();
    rerender();
  }, [navigator, rerender]);

  const state = navigator.getState();

  return {
    cursor: state.cursor,
    hasPrev: state.hasPrev,
    hasNext: meta != null && meta.next_cursor != null,
    next,
    prev,
    reset,
  };
}
