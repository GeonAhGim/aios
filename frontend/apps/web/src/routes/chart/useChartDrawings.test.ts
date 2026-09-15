import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { toDrawingsDocument } from "@aios/chart-engine/src/drawings/serialize";
import type { DrawingCollection } from "@aios/chart-engine/src/drawings/model";
import { saveDrawings, type ChartingPort, type ChartingDrawingsRecord } from "@aios/chart-engine/src/layout/persistence";
import { useChartDrawings, type UseChartDrawingsOptions } from "./useChartDrawings";
import { perfBudgetMs } from "../../test/perfBudget";

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

// DEEPEN task-3110 (DEPTH_CH task-2729 감사 #2101 보강): CH-4c(2ef3c544)가 도입한
// lastSyncedRef 스킵 로직은 "서버와 일치함이 확인된 상태"에서만 PUT을 생략해야 한다.
// 원 리프의 신규 2건은 그 happy-path(무변경 생략·중복 PUT 생략)만 증명했고, 스킵 로직이
// 실패·미동기 상태를 "일치"로 착각하지 않는지는 검증하지 않았다 — 아래 3건이 그 신규
// negative-path를 채운다.
describe("CH-4c negative: 실패한 저장 뒤에는 스킵하지 않는다(실패 주입)", () => {
  it("PUT이 예외(네트워크 등)로 실패하면 lastSyncedRef를 갱신하지 않아, 동일(변경 없는) 도형으로 재시도하면 다시 PUT을 보낸다", async () => {
    const err = new Error("network down");
    const putDrawings = vi
      .fn()
      .mockRejectedValueOnce(err)
      .mockImplementationOnce(async (layoutId: string, input) => ({
        layoutId,
        document: { schema_version: input.schemaVersion, drawings: input.drawings },
        revision: 1,
        updatedAt: "t1",
      }));
    const port = fakePort({ putDrawings });
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));

    act(() => result.current.setDrawingTool("horizontal-line"));
    act(() => result.current.handleAddDrawing(fakeCandle(0, "100")));

    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(result.current.saveStatus).toBe("error");
    expect(putDrawings).toHaveBeenCalledTimes(1);

    // 스킵 로직이 실패한 첫 시도를 "동기화됨"으로 착각한다면 여기서 putDrawings가
    // 다시 불리지 않는 버그가 될 것 — 실제로는 다시 불려야 한다.
    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(putDrawings).toHaveBeenCalledTimes(2);
    expect(result.current.saveStatus).toBe("idle");
  });

  it("PUT이 409로 실패(낙관적 잠금 충돌)해도 lastSyncedRef를 갱신하지 않아, 동일(변경 없는) 도형으로 재시도하면 다시 PUT을 보낸다", async () => {
    const putDrawings = vi
      .fn()
      .mockRejectedValueOnce(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT"))
      .mockImplementationOnce(async (layoutId: string, input) => ({
        layoutId,
        document: { schema_version: input.schemaVersion, drawings: input.drawings },
        revision: 1,
        updatedAt: "t1",
      }));
    const port = fakePort({ putDrawings });
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));

    act(() => result.current.setDrawingTool("horizontal-line"));
    act(() => result.current.handleAddDrawing(fakeCandle(0, "100")));

    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(result.current.saveStatus).toBe("conflict");
    expect(putDrawings).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(putDrawings).toHaveBeenCalledTimes(2);
    expect(result.current.saveStatus).toBe("idle");
  });
});

