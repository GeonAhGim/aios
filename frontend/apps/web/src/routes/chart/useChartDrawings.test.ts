import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { toDrawingsDocument } from "@aios/chart-engine/src/drawings/serialize";
import type { ChartingPort, ChartingDrawingsRecord } from "@aios/chart-engine/src/layout/persistence";
import { useChartDrawings, type UseChartDrawingsOptions } from "./useChartDrawings";

// persistence.test.ts/useChartLayout.test.ts와 동일 관용: routeApiError는
// statusCode/errorCode 덕타이핑으로 분류하므로 ApiError 클래스를 임포트할 필요가 없다.
function apiErrorLike(statusCode: number, errorCode: string): Error & { statusCode: number; errorCode: string } {
  return Object.assign(new Error(errorCode), { statusCode, errorCode });
}

function fakePort(overrides: Partial<ChartingPort> = {}): ChartingPort {
  return {
    createLayout: vi.fn(),
    listLayouts: vi.fn(),
    getLayout: vi.fn(),
    updateLayout: vi.fn(),
    deleteLayout: vi.fn(),
    // 기본값: 빈 도형 문서(레이아웃 생성 시 서버가 항상 함께 만드는 초기 상태와 동일) —
    // persist()만 검증하는 테스트가 restore 단계에서 불필요하게 실패하지 않게 한다.
    getDrawings: vi.fn(async (layoutId: string) => drawingsRecord(layoutId)),
    putDrawings: vi.fn(),
    ...overrides,
  };
}

function fakeCandle(openTimeMs: number, close: string): StreamCandle {
  return {
    openTimeMs,
    confirmed: true,
    record: {
      key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" },
      open_time: new Date(openTimeMs).toISOString(),
      close_time: new Date(openTimeMs + 3_600_000).toISOString(),
      open: close,
      high: close,
      low: close,
      close,
      volume: "1",
      quote_volume: "1",
    },
  };
}

function drawingsRecord(layoutId: string, overrides: Partial<ChartingDrawingsRecord> = {}): ChartingDrawingsRecord {
  return {
    layoutId,
    document: { schema_version: 1, drawings: [] },
    revision: 0,
    updatedAt: "2026-09-07T00:00:00Z",
    ...overrides,
  };
}

function setup(port: ChartingPort, options: Partial<UseChartDrawingsOptions> = {}) {
  return renderHook(
    (opts: UseChartDrawingsOptions) =>
      useChartDrawings("BITGET", "BTCUSDT", "1h", opts),
    { initialProps: { port, layoutId: null, layoutStatus: "ready", ...options } },
  );
}

describe("복원", () => {
  it("layoutId가 없는(아직 한 번도 저장 안 한) 레이아웃이면 서버를 부르지 않고 ready가 된다", async () => {
    const port = fakePort();
    const { result } = setup(port);

    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));
    expect(port.getDrawings).not.toHaveBeenCalled();
    expect(result.current.drawings).toHaveLength(0);
  });

  it("layoutId가 있으면(복원된 레이아웃) 그 도형 세트를 실제로 불러온다(실측: getDrawings 호출)", async () => {
    const record = drawingsRecord("layout-1", {
      document: {
        schema_version: 1,
        drawings: [{ id: "d1", kind: "horizontal-line", price: 50000 }],
      },
      revision: 3,
    });
    const port = fakePort({ getDrawings: vi.fn(async () => record) });
    const { result } = setup(port, { layoutId: "layout-1" });

    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));
    expect(port.getDrawings).toHaveBeenCalledWith("layout-1");
    expect(result.current.drawings).toEqual([{ id: "d1", kind: "horizontal-line", price: 50000 }]);
  });

  it("negative: 교차 테넌트·미존재 layout_id(404)는 빈 목록으로 뭉개지 않고 not_found로 표면화한다", async () => {
    const port = fakePort({ getDrawings: vi.fn().mockRejectedValue(apiErrorLike(404, "RESOURCE_NOT_FOUND")) });
    const { result } = setup(port, { layoutId: "someone-elses-layout" });

    await waitFor(() => expect(result.current.restoreStatus).toBe("not_found"));
    expect(result.current.drawings).toHaveLength(0);
  });

  it("negative: 5xx는 restore_failed를 유지하고 raw error를 보존하며, retryRestore로 회복한다", async () => {
    const err = apiErrorLike(500, "SYSTEM_INTERNAL_ERROR");
    const port = fakePort({
      getDrawings: vi.fn().mockRejectedValueOnce(err).mockResolvedValueOnce(drawingsRecord("layout-1")),
    });
    const { result } = setup(port, { layoutId: "layout-1" });

    await waitFor(() => expect(result.current.restoreStatus).toBe("restore_failed"));
    expect(result.current.restoreError).toBe(err);

    act(() => result.current.retryRestore());
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));
  });
});

