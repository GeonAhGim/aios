import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TemplateApplyResult } from "./ChartTemplates";
import { useIndicatorSelection } from "./useIndicatorSelection";

// CH-6a 순수 이동(task-2011): ChartPage.tsx에서 그대로 옮겨진 "레전드/오브젝트
// 트리 상태" 소유 단위. 로직 변경이 없는 이동이므로, 여기서는 그 계약 자체
// (토글·main/sub 분리·템플릿 적용 시 재마운트 트리거)를 검증한다.

describe("useIndicatorSelection — 초기 상태", () => {
  it("아무것도 선택되지 않은 채, 레지스트리 전체가 overlayEntries/knownIndicatorIds로 노출된다", () => {
    const { result } = renderHook(() => useIndicatorSelection());

    expect(result.current.selectedIndicatorIds).toEqual([]);
    expect(result.current.selectedOverlayEntries).toEqual([]);
    expect(result.current.mainOverlayEntries).toEqual([]);
    expect(result.current.subOverlayEntries).toEqual([]);
    expect(result.current.knownIndicatorIds.has("SMA")).toBe(true);
    expect(result.current.knownIndicatorIds.has("RSI")).toBe(true);
    expect(result.current.appliedPaneHeightRatios).toBeUndefined();
    expect(result.current.paneRemountKey).toBe(0);
  });
});

describe("useIndicatorSelection — toggleIndicator", () => {
  it("선택되지 않은 id를 토글하면 추가되고, main-overlay/sub-pane 분리 목록에 각각 반영된다", () => {
    const { result } = renderHook(() => useIndicatorSelection());

    act(() => result.current.toggleIndicator("SMA"));
    act(() => result.current.toggleIndicator("RSI"));

    expect(result.current.selectedIndicatorIds).toEqual(["SMA", "RSI"]);
    expect(result.current.mainOverlayEntries.map((e) => e.id)).toEqual(["SMA"]);
    expect(result.current.subOverlayEntries.map((e) => e.id)).toEqual(["RSI"]);
  });

  it("이미 선택된 id를 다시 토글하면 제거된다", () => {
    const { result } = renderHook(() => useIndicatorSelection());

    act(() => result.current.toggleIndicator("SMA"));
    act(() => result.current.toggleIndicator("SMA"));

    expect(result.current.selectedIndicatorIds).toEqual([]);
    expect(result.current.mainOverlayEntries).toEqual([]);
  });
});

describe("useIndicatorSelection — handleTemplateApplied(CH-17c)", () => {
  it("템플릿 결과의 indicatorIds/paneHeightRatios를 그대로 반영하고 paneRemountKey를 증가시킨다(CH-17c 재마운트 강제)", () => {
    const { result } = renderHook(() => useIndicatorSelection());
    const applyResult: TemplateApplyResult = {
      indicatorIds: ["SMA", "MACD"],
      paneHeightRatios: { main: 0.6, "sub-MACD": 0.4 },
    };

    act(() => result.current.handleTemplateApplied(applyResult));

    expect(result.current.selectedIndicatorIds).toEqual(["SMA", "MACD"]);
    expect(result.current.appliedPaneHeightRatios).toEqual({ main: 0.6, "sub-MACD": 0.4 });
    expect(result.current.paneRemountKey).toBe(1);
    expect(result.current.mainOverlayEntries.map((e) => e.id)).toEqual(["SMA"]);
    expect(result.current.subOverlayEntries.map((e) => e.id)).toEqual(["MACD"]);
  });

  it("연속으로 템플릿을 적용하면 paneRemountKey가 매번 증가한다", () => {
    const { result } = renderHook(() => useIndicatorSelection());

    act(() => result.current.handleTemplateApplied({ indicatorIds: ["SMA"], paneHeightRatios: { main: 1 } }));
    act(() => result.current.handleTemplateApplied({ indicatorIds: ["EMA"], paneHeightRatios: { main: 1 } }));

    expect(result.current.paneRemountKey).toBe(2);
    expect(result.current.selectedIndicatorIds).toEqual(["EMA"]);
  });
});

describe("useIndicatorSelection — setSelectedIndicatorIds 직접 노출", () => {
  it("setSelectedIndicatorIds로 직접 갱신할 수 있다(외부 배선 호환)", () => {
    const { result } = renderHook(() => useIndicatorSelection());

    act(() => result.current.setSelectedIndicatorIds(["ATR", "OBV"]));

    expect(result.current.selectedIndicatorIds).toEqual(["ATR", "OBV"]);
    expect(result.current.subOverlayEntries.map((e) => e.id)).toEqual(["ATR", "OBV"]);
  });
});
