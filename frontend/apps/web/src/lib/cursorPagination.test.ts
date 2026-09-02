import { describe, expect, it } from "vitest";
import { CursorNavigationError, createCursorNavigator } from "./cursorPagination";

describe("createCursorNavigator", () => {
  it("초기 상태: 첫 페이지 커서는 undefined이고 이전 페이지가 없다", () => {
    const nav = createCursorNavigator();

    expect(nav.getState()).toEqual({ cursor: undefined, hasPrev: false });
  });

  it("next(): next_cursor로 이동하고 이전 페이지가 생긴다", () => {
    const nav = createCursorNavigator();

    nav.next({ next_cursor: "page2" });

    expect(nav.getState()).toEqual({ cursor: "page2", hasPrev: true });
  });

  it("next()를 여러 번 호출하면 가장 최근 커서로 이동한다", () => {
    const nav = createCursorNavigator();

    nav.next({ next_cursor: "page2" });
    nav.next({ next_cursor: "page3" });

    expect(nav.getState()).toEqual({ cursor: "page3", hasPrev: true });
  });

  it("prev(): 방문 스택에서 바로 이전 커서로 되돌아간다", () => {
    const nav = createCursorNavigator();
    nav.next({ next_cursor: "page2" });
    nav.next({ next_cursor: "page3" });

    nav.prev();

    expect(nav.getState()).toEqual({ cursor: "page2", hasPrev: true });
  });

  it("prev()를 첫 페이지까지 반복하면 cursor가 undefined로 돌아가고 hasPrev가 false가 된다", () => {
    const nav = createCursorNavigator();
    nav.next({ next_cursor: "page2" });
    nav.next({ next_cursor: "page3" });

    nav.prev();
    nav.prev();

    expect(nav.getState()).toEqual({ cursor: undefined, hasPrev: false });
  });

  it("prev() 후 next()하면 이전 '다음' 이력을 버리고 새 커서로 대체한다", () => {
    const nav = createCursorNavigator();
    nav.next({ next_cursor: "page2" });
    nav.next({ next_cursor: "page3-old" });
    nav.prev();

    nav.next({ next_cursor: "page3-new" });

    expect(nav.getState()).toEqual({ cursor: "page3-new", hasPrev: true });
    // 예전 이력(page3-old)이 아니라 새 이력만 쌓였는지 prev()로 재확인한다.
    nav.prev();
    expect(nav.getState()).toEqual({ cursor: "page2", hasPrev: true });
  });

  it("reset(): 방문 이력을 모두 지우고 첫 페이지 상태로 되돌린다", () => {
    const nav = createCursorNavigator();
    nav.next({ next_cursor: "page2" });
    nav.next({ next_cursor: "page3" });

    nav.reset();

    expect(nav.getState()).toEqual({ cursor: undefined, hasPrev: false });
  });

  it("reset() 이후 다시 next()를 정상적으로 사용할 수 있다", () => {
    const nav = createCursorNavigator();
    nav.next({ next_cursor: "page2" });
    nav.reset();

    nav.next({ next_cursor: "fresh" });

    expect(nav.getState()).toEqual({ cursor: "fresh", hasPrev: true });
  });

  describe("negative: 잘못된 이동을 거부한다", () => {
    it("next_cursor가 null이면 next()를 거부하고 상태를 바꾸지 않는다", () => {
      const nav = createCursorNavigator();

      expect(() => nav.next({ next_cursor: null })).toThrow(CursorNavigationError);
      expect(nav.getState()).toEqual({ cursor: undefined, hasPrev: false });
    });

    it("방문 이력이 없는 첫 페이지에서 prev()를 거부하고 상태를 바꾸지 않는다", () => {
      const nav = createCursorNavigator();

      expect(() => nav.prev()).toThrow(CursorNavigationError);
      expect(nav.getState()).toEqual({ cursor: undefined, hasPrev: false });
    });

    it("이동 중간에 next_cursor가 null이 되면 그 지점에서 거부하고 이전 상태를 유지한다", () => {
      const nav = createCursorNavigator();
      nav.next({ next_cursor: "page2" });

      expect(() => nav.next({ next_cursor: null })).toThrow(CursorNavigationError);
      expect(nav.getState()).toEqual({ cursor: "page2", hasPrev: true });
    });
  });
});
