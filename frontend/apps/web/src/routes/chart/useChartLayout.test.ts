import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createEmptyLayoutModel, encodeLayoutModel, type ChartLayoutModel } from "@aios/chart-engine/src/layout/layoutModel";
import type { ChartingLayoutRecord, ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import { useChartLayout, type ChartViewSnapshot, type UseChartLayoutOptions } from "./useChartLayout";

// persistence.test.ts와 동일 관용: routeApiError는 statusCode/errorCode 덕타이핑으로
// 분류하므로(errorRouting.ts) api-client의 ApiError를 임포트할 필요가 없다.
function apiErrorLike(statusCode: number, errorCode: string): Error & { statusCode: number; errorCode: string } {
  return Object.assign(new Error(errorCode), { statusCode, errorCode });
}

function fakePort(overrides: Partial<ChartingPort> = {}): ChartingPort {
  return {
    createLayout: vi.fn(),
    listLayouts: vi.fn(async () => []),
    getLayout: vi.fn(),
    updateLayout: vi.fn(),
    deleteLayout: vi.fn(),
    getDrawings: vi.fn(),
    putDrawings: vi.fn(),
    ...overrides,
  };
}

function layoutRecord(model: ChartLayoutModel, overrides: Partial<ChartingLayoutRecord> = {}): ChartingLayoutRecord {
  return {
    id: "layout-1",
    name: "저장된 레이아웃",
    layoutState: encodeLayoutModel(model),
    revision: 1,
    updatedAt: "2026-09-06T00:00:00Z",
    ...overrides,
  };
}

const BASE_VIEW: ChartViewSnapshot = {
  instrumentId: "BTCUSDT",
  venue: "BITGET",
  timeframe: "1h",
  indicatorIds: [],
  compareSymbolIds: [],
};

function savedModelFor(view: ChartViewSnapshot): ChartLayoutModel {
  const panel = {
    id: "server-panel",
    instrument: { instrumentId: view.instrumentId, venue: view.venue, symbol: view.instrumentId },
    timeframe: view.timeframe,
    indicators: [
      ...view.indicatorIds.map((id) => ({ id })),
      ...view.compareSymbolIds.map((id) => ({ id: `compare:${id}` })),
    ],
    drawingSetId: "server-panel",
  };
  return { ...createEmptyLayoutModel(), panels: [panel], activePanelId: panel.id };
}

function setup(port: ChartingPort, view: ChartViewSnapshot = BASE_VIEW, onApplyView = vi.fn()) {
  return renderHook((props: UseChartLayoutOptions) => useChartLayout(props), {
    initialProps: { port, enabled: true, view, onApplyView },
  });
}

describe("복원", () => {
  it("저장된 레이아웃이 없으면 기본 1패널로 시작하고 현재 뷰를 그대로 반영한다", async () => {
    const onApplyView = vi.fn();
    const port = fakePort();
    const { result } = setup(port, BASE_VIEW, onApplyView);

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.model.panels).toHaveLength(1);
    expect(onApplyView).toHaveBeenCalledWith(BASE_VIEW);
  });

  it("저장된 레이아웃이 있으면 그 활성 패널을 화면에 적용한다", async () => {
    const remoteView: ChartViewSnapshot = {
      instrumentId: "ETHUSDT",
      venue: "BITGET",
      timeframe: "4h",
      indicatorIds: ["SMA"],
      compareSymbolIds: ["BITGET:ETHUSDT"],
    };
    const record = layoutRecord(savedModelFor(remoteView));
    const onApplyView = vi.fn();
    const port = fakePort({ listLayouts: vi.fn(async () => [record]) });
    const { result } = setup(port, BASE_VIEW, onApplyView);

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(onApplyView).toHaveBeenCalledWith(remoteView);
    expect(result.current.layoutName).toBe("저장된 레이아웃");
  });

  it("negative: 복원이 5xx로 실패하면 restore_failed를 유지하고, retryRestore로 회복한다", async () => {
    const err = apiErrorLike(500, "INTERNAL_ERROR");
    const port = fakePort({ listLayouts: vi.fn().mockRejectedValueOnce(err).mockResolvedValueOnce([]) });
    const { result } = setup(port);

    await waitFor(() => expect(result.current.status).toBe("restore_failed"));
    expect(result.current.restoreError).toBe(err);

    act(() => result.current.retryRestore());
    await waitFor(() => expect(result.current.status).toBe("ready"));
  });
});

