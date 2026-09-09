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