describe("그리기 → 저장 → (재마운트) 복원 왕복", () => {
  it("배선 증명: 3종(추세선·수평선·피보나치)을 그린 뒤 저장하면 putDrawings가 실제로 인코딩된 페이로드로 호출되고, 그 페이로드를 그대로 되돌리면 좌표·스타일까지 동일하게 복원된다", async () => {
    let stored: ChartingDrawingsRecord | null = null;
    const port = fakePort({
      putDrawings: vi.fn(async (layoutId: string, input) => {
        stored = { layoutId, document: { schema_version: input.schemaVersion, drawings: input.drawings }, revision: 1, updatedAt: "t1" };
        return stored;
      }),
    });

    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));

    act(() => result.current.setDrawingTool("trendline"));
    act(() => result.current.handleAddDrawing(fakeCandle(0, "100")));
    act(() => result.current.setDrawingTool("horizontal-line"));
    act(() => result.current.handleAddDrawing(fakeCandle(3_600_000, "200")));
    act(() => result.current.setDrawingTool("fibonacci"));
    act(() => result.current.handleAddDrawing(fakeCandle(7_200_000, "300")));
    expect(result.current.drawings).toHaveLength(3);
    const drawn = result.current.drawings;

    await act(async () => {
      await result.current.persist("layout-1");
    });

    // 동어반복 목 금지: putDrawings에 실제로 전달된 페이로드(수기 값이 아니라 그린
    // 도형을 CH-4 toDrawingsDocument로 인코딩한 것과 동일)를 검증한다.
    const expectedDoc = toDrawingsDocument(drawn);
    expect(port.putDrawings).toHaveBeenCalledWith("layout-1", {
      expectedRevision: 0,
      schemaVersion: expectedDoc.schema_version,
      drawings: expectedDoc.drawings,
    });
    expect(result.current.saveStatus).toBe("idle");

    // 컴포넌트 재마운트를 시뮬레이션: 새 훅 인스턴스가 getDrawings로 "stored"(방금 그
    // putDrawings 호출이 실제로 담아 보낸 페이로드)를 그대로 돌려받는다 — 서버 왕복.
    const remountPort = fakePort({ getDrawings: vi.fn(async () => stored!) });
    const remounted = setup(remountPort, { layoutId: "layout-1" });
    await waitFor(() => expect(remounted.result.current.restoreStatus).toBe("ready"));

    expect(remounted.result.current.drawings).toEqual(drawn);
    const byKind = Object.fromEntries(remounted.result.current.drawings.map((d) => [d.kind, d]));
    expect(byKind.trendline).toEqual(drawn.find((d) => d.kind === "trendline"));
    expect(byKind["horizontal-line"]).toEqual(drawn.find((d) => d.kind === "horizontal-line"));
    expect(byKind.fibonacci).toEqual(drawn.find((d) => d.kind === "fibonacci"));
  });
});

describe("CH-4c: 변경 없는 저장은 chart-engine 직렬화기로 생략한다", () => {
  it("복원 직후(그리기 없음) persist를 호출해도 putDrawings를 부르지 않는다", async () => {
    const port = fakePort();
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));

    await act(async () => {
      await result.current.persist("layout-1");
    });

    expect(port.putDrawings).not.toHaveBeenCalled();
    expect(result.current.saveStatus).toBe("idle");
  });

  it("실측: 그린 뒤 저장하고, 아무것도 바꾸지 않은 채 '레이아웃 저장'을 다시 눌러도(persist 재호출) putDrawings는 처음 한 번만 나간다", async () => {
    const port = fakePort({
      putDrawings: vi.fn(async (layoutId: string, input) => ({
        layoutId,
        document: { schema_version: input.schemaVersion, drawings: input.drawings },
        revision: 1,
        updatedAt: "t1",
      })),
    });
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));

    act(() => result.current.setDrawingTool("horizontal-line"));
    act(() => result.current.handleAddDrawing(fakeCandle(0, "100")));

    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(port.putDrawings).toHaveBeenCalledTimes(1);

    // chartToolbarLayoutProps.ts chains persist() onto every "레이아웃 저장" click,
    // even ones that touched no drawing — this proves the second call is a real no-op.
    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(port.putDrawings).toHaveBeenCalledTimes(1);
    expect(result.current.saveStatus).toBe("idle");
  });
});

describe("409 충돌: 낙관적 잠금", () => {
  it("PUT이 409로 실패하면 로컬 도형을 지우지 않고 saveStatus를 conflict로 표면화한다", async () => {
    const port = fakePort({ putDrawings: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")) });
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));

    act(() => result.current.setDrawingTool("horizontal-line"));
    act(() => result.current.handleAddDrawing(fakeCandle(0, "100")));
    const drawnBeforeConflict = result.current.drawings;
    expect(drawnBeforeConflict).toHaveLength(1);

    await act(async () => {
      await result.current.persist("layout-1");
    });

    expect(result.current.saveStatus).toBe("conflict");
    // negative: 조용히 삼키거나 로컬 상태를 비우지 않는다.
    expect(result.current.drawings).toEqual(drawnBeforeConflict);
  });
});

describe("negative: 저장 대상 layout_id가 사라짐(404)", () => {
  it("PUT이 404로 실패해도 로컬 도형을 비우지 않고 not_found로 표면화한다", async () => {
    const port = fakePort({ putDrawings: vi.fn().mockRejectedValue(apiErrorLike(404, "RESOURCE_NOT_FOUND")) });
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));

    act(() => result.current.setDrawingTool("vertical-line"));
    act(() => result.current.handleAddDrawing(fakeCandle(0, "100")));
    const drawnBeforeError = result.current.drawings;

    await act(async () => {
      await result.current.persist("layout-1");
    });

    expect(result.current.saveStatus).toBe("not_found");
    expect(result.current.drawings).toEqual(drawnBeforeError);
  });
});