describe("뷰 → 모델 거울 반영", () => {
  it("타임프레임·지표 선택이 바뀌면 활성 패널에 반영되고 dirty가 된다", async () => {
    const onApplyView = vi.fn();
    const port = fakePort();
    const { result, rerender } = setup(port, BASE_VIEW, onApplyView);
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.isDirty).toBe(false);

    const nextView: ChartViewSnapshot = { ...BASE_VIEW, timeframe: "4h", indicatorIds: ["SMA"] };
    rerender({ port, enabled: true, view: nextView, onApplyView });

    await waitFor(() => {
      const active = result.current.model.panels.find((p) => p.id === result.current.model.activePanelId);
      expect(active?.timeframe).toBe("4h");
      expect(active?.indicators.map((i) => i.id)).toEqual(["SMA"]);
    });
    expect(result.current.isDirty).toBe(true);
  });
});

describe("복원 → 편집 → 저장 왕복", () => {
  it("최초 저장은 createLayout, 이후 저장은 updateLayout으로 간다(layoutId 확보 증명)", async () => {
    const port = fakePort();
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => result.current.addPanel());
    expect(result.current.model.panels).toHaveLength(2);
    expect(result.current.isDirty).toBe(true);

    let saved: ChartingLayoutRecord | undefined;
    port.createLayout = vi.fn(async (input) => {
      saved = { id: "layout-1", name: input.name, layoutState: input.layoutState, revision: 0, updatedAt: "t0" };
      return saved;
    });
    await act(async () => {
      await result.current.save();
    });
    expect(port.createLayout).toHaveBeenCalledTimes(1);
    expect(result.current.saveStatus).toBe("idle");
    expect(result.current.isDirty).toBe(false);

    port.updateLayout = vi.fn(async (id, input) => ({
      id,
      name: input.name ?? saved!.name,
      layoutState: input.layoutState ?? saved!.layoutState,
      revision: (input.expectedRevision ?? 0) + 1,
      updatedAt: "t1",
    }));
    act(() => result.current.rename("이름변경"));
    await act(async () => {
      await result.current.save();
    });
    expect(port.updateLayout).toHaveBeenCalledWith("layout-1", expect.objectContaining({ name: "이름변경" }));
    expect(port.createLayout).toHaveBeenCalledTimes(1);
  });
});

describe("409 충돌: 자동 덮어쓰기 없이 배너로 안내", () => {
  it("저장 중 충돌하면 saveStatus가 conflict가 되고, reload로만 최신 내용을 되불러온다", async () => {
    const record = layoutRecord(savedModelFor(BASE_VIEW));
    const port = fakePort({
      listLayouts: vi.fn(async () => [record]),
      updateLayout: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")),
      getLayout: vi.fn(async () => ({ ...record, revision: 2 })),
    });
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));

    await act(async () => {
      await result.current.save();
    });
    expect(result.current.saveStatus).toBe("conflict");
    // 충돌 상태에서 재저장을 자동으로 시도하지 않는다 — 명시적 reload만 상태를 바꾼다.
    expect(port.getLayout).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.reload();
    });
    expect(port.getLayout).toHaveBeenCalledWith("layout-1");
    expect(result.current.saveStatus).toBe("idle");
  });
});

