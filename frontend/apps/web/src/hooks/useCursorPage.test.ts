import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useCursorPage } from "./useCursorPage";
import type { CursorNavigatorMeta } from "../lib/cursorPagination";

describe("useCursorPage", () => {
  it("meta가 없으면 cursor는 undefined, hasNext/hasPrev 모두 false다", () => {
    const { result } = renderHook(() => useCursorPage(null));

    expect(result.current.cursor).toBeUndefined();
    expect(result.current.hasNext).toBe(false);
    expect(result.current.hasPrev).toBe(false);
  });

  it("meta.next_cursor가 있으면 hasNext가 true다(total=null이어도 totalPages를 요구하지 않는다)", () => {
    const meta: CursorNavigatorMeta = { next_cursor: "page2" };
    const { result } = renderHook(() => useCursorPage(meta));

    expect(result.current.hasNext).toBe(true);
    expect(result.current).not.toHaveProperty("totalPages");
  });

  it("next(): 다음 페이지로 이동하면 cursor가 next_cursor 값이 되고 hasPrev가 true가 된다", () => {
    let meta: CursorNavigatorMeta = { next_cursor: "page2" };
    const { result, rerender } = renderHook(() => useCursorPage(meta));

    act(() => result.current.next());

    expect(result.current.cursor).toBe("page2");
    expect(result.current.hasPrev).toBe(true);

    meta = { next_cursor: null };
    rerender();
    expect(result.current.cursor).toBe("page2");
    expect(result.current.hasNext).toBe(false);
  });

  it("prev(): 이전 페이지로 이동하면 첫 페이지 커서(undefined)로 돌아간다", () => {
    let meta: CursorNavigatorMeta = { next_cursor: "page2" };
    const { result, rerender } = renderHook(() => useCursorPage(meta));
    act(() => result.current.next());
    meta = { next_cursor: null };
    rerender();

    act(() => result.current.prev());

    expect(result.current.cursor).toBeUndefined();
    expect(result.current.hasPrev).toBe(false);
  });

  it("reset(): 여러 페이지 이동 후에도 첫 페이지 상태로 되돌린다", () => {
    let meta: CursorNavigatorMeta = { next_cursor: "page2" };
    const { result, rerender } = renderHook(() => useCursorPage(meta));
    act(() => result.current.next());
    meta = { next_cursor: "page3" };
    rerender();
    act(() => result.current.next());

    act(() => result.current.reset());

    expect(result.current.cursor).toBeUndefined();
    expect(result.current.hasPrev).toBe(false);
  });

  describe("negative: 이동할 수 없을 때는 조용히 무시한다", () => {
    it("meta.next_cursor가 null이면 next()를 호출해도 cursor가 바뀌지 않는다", () => {
      const meta: CursorNavigatorMeta = { next_cursor: null };
      const { result } = renderHook(() => useCursorPage(meta));

      act(() => result.current.next());

      expect(result.current.cursor).toBeUndefined();
      expect(result.current.hasPrev).toBe(false);
    });

    it("첫 페이지에서 prev()를 호출해도 상태가 바뀌지 않는다", () => {
      const meta: CursorNavigatorMeta = { next_cursor: "page2" };
      const { result } = renderHook(() => useCursorPage(meta));

      act(() => result.current.prev());

      expect(result.current.cursor).toBeUndefined();
      expect(result.current.hasPrev).toBe(false);
    });
  });
});
