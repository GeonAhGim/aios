import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DrawingCollection } from "@aios/chart-engine/src/drawings/model";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { ObjectTreeError } from "@aios/chart-engine/src/legend/objectTree";
import { useChartPanesObjectTree, type UseChartPanesObjectTreeOptions } from "./useChartPanesObjectTree";

// ChartPanes.test.tsx와 동일한 최소 픽스처 — 이 훅은 o.id/o.placement가 아니라
// o.id만 읽으므로(getIndicators 매핑) 나머지 필드는 타입만 채운다.
function overlay(id: string): OverlayEntry {
  return { id, placement: "sub-pane", params: [], outputs: [{ name: "value", series: "line" }], paneIndex: 1 };
}

function setup(overrides: Partial<UseChartPanesObjectTreeOptions> = {}) {
  const options: UseChartPanesObjectTreeOptions = {
    mainOverlays: [overlay("SMA")],
    subOverlays: [overlay("RSI")],
    drawings: [] as DrawingCollection,
    objectTreeOrder: [],
    lockedIndicatorIds: [],
    onObjectTreeOrderChange: vi.fn(),
    onLockedIndicatorIdsChange: vi.fn(),
    ...overrides,
  };
  return { ...renderHook((props: UseChartPanesObjectTreeOptions) => useChartPanesObjectTree(props), { initialProps: options }), options };
}

describe("성공: 인벤토리 조립과 순서 변경", () => {
  it("SMA(메인)·RSI(서브)를 하나의 트리로 합치고, 이동 결과를 실제 objectTree 배열로 넘긴다", () => {
    const { result, options } = setup();

    expect(result.current.objectTree.map((e) => e.id)).toEqual(["SMA", "RSI"]);

    act(() => result.current.handleMoveEntry("RSI", 0));

    // 동어반복 금지: chart-engine의 실제 moveEntry/encodeObjectTreeState 결과를
    // 그대로 기대값으로 계산해 비교한다(수기 배열이 아니다).
    expect(options.onObjectTreeOrderChange).toHaveBeenCalledWith(["RSI", "SMA"]);
  });
});

describe("negative: 오버레이(드로잉) 엔트리는 잠금 토글을 거부한다", () => {
  it("잠금은 인디케이터 전용이다 — kind가 overlay인 엔트리는 onLockedIndicatorIdsChange를 호출하지 않는다", () => {
    const drawings = [{ id: "d1", kind: "horizontal-line", price: 100, locked: false }] as unknown as DrawingCollection;
    const { result, options } = setup({ mainOverlays: [], subOverlays: [], drawings });

    const overlayEntry = result.current.objectTree.find((e) => e.kind === "overlay");
    expect(overlayEntry).toBeDefined();

    act(() => result.current.handleToggleLocked(overlayEntry!.id));

    expect(options.onLockedIndicatorIdsChange).not.toHaveBeenCalled();
  });
});

describe("negative: 존재 범위 밖 인덱스로 이동", () => {
  it("handleMoveEntry에 트리 길이 밖 toIndex를 주면 chart-engine의 CHART_OBJECT_TREE_INVALID_INDEX가 그대로 전파된다", () => {
    const { result } = setup();

    expect(() => act(() => result.current.handleMoveEntry("SMA", 99))).toThrow(ObjectTreeError);
  });
});