describe("negative: 복원 실패(404) → 기본 레이아웃", () => {
  it("reload 중 서버에서 레이아웃이 사라졌으면(404) 에러 없이 기본 레이아웃으로 되돌아간다", async () => {
    const record = layoutRecord(savedModelFor(BASE_VIEW));
    const port = fakePort({
      listLayouts: vi.fn(async () => [record]),
      getLayout: vi.fn().mockRejectedValue(apiErrorLike(404, "RESOURCE_NOT_FOUND")),
    });
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.model.panels[0]!.id).toBe("server-panel");

    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.saveStatus).toBe("idle");
    expect(result.current.model.panels[0]!.id).not.toBe("server-panel");

    // 되돌아간 기본 레이아웃엔 layoutId가 없으므로 다음 저장은 createLayout으로 간다.
    port.createLayout = vi.fn(async (input) => ({ id: "new-1", name: input.name, layoutState: input.layoutState, revision: 0, updatedAt: "t" }));
    await act(async () => {
      await result.current.save();
    });
    expect(port.createLayout).toHaveBeenCalledTimes(1);
  });
});

describe("패널 추가·제거·전환", () => {
  it("패널 전환은 onApplyView를 그 패널 값으로 즉시 호출한다", async () => {
    const onApplyView = vi.fn();
    const port = fakePort();
    const { result } = setup(port, BASE_VIEW, onApplyView);
    await waitFor(() => expect(result.current.status).toBe("ready"));
    const firstId = result.current.model.panels[0]!.id;

    act(() => result.current.addPanel());
    expect(result.current.model.panels).toHaveLength(2);

    onApplyView.mockClear();
    act(() => result.current.setActivePanel(firstId));
    expect(result.current.model.activePanelId).toBe(firstId);
    expect(onApplyView).toHaveBeenCalledWith(expect.objectContaining({ instrumentId: BASE_VIEW.instrumentId }));
  });

  it("negative: 마지막 패널을 제거하면 activePanelId가 null이 된다", async () => {
    const port = fakePort();
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));
    const onlyId = result.current.model.panels[0]!.id;

    act(() => result.current.removePanel(onlyId));
    expect(result.current.model.panels).toHaveLength(0);
    expect(result.current.model.activePanelId).toBeNull();
  });
});

describe("CH-16b: 오브젝트 트리 순서·잠금", () => {
  it("setObjectTreeOrder/setLockedIndicatorIds는 활성 패널에 반영되고 dirty가 된다", async () => {
    const port = fakePort();
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.objectTreeOrder).toEqual([]);
    expect(result.current.lockedIndicatorIds).toEqual([]);

    act(() => result.current.setObjectTreeOrder(["RSI", "SMA"]));
    expect(result.current.objectTreeOrder).toEqual(["RSI", "SMA"]);
    expect(result.current.isDirty).toBe(true);

    act(() => result.current.setLockedIndicatorIds(["SMA"]));
    expect(result.current.lockedIndicatorIds).toEqual(["SMA"]);

    const activeId = result.current.model.activePanelId;
    const active = result.current.model.panels.find((p) => p.id === activeId);
    expect(active?.objectTreeOrder).toEqual(["RSI", "SMA"]);
    expect(active?.lockedIndicatorIds).toEqual(["SMA"]);
  });

  it("복원된 레이아웃의 objectTreeOrder/lockedIndicatorIds를 그대로 노출한다", async () => {
    const model = savedModelFor(BASE_VIEW);
    const withObjectTree: ChartLayoutModel = {
      ...model,
      panels: model.panels.map((p) => ({ ...p, objectTreeOrder: ["SMA"], lockedIndicatorIds: ["SMA"] })),
    };
    const record = layoutRecord(withObjectTree);
    const port = fakePort({ listLayouts: vi.fn(async () => [record]) });
    const { result } = setup(port);

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.objectTreeOrder).toEqual(["SMA"]);
    expect(result.current.lockedIndicatorIds).toEqual(["SMA"]);
  });
});

describe("워치리스트 변경", () => {
  it("toggleWatchlistEntry를 두 번 호출하면 추가 후 제거된다", async () => {
    const port = fakePort();
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));
    const entry = { instrumentId: "BTCUSDT", venue: "BITGET", symbol: "BTCUSDT" };

    act(() => result.current.toggleWatchlistEntry(entry));
    expect(result.current.model.watchlists[0]!.entries).toHaveLength(1);

    act(() => result.current.toggleWatchlistEntry(entry));
    expect(result.current.model.watchlists[0]!.entries).toHaveLength(0);
  });
});
