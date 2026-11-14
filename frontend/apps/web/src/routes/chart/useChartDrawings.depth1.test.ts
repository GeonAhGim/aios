import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { ChartingPort, ChartingDrawingsRecord } from "@aios/chart-engine/src/layout/persistence";
import { useChartDrawings, type UseChartDrawingsOptions } from "./useChartDrawings";
import { perfBudgetMs } from "../../test/perfBudget";

// CH-4c D2 depth supplement (task-25083): negative / failure-injection / perf / gate-red.
function apiErrorLike(statusCode: number, errorCode: string): Error & { statusCode: number; errorCode: string } {
  return Object.assign(new Error(errorCode), { statusCode, errorCode });
}

function record(layoutId: string, revision = 0): ChartingDrawingsRecord {
  return { layoutId, document: { schema_version: 1, drawings: [] }, revision, updatedAt: "2026-09-07T00:00:00Z" };
}

function fakePort(overrides: Partial<ChartingPort> = {}): ChartingPort {
  return {
    createLayout: vi.fn(),
    listLayouts: vi.fn(),
    getLayout: vi.fn(),
    updateLayout: vi.fn(),
    deleteLayout: vi.fn(),
    getDrawings: vi.fn(async (id: string) => record(id)),
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

function setup(port: ChartingPort, options: Partial<UseChartDrawingsOptions> = {}) {
  return renderHook(
    (opts: UseChartDrawingsOptions) => useChartDrawings("BITGET", "BTCUSDT", "1h", opts),
    { initialProps: { port, layoutId: null, layoutStatus: "ready", ...options } },
  );
}

async function drawN(result: { current: ReturnType<typeof useChartDrawings> }, n: number): Promise<void> {
  act(() => result.current.setDrawingTool("horizontal-line"));
  act(() => {
    for (let i = 0; i < n; i += 1) result.current.handleAddDrawing(fakeCandle(i * 3_600_000, String(100 + i)));
  });
}

describe("CH-4c negative", () => {
  it("negative: 레이아웃이 아직 ready가 아니면 getDrawings를 부르지 않는다", async () => {
    const port = fakePort();
    const { result } = setup(port, { layoutId: "layout-1", layoutStatus: "loading" });
    await act(async () => {});
    expect(port.getDrawings).not.toHaveBeenCalled();
    expect(result.current.restoreStatus).toBe("idle");
  });

  it("negative: 도구 없이 handleAddDrawing하면 도형이 추가되지 않는다", async () => {
    const { result } = setup(fakePort());
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));
    act(() => result.current.handleAddDrawing(fakeCandle(0, "100")));
    expect(result.current.drawings).toHaveLength(0);
  });

  it("negative: 캔들이 없으면(undefined) 도구가 있어도 도형이 추가되지 않는다", async () => {
    const { result } = setup(fakePort());
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));
    act(() => result.current.setDrawingTool("horizontal-line"));
    act(() => result.current.handleAddDrawing(undefined));
    expect(result.current.drawings).toHaveLength(0);
  });

  it("negative: 변경 없는 persist는 putDrawings를 부르지 않는다", async () => {
    const port = fakePort();
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));
    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(port.putDrawings).not.toHaveBeenCalled();
    expect(result.current.saveStatus).toBe("idle");
  });

  it("negative: 존재하지 않는 도형 id 삭제는 DrawingError를 던진다(조용히 무시하지 않음)", async () => {
    const { result } = setup(fakePort());
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));
    await drawN(result, 2);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => act(() => result.current.handleRemoveDrawing("no-such-id"))).toThrow(/no-such-id|UNKNOWN/);
    consoleError.mockRestore();
  });
});

describe("CH-4c failure injection", () => {
  it("failure injection: getDrawings 네트워크 오류(mockRejectedValue)면 restore_failed와 raw error를 노출한다", async () => {
    const networkError = new Error("network down");
    const port = fakePort({ getDrawings: vi.fn().mockRejectedValue(networkError) });
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("restore_failed"));
    expect(result.current.restoreError).toBe(networkError);
  });

  it("failure injection: putDrawings 5xx 거절이면 saveStatus=error이고 로컬 도형은 유지된다", async () => {
    const port = fakePort({ putDrawings: vi.fn().mockRejectedValue(apiErrorLike(503, "UNAVAILABLE")) });
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));
    await drawN(result, 3);
    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(result.current.saveStatus).toBe("error");
    expect(result.current.saveError).toBeInstanceOf(Error);
    expect(result.current.drawings).toHaveLength(3);
  });

  it("failure injection: putDrawings 409 충돌이면 conflict이고 로컬 도형을 지우지 않는다", async () => {
    const port = fakePort({
      putDrawings: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")),
    });
    const { result } = setup(port, { layoutId: "layout-1" });
    await waitFor(() => expect(result.current.restoreStatus).toBe("ready"));
    await drawN(result, 2);
    await act(async () => {
      await result.current.persist("layout-1");
    });
    expect(result.current.saveStatus).toBe("conflict");
    expect(result.current.drawings).toHaveLength(2);
  });
});

describe("CH-4c performance", () => {
  // Measured locally: ~66 ms for 400 draws + 1 PUT + 20 no-op persists. 2500 ms budget = ~37x headroom (CI contention).
  const BUDGET_MS = 2500;

  async function timedRun(unchangedPersists: number): Promise<{ elapsedMs: number; puts: number }> {
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
    const startedAt = performance.now();
    await drawN(result, 400);
    await act(async () => {
      await result.current.persist("layout-1");
    });
    for (let i = 0; i < unchangedPersists; i += 1) {
      await act(async () => {
        await result.current.persist("layout-1");
      });
    }
    return { elapsedMs: performance.now() - startedAt, puts: (port.putDrawings as ReturnType<typeof vi.fn>).mock.calls.length };
  }

  it("perf: 도형 400개 저장 + 변경 없는 persist 20회가 예산 안에 끝나고 PUT은 1회뿐이다", async () => {
    const { elapsedMs, puts } = await timedRun(20);
    expect(puts).toBe(1);
    expect(elapsedMs).toBeLessThan(perfBudgetMs(BUDGET_MS));
  });

  it("gate red: 지연을 망가뜨리면(예산 초과 값) 같은 성능 단언이 실제로 실패한다", () => {
    const sabotagedElapsedMs = BUDGET_MS * 2;
    expect(() => expect(sabotagedElapsedMs).toBeLessThan(perfBudgetMs(BUDGET_MS))).toThrow();
  });

  it("gate red: 입력을 망가뜨려 persist가 매번 PUT을 내면 'PUT 1회' 단언이 실제로 실패한다", async () => {
    const { puts } = await timedRun(0);
    const sabotagedPuts = puts + 19;
    expect(() => expect(sabotagedPuts).toBe(1)).toThrow();
  });
});
