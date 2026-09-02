import { describe, expect, it } from "vitest";
import { derivePageState } from "./pagination";

describe("derivePageState", () => {
  it("일반적인 중간 페이지: total/size로 총 페이지·이전/다음·범위를 계산한다", () => {
    const state = derivePageState({ page: 2, size: 20, total: 45, next_cursor: null });

    expect(state).toEqual({
      page: 2,
      size: 20,
      total: 45,
      totalPages: 3,
      hasPrev: true,
      hasNext: true,
      rangeStart: 21,
      rangeEnd: 40,
    });
  });

  it("마지막 페이지: 다음 페이지가 없고 range 끝이 total로 잘린다", () => {
    const state = derivePageState({ page: 3, size: 20, total: 45, next_cursor: null });

    expect(state.hasNext).toBe(false);
    expect(state.hasPrev).toBe(true);
    expect(state.rangeStart).toBe(41);
    expect(state.rangeEnd).toBe(45);
  });

  it("첫 페이지: 이전 페이지가 없다", () => {
    const state = derivePageState({ page: 1, size: 20, total: 45, next_cursor: null });

    expect(state.hasPrev).toBe(false);
    expect(state.hasNext).toBe(true);
  });

  it("커서 방식(total=null): totalPages/range는 null·0을 반환하지 않고 추정치를 주며 next_cursor로 hasNext를 판단한다", () => {
    const withMore = derivePageState({ page: 1, size: 20, total: null, next_cursor: "abc" });
    expect(withMore.totalPages).toBeNull();
    expect(withMore.total).toBeNull();
    expect(withMore.hasNext).toBe(true);
    expect(withMore.hasPrev).toBe(false);
    expect(withMore.rangeStart).toBe(1);
    expect(withMore.rangeEnd).toBe(20);

    const noMore = derivePageState({ page: 2, size: 20, total: null, next_cursor: null });
    expect(noMore.hasNext).toBe(false);
    expect(noMore.hasPrev).toBe(true);
  });

  describe("negative: 예외 없이 안전한 값으로 수렴", () => {
    it("total=0: 총 페이지 0, 다음/이전 없음, range는 0/0", () => {
      const state = derivePageState({ page: 1, size: 20, total: 0, next_cursor: null });

      expect(state.totalPages).toBe(0);
      expect(state.hasPrev).toBe(false);
      expect(state.hasNext).toBe(false);
      expect(state.rangeStart).toBe(0);
      expect(state.rangeEnd).toBe(0);
      expect(state.page).toBe(1);
    });

    it("size=0: 나눗셈 폭주 없이 기본 크기로 보정한다", () => {
      const state = derivePageState({ page: 1, size: 0, total: 45, next_cursor: null });

      expect(() => state).not.toThrow();
      expect(state.size).toBeGreaterThan(0);
      expect(Number.isFinite(state.totalPages)).toBe(true);
    });

    it("size가 음수: 기본 크기로 보정한다", () => {
      const state = derivePageState({ page: 1, size: -5, total: 45, next_cursor: null });

      expect(state.size).toBeGreaterThan(0);
      expect(state.totalPages).toBeGreaterThan(0);
    });

    it("page=0: 서버가 400으로 막는 값이지만 클라이언트는 1로 보정한다", () => {
      const state = derivePageState({ page: 0, size: 20, total: 45, next_cursor: null });

      expect(state.page).toBe(1);
      expect(state.hasPrev).toBe(false);
    });

    it("page가 음수: 1로 보정한다", () => {
      const state = derivePageState({ page: -3, size: 20, total: 45, next_cursor: null });

      expect(state.page).toBe(1);
    });

    it("page가 총 페이지수를 초과: 마지막 페이지로 클램프한다", () => {
      const state = derivePageState({ page: 999, size: 20, total: 45, next_cursor: null });

      expect(state.page).toBe(3);
      expect(state.hasNext).toBe(false);
      expect(state.hasPrev).toBe(true);
    });

    it("total이 음수: 0으로 보정한다", () => {
      const state = derivePageState({ page: 1, size: 20, total: -10, next_cursor: null });

      expect(state.total).toBe(0);
      expect(state.totalPages).toBe(0);
    });

    it("meta 자체가 없음(봉투에 pagination 없음): 안전한 기본 상태를 반환한다", () => {
      expect(derivePageState(null)).toEqual({
        page: 1,
        size: 20,
        total: null,
        totalPages: null,
        hasPrev: false,
        hasNext: false,
        rangeStart: 0,
        rangeEnd: 0,
      });

      expect(derivePageState(undefined, { defaultSize: 50 }).size).toBe(50);
    });

    it("meta.page가 null(커서 응답에서 흔함): 1로 보정한다", () => {
      const state = derivePageState({ page: null, size: 20, total: 45, next_cursor: null });

      expect(state.page).toBe(1);
    });
  });
});
