import { useRef, useState } from "react";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createEmptyLayoutModel, type ChartLayoutModel } from "@aios/chart-engine/src/layout/layoutModel";
import { useChartLayoutPanels, type UseChartLayoutPanelsOptions } from "./useChartLayoutPanels";
import type { ChartViewSnapshot } from "./chartLayoutView";

const BASE_VIEW: ChartViewSnapshot = {
  instrumentId: "BTCUSDT",
  venue: "BITGET",
  timeframe: "1h",
  indicatorIds: [],
  compareSymbolIds: [],
};

let idSeq = 0;
function genId(prefix: string): string {
  idSeq += 1;
  return `${prefix}-${idSeq}`;
}

function modelWithOnePanel(): ChartLayoutModel {
  return { ...createEmptyLayoutModel(), panels: [{ id: "panel-0", ...panelFieldsFor(BASE_VIEW), drawingSetId: "panel-0" }], activePanelId: "panel-0" };
}

function panelFieldsFor(view: ChartViewSnapshot) {
  return {
    instrument: { instrumentId: view.instrumentId, venue: view.venue, symbol: view.instrumentId },
    timeframe: view.timeframe,
    indicators: view.indicatorIds.map((id) => ({ id })),
  };
}

type HarnessProps = Omit<UseChartLayoutPanelsOptions, "onApplyViewRef" | "genId" | "model" | "setModel" | "setIsDirty"> & {
  readonly initialModel: ChartLayoutModel;
  readonly onApplyView: (view: ChartViewSnapshot) => void;
  readonly setIsDirty: (dirty: boolean) => void;
};

// useChartLayout.ts가 실제 조립 시 하는 것과 동일하게: 모델은 상위(useChartLayoutPersistence)가
// 갖고 있으므로, 이 하니스는 그 자리를 useState로 대신하고 model/setModel을 그대로 훅에 넘긴다.
function setup(initialModel: ChartLayoutModel = modelWithOnePanel(), view: ChartViewSnapshot = BASE_VIEW) {
  idSeq = 0;
  const onApplyView = vi.fn();
  const setIsDirty = vi.fn();
  const { result, rerender } = renderHook(
    (props: HarnessProps) => {
      const onApplyViewRef = useRef(props.onApplyView);
      onApplyViewRef.current = props.onApplyView;
      // useState의 초기값 인자는 마운트 시 한 번만 쓰인다 — rerender로 다른
      // initialProps가 들어와도 이후 렌더에서 model은 setModel로만 바뀐다.
      const [model, setModel] = useState(props.initialModel);
      const panels = useChartLayoutPanels({
        view: props.view,
        model,
        setModel,
        setIsDirty: props.setIsDirty,
        onApplyViewRef,
        genId,
      });
      return { model, ...panels };
    },
    { initialProps: { initialModel, view, onApplyView, setIsDirty } },
  );
  return { result, rerender, onApplyView, setIsDirty };
}

describe("성공: 패널 추가", () => {
  it("addPanel은 현재 뷰 값으로 새 패널을 만들고 활성 패널로 전환한다", () => {
    const { result } = setup();
    act(() => result.current.addPanel());

    expect(result.current.model.panels).toHaveLength(2);
    const added = result.current.model.panels[1]!;
    expect(added.instrument.instrumentId).toBe(BASE_VIEW.instrumentId);
    expect(result.current.model.activePanelId).toBe(added.id);
  });
});

describe("negative: setActivePanel에 존재하지 않는 패널 id", () => {
  it("모델을 바꾸지 않고 onApplyView도 호출하지 않는다", () => {
    const { result, onApplyView } = setup();
    const originalActiveId = result.current.model.activePanelId;
    onApplyView.mockClear();

    act(() => result.current.setActivePanel("no-such-panel"));

    expect(result.current.model.activePanelId).toBe(originalActiveId);
    expect(onApplyView).not.toHaveBeenCalled();
  });
});

describe("negative: 활성 패널이 없을 때(패널 전부 제거 후) objectTree 갱신", () => {
  it("setObjectTreeOrder/setLockedIndicatorIds는 activePanelId가 null이면 조용히 거부한다(패널 없음)", () => {
    const emptyModel: ChartLayoutModel = { ...createEmptyLayoutModel(), panels: [], activePanelId: null };
    const { result } = setup(emptyModel);

    expect(result.current.objectTreeOrder).toEqual([]);
    act(() => result.current.setObjectTreeOrder(["RSI"]));
    act(() => result.current.setLockedIndicatorIds(["RSI"]));

    expect(result.current.model.panels).toHaveLength(0);
    expect(result.current.objectTreeOrder).toEqual([]);
    expect(result.current.lockedIndicatorIds).toEqual([]);
  });
});

describe("성공: 패널 제거", () => {
  it("활성 패널을 제거하면 남은 첫 패널이 활성이 되고 그 값으로 onApplyView가 호출된다", () => {
    const { result, onApplyView } = setup();
    act(() => result.current.addPanel());
    const [firstId, secondId] = result.current.model.panels.map((p) => p.id);
    expect(result.current.model.activePanelId).toBe(secondId);

    onApplyView.mockClear();
    act(() => result.current.removePanel(secondId));

    expect(result.current.model.panels.map((p) => p.id)).toEqual([firstId]);
    expect(result.current.model.activePanelId).toBe(firstId);
    expect(onApplyView).toHaveBeenCalledWith(expect.objectContaining({ instrumentId: BASE_VIEW.instrumentId }));
  });
});
