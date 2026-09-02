import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useReadiness } from "./useReadiness";

const READY_REPORT = {
  status: "ready",
  checks: { db_pool: { ok: true, detail: null, observed: 3, threshold: 10 } },
  as_of: "2026-09-03T00:00:00Z",
};

const NOT_READY_REPORT = {
  status: "not_ready",
  checks: {
    db_pool: { ok: true, detail: null, observed: 1, threshold: 10 },
    event_bus: { ok: false, detail: "no consumers", observed: 0, threshold: 1 },
    "loop:trading": { ok: false, detail: null, observed: 900, threshold: 300 },
  },
  as_of: "2026-09-03T00:00:00Z",
};

describe("useReadiness", () => {
  it("초기 상태는 배너를 띄우지 않고 실패 원인도 없다(unknown)", () => {
    const { result } = renderHook(() => useReadiness());
    expect(result.current.showDegradedBanner).toBe(false);
    expect(result.current.failureReasons).toEqual([]);
  });

  it("ready 응답을 넣으면 배너를 띄우지 않는다", () => {
    const { result } = renderHook(() => useReadiness());

    act(() => {
      result.current.setFromResponse(READY_REPORT);
    });

    expect(result.current.showDegradedBanner).toBe(false);
    expect(result.current.failureReasons).toEqual([]);
  });

  it("not_ready 응답을 넣으면 배너를 띄우고 실패 원인을 'name: detail' 형식으로 나열한다", () => {
    const { result } = renderHook(() => useReadiness());

    act(() => {
      result.current.setFromResponse(NOT_READY_REPORT);
    });

    expect(result.current.showDegradedBanner).toBe(true);
    expect(result.current.failureReasons).toEqual(["event_bus: no consumers", "loop:trading"]);
  });

  it("ApiResponse 봉투로 감싼 응답도 그대로 소비한다", () => {
    const { result } = renderHook(() => useReadiness());

    act(() => {
      result.current.setFromResponse({
        data: NOT_READY_REPORT,
        meta: { trace_id: "t1", as_of: "2026-09-03T00:00:00Z", page: null },
      });
    });

    expect(result.current.showDegradedBanner).toBe(true);
    expect(result.current.failureReasons.length).toBe(2);
  });

  it("negative: 파싱 실패 응답(스키마 불일치)은 not_ready로 단정하지 않고 배너를 띄우지 않는다", () => {
    const { result } = renderHook(() => useReadiness());

    act(() => {
      result.current.setFromResponse({ status: "healthy", checks: {}, as_of: "x" });
    });

    expect(result.current.showDegradedBanner).toBe(false);
    expect(result.current.failureReasons).toEqual([]);
  });

  it("negative: 응답 자체가 없어도(null) not_ready로 단정하지 않는다", () => {
    const { result } = renderHook(() => useReadiness());

    act(() => {
      result.current.setFromResponse(null);
    });

    expect(result.current.showDegradedBanner).toBe(false);
  });

  it("이전에 not_ready였다가 새 ready 응답을 받으면 배너가 사라진다", () => {
    const { result } = renderHook(() => useReadiness());

    act(() => {
      result.current.setFromResponse(NOT_READY_REPORT);
    });
    expect(result.current.showDegradedBanner).toBe(true);

    act(() => {
      result.current.setFromResponse(READY_REPORT);
    });

    expect(result.current.showDegradedBanner).toBe(false);
    expect(result.current.failureReasons).toEqual([]);
  });
});