describe("CH-4c negative: 최초 동기화 이전에는 빈 도형도 스킵하지 않는다", () => {
  it("restore가 not_found로 끝난(한 번도 저장 안 한/교차 테넌트) layoutId에 대해 persist를 호출하면, 그린 도형이 없어도 lastSyncedRef가 null이라 putDrawings를 보낸다", async () => {
    const putDrawings = vi.fn(async (layoutId: string, input) => ({
      layoutId,
      document: { schema_version: input.schemaVersion, drawings: input.drawings },
      revision: 1,
      updatedAt: "t1",
    }));
    const port = fakePort({
      getDrawings: vi.fn().mockRejectedValue(apiErrorLike(404, "RESOURCE_NOT_FOUND")),
      putDrawings,
    });
    const { result } = setup(port, { layoutId: "someone-elses-layout" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("not_found"));
    expect(result.current.drawings).toHaveLength(0);

    await act(async () => {
      await result.current.persist("someone-elses-layout");
    });

    expect(putDrawings).toHaveBeenCalledTimes(1);
  });
});

// 게이트 적색 재현(task-3110, DEPTH_CH task-2729 감사 #2101 보강): CH-4c(2ef3c544)
// 이전에는 persist()가 serializeDrawings로 마지막 동기화 상태와 비교하지 않고 매번
// 무조건 saveDrawings를 호출했다(CH-4b, a4269edc). naivePersist는 그 이전 로직을 그대로
// 재구현한 것 — 동일한 "서버와 이미 일치하는 도형"을 그대로 다시 저장해도 매번 PUT을
// 보낸다. 동일 서버 상태·동일 도형 입력에서 real(useChartDrawings.persist)은 생략함을
// 대조해, 스킵 로직을 되돌리면 이 테스트가 실제로 FAIL함을 파일 안에서 증명한다.
async function naivePersist(
  port: ChartingPort,
  layoutId: string,
  expectedRevision: number,
  drawings: DrawingCollection,
): Promise<void> {
  await saveDrawings(port, layoutId, expectedRevision, drawings);
}

describe("CH-4c 게이트 적색 재현: 스킵 로직을 되돌리면 이 대조가 적발한다", () => {
  it("동일한(변경 없는) 도형·리비전에서 naive(CH-4c 이전)는 매번 PUT을 보내고, real은 이미 동기화됨을 인지해 생략한다", async () => {
    const syncedDrawing = { id: "d1", kind: "horizontal-line" as const, price: 50000 };
    const record = drawingsRecord("layout-1", {
      document: { schema_version: 1, drawings: [syncedDrawing] },
      revision: 3,
    });

    const naivePutDrawings = vi.fn(async (layoutId: string, input) => ({
      layoutId,
      document: { schema_version: input.schemaVersion, drawings: input.drawings },
      revision: 4,
      updatedAt: "t1",
    }));
    const naivePort = fakePort({ getDrawings: vi.fn(async () => record), putDrawings: naivePutDrawings });
    await naivePersist(naivePort, "layout-1", 3, [syncedDrawing]);
    // naive(되돌린 상태)에서는 여기서 이미 실패를 놓친다 — 변경이 없어도 PUT이 나간다.
    expect(naivePutDrawings).toHaveBeenCalledTimes(1);

    const realPutDrawings = vi.fn();
    const realPort = fakePort({ getDrawings: vi.fn(async () => record), putDrawings: realPutDrawings });
    const { result } = setup(realPort, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));

    await act(async () => {
      await result.current.persist("layout-1");
    });
    // 동일 시나리오에서 real은 생략한다 — 이 대조가 되돌림을 실제로 적발함을 증명.
    expect(realPutDrawings).not.toHaveBeenCalled();
  });
});

// 수치 성능 단언(DEEPEN task-3099, DEPTH_CH task-2729 감사 #2012 보강): CH-4b는
// addDrawing/removeDrawing(순수 모델)을 그대로 재사용하지만, 이 훅 자신의 persist()
// 경로(그린 도형 수만큼 serializeDrawings 재계산 + putDrawings 페이로드 조립)가 도형
// 수에 비례해 감당 못 할 정도로 느려지는 회귀(예: O(n^2) 직렬화)가 없는지 실측 상한을
// 둔다. jsdom 유닛테스트 시간이라 느슨한 예산(1초)이지만, 그런 회귀가 생기면 확실히 넘는다.
describe("성능 단언", () => {
  it("도형 300개를 그리고 저장하는 것이 1초 안에 끝난다", async () => {
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

    const startedAt = performance.now();
    act(() => {
      for (let i = 0; i < 300; i += 1) {
        result.current.handleAddDrawing(fakeCandle(i * 3_600_000, String(100 + i)));
      }
    });
    expect(result.current.drawings).toHaveLength(300);

    await act(async () => {
      await result.current.persist("layout-1");
    });
    const elapsedMs = performance.now() - startedAt;

    expect(port.putDrawings).toHaveBeenCalledTimes(1);
    expect(elapsedMs).toBeLessThan(perfBudgetMs(1000));
  });

  // CH-4c 전용 수치 성능 단언(task-3110): 위 테스트는 draw+최초 persist(실제 PUT 1회)
  // 경로만 잰다. 이 테스트는 CH-4c가 새로 추가한 비교 자체(persist마다 도형 500개를
  // serializeDrawings로 재인코딩해 lastSyncedRef와 문자열 비교) 를 50회 반복해도
  // 비용이 도형 수·반복 횟수에 못 감당할 정도로 불어나지 않는지(예: 매 비교마다 서버에
  // 확인 요청을 보내는 회귀) 실측한다.
  it("도형 500개를 저장한 뒤 변경 없이 persist를 50회 반복해도 예산 안에 끝나고 putDrawings는 처음 1회만 나간다", async () => {
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
    act(() => {
      for (let i = 0; i < 500; i += 1) {
        result.current.handleAddDrawing(fakeCandle(i * 3_600_000, String(100 + i)));
      }
    });
    expect(result.current.drawings).toHaveLength(500);

    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(port.putDrawings).toHaveBeenCalledTimes(1);

    const startedAt = performance.now();
    for (let i = 0; i < 50; i += 1) {
      await act(async () => {
        await result.current.persist("layout-1");
      });
    }
    const elapsedMs = performance.now() - startedAt;

    expect(port.putDrawings).toHaveBeenCalledTimes(1);
    expect(elapsedMs).toBeLessThan(perfBudgetMs(500));
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
