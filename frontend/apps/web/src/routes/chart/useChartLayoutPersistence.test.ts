import { useRef } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createEmptyLayoutModel, encodeLayoutModel, type ChartLayoutModel } from "@aios/chart-engine/src/layout/layoutModel";
import type { ChartingLayoutRecord, ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import {
  useChartLayoutPersistence,
  type UseChartLayoutPersistenceOptions,
} from "./useChartLayoutPersistence";
import type { ChartViewSnapshot } from "./chartLayoutView";

// useChartLayout.test.ts와 동일 관용: routeApiError는 statusCode/errorCode 덕타이핑으로
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
    indicators: view.indicatorIds.map((id) => ({ id })),
    drawingSetId: "server-panel",
  };
  return { ...createEmptyLayoutModel(), panels: [panel], activePanelId: panel.id };
}

let idSeq = 0;
function genId(prefix: string): string {
  idSeq += 1;
  return `${prefix}-${idSeq}`;
}

type HarnessProps = Omit<UseChartLayoutPersistenceOptions, "onApplyViewRef" | "genId"> & {
  readonly onApplyView: (view: ChartViewSnapshot) => void;
};

// 훅 계약이 onApplyViewRef(MutableRefObject)를 요구한다 — useChartLayout.ts가
// 조립 시 하는 것과 동일하게, 이 얇은 하니스가 매 렌더 최신 콜백을 ref에 반영한다.
function setup(port: ChartingPort, view: ChartViewSnapshot = BASE_VIEW, onApplyView = vi.fn()) {
  idSeq = 0;
  return renderHook(
    (props: HarnessProps) => {
      const onApplyViewRef = useRef(props.onApplyView);
      onApplyViewRef.current = props.onApplyView;
      return useChartLayoutPersistence({ ...props, onApplyViewRef, genId });
    },
    { initialProps: { port, enabled: true, view, onApplyView } },
  );
}

describe("복원: 성공 경로", () => {
  it("저장된 레이아웃이 없으면 기본 1패널로 시작하고 현재 뷰를 화면에 반영한다", async () => {
    const onApplyView = vi.fn();
    const port = fakePort();
    const { result } = setup(port, BASE_VIEW, onApplyView);

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.model.panels).toHaveLength(1);
    expect(result.current.layoutId).toBeNull();
    expect(onApplyView).toHaveBeenCalledWith(BASE_VIEW);
  });
});

describe("negative: 409 STATE_CONCURRENCY_CONFLICT", () => {
  it("저장 중 충돌 응답이 오면 saveStatus가 conflict가 되고 모델은 서버 상태로 덮어써지지 않는다", async () => {
    const record = layoutRecord(savedModelFor(BASE_VIEW));
    const port = fakePort({
      listLayouts: vi.fn(async () => [record]),
      updateLayout: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")),
    });
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => result.current.rename("이름변경"));
    expect(result.current.isDirty).toBe(true);

    let resolved: string | null = "unset";
    await act(async () => {
      resolved = await result.current.save();
    });

    expect(resolved).toBeNull();
    expect(result.current.saveStatus).toBe("conflict");
    expect(result.current.saveError).toBeNull();
    // 충돌 시 로컬 편집(이름변경)을 서버 응답으로 덮어쓰지 않는다 — applySaved가 호출되지 않았다.
    expect(result.current.layoutName).toBe("이름변경");
    expect(result.current.layoutId).toBe("layout-1");
  });
});
