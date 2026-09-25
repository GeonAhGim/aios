import { describe, expect, it, vi } from "vitest";
import { createEmptyLayoutModel, type ChartLayoutModel } from "@aios/chart-engine/src/layout/layoutModel";
import { buildChartLayoutControls } from "./chartToolbarLayoutProps";
import type { UseChartLayoutResult } from "./useChartLayout";

function modelWithPanels(overrides: Partial<ChartLayoutModel> = {}): ChartLayoutModel {
  return {
    ...createEmptyLayoutModel(),
    panels: [
      {
        id: "p1",
        instrument: { instrumentId: "BTCUSDT", venue: "BITGET", symbol: "BTCUSDT" },
        timeframe: "1h",
        indicators: [],
        drawingSetId: "p1",
      },
    ],
    activePanelId: "p1",
    ...overrides,
  };
}

function baseLayout(overrides: Partial<UseChartLayoutResult> = {}): UseChartLayoutResult {
  return {
    status: "ready",
    restoreError: null,
    retryRestore: vi.fn(),
    model: modelWithPanels(),
    layoutId: null,
    layoutName: "기본 레이아웃",
    isDirty: false,
    saveStatus: "idle",
    saveError: null,
    save: vi.fn(async () => null),
    reload: vi.fn(async () => {}),
    rename: vi.fn(),
    remove: vi.fn(async () => {}),
    addPanel: vi.fn(),
    removePanel: vi.fn(),
    setActivePanel: vi.fn(),
    toggleWatchlistEntry: vi.fn(),
    objectTreeOrder: [],
    lockedIndicatorIds: [],
    setObjectTreeOrder: vi.fn(),
    setLockedIndicatorIds: vi.fn(),
    ...overrides,
  };
}

describe("buildChartLayoutControls 필드 매핑", () => {
  it("name·saveStatus·activePanelId를 layout에서 그대로 옮기고, panels는 라벨을 조립한다", () => {
    const controls = buildChartLayoutControls(baseLayout(), "BTCUSDT", "BITGET", vi.fn());

    expect(controls.name).toBe("기본 레이아웃");
    expect(controls.saveStatus).toBe("idle");
    expect(controls.activePanelId).toBe("p1");
    expect(controls.panels).toEqual([{ id: "p1", label: "BTCUSDT · 1h" }]);
  });

  it("onNameChange/onReload/onSelectPanel/onAddPanel은 layout의 대응 함수로 그대로 위임한다", () => {
    const rename = vi.fn();
    const reload = vi.fn(async () => {});
    const setActivePanel = vi.fn();
    const addPanel = vi.fn();
    const controls = buildChartLayoutControls(
      baseLayout({ rename, reload, setActivePanel, addPanel }),
      "BTCUSDT",
      "BITGET",
      vi.fn(),
    );

    controls.onNameChange("새 이름");
    controls.onReload();
    controls.onSelectPanel("p2");
    controls.onAddPanel();

    expect(rename).toHaveBeenCalledWith("새 이름");
    expect(reload).toHaveBeenCalledTimes(1);
    expect(setActivePanel).toHaveBeenCalledWith("p2");
    expect(addPanel).toHaveBeenCalledTimes(1);
  });
});

describe("buildChartLayoutControls 저장 도형 배선(CH-4b)", () => {
  it("layout.save가 layoutId를 반환하면 그 id로 persistDrawings를 호출한다", async () => {
    const save = vi.fn(async () => "layout-9");
    const persistDrawings = vi.fn(async () => {});
    const controls = buildChartLayoutControls(baseLayout({ save }), "BTCUSDT", "BITGET", persistDrawings);

    controls.onSave();
    await Promise.resolve();
    await Promise.resolve();

    expect(save).toHaveBeenCalledTimes(1);
    expect(persistDrawings).toHaveBeenCalledWith("layout-9");
  });

  it("layout.save가 null을 반환하면(저장 실패) persistDrawings를 호출하지 않는다", async () => {
    const save = vi.fn(async () => null);
    const persistDrawings = vi.fn(async () => {});
    const controls = buildChartLayoutControls(baseLayout({ save }), "BTCUSDT", "BITGET", persistDrawings);

    controls.onSave();
    await Promise.resolve();
    await Promise.resolve();

    expect(persistDrawings).not.toHaveBeenCalled();
  });
});

// 게이트 적색 재현(DEEPEN task-3099, DEPTH_CH task-2729 감사 #2012 보강): task-2012
// (a4269edc) 커밋 메시지는 "onSave 체이닝을 되돌리면 CH-4b 통합 테스트 3건 중 2건이
// FAIL한다"고 주장했지만, 그 되돌린 상태를 실제로 구현해 대조 검증한 파일 내 증거가
// 없어 DEPTH 감사에서 재현 불인정 판정을 받았다. 아래 naive는 CH-4b(a4269edc) 이전
// 상태 — onSave가 layout.save()만 부르고 그 결과 layoutId로 persistDrawings를 체이닝
// 하지 않는 코드 — 를 그대로 재구현한 것이다. real(buildChartLayoutControls)과 동일한
// 입력에서 naive는 persistDrawings를 절대 호출하지 않음을 직접 단언해, "체이닝을
// 되돌리면 실패한다"는 주장을 이 파일 안에서 재현한다.
function buildNaiveOnSave(layout: UseChartLayoutResult): () => void {
  return () => {
    void layout.save();
  };
}

describe("buildChartLayoutControls 저장 도형 배선(CH-4b) — 게이트 적색 재현", () => {
  it("naive(체이닝을 되돌린) onSave는 save가 layoutId를 반환해도 persistDrawings를 호출하지 않는다 — real은 호출한다", async () => {
    const save = vi.fn(async () => "layout-9");
    const persistDrawings = vi.fn(async () => {});
    const layout = baseLayout({ save });

    const naiveOnSave = buildNaiveOnSave(layout);
    naiveOnSave();
    await Promise.resolve();
    await Promise.resolve();
    // naive(되돌린 상태)에서는 여기서 이미 실패를 놓친다 — persistDrawings 미호출.
    expect(persistDrawings).not.toHaveBeenCalled();

    const realControls = buildChartLayoutControls(layout, "BTCUSDT", "BITGET", persistDrawings);
    realControls.onSave();
    await Promise.resolve();
    await Promise.resolve();
    // 동일 save/layoutId 조합에서 real은 호출한다 — 이 대조가 되돌림을 실제로 적발함을 증명.
    expect(persistDrawings).toHaveBeenCalledWith("layout-9");
  });
});

describe("buildChartLayoutControls 패널 제거/삭제", () => {
  it("onDelete는 layout.remove를 호출한다", () => {
    const remove = vi.fn(async () => {});
    const controls = buildChartLayoutControls(baseLayout({ remove }), "BTCUSDT", "BITGET", vi.fn());

    controls.onDelete();
    expect(remove).toHaveBeenCalledTimes(1);
  });

  it("onRemovePanel은 activePanelId가 있으면 그 id로 removePanel을 호출한다", () => {
    const removePanel = vi.fn();
    const controls = buildChartLayoutControls(
      baseLayout({ model: modelWithPanels({ activePanelId: "p1" }), removePanel }),
      "BTCUSDT",
      "BITGET",
      vi.fn(),
    );

    controls.onRemovePanel();
    expect(removePanel).toHaveBeenCalledWith("p1");
  });

  it("onRemovePanel은 activePanelId가 null이면 removePanel을 호출하지 않는다", () => {
    const removePanel = vi.fn();
    const controls = buildChartLayoutControls(
      baseLayout({ model: modelWithPanels({ activePanelId: null }), removePanel }),
      "BTCUSDT",
      "BITGET",
      vi.fn(),
    );

    controls.onRemovePanel();
    expect(removePanel).not.toHaveBeenCalled();
  });
});

describe("buildChartLayoutControls 관심목록 배지(isWatchlisted)", () => {
  it("현재 심볼·거래소가 어느 관심목록에도 없으면 false다", () => {
    const controls = buildChartLayoutControls(baseLayout(), "BTCUSDT", "BITGET", vi.fn());
    expect(controls.isWatchlisted).toBe(false);
  });

  it("현재 심볼·거래소가 관심목록 항목과 일치하면 true다", () => {
    const model = modelWithPanels({
      watchlists: [
        {
          id: "w1",
          name: "기본",
          entries: [{ instrumentId: "BTCUSDT", venue: "BITGET", symbol: "BTCUSDT" }],
        },
      ],
    });
    const controls = buildChartLayoutControls(baseLayout({ model }), "BTCUSDT", "BITGET", vi.fn());
    expect(controls.isWatchlisted).toBe(true);
  });

  it("onToggleWatchlist는 현재 심볼·거래소로 toggleWatchlistEntry를 호출한다", () => {
    const toggleWatchlistEntry = vi.fn();
    const controls = buildChartLayoutControls(
      baseLayout({ toggleWatchlistEntry }),
      "BTCUSDT",
      "BITGET",
      vi.fn(),
    );

    controls.onToggleWatchlist();
    expect(toggleWatchlistEntry).toHaveBeenCalledWith({
      instrumentId: "BTCUSDT",
      venue: "BITGET",
      symbol: "BTCUSDT",
    });
  });
});
