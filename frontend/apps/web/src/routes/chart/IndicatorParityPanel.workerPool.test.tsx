import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndicatorParityPanel } from "./IndicatorParityPanel";
import type { WorkerPool } from "@aios/chart-engine/src/compute/workerPool";
import { SMA, manyCandles, smaVerifiedCatalog } from "./IndicatorParityPanel.fixtures";

afterEach(() => {
  cleanup();
});

// CH-18e — client compute now runs through workerPool.ts (task-2039). These
// tests exercise the async dispatch, the browser's default no-Worker fallback
// note, worker-error -> server fallback, and cancellation of stale results —
// all via an injected `computePool` prop so they never depend on jsdom's lack
// of a real `Worker`/bundler `import.meta.url` worker construction.
describe("IndicatorParityPanel — CH-18e workerPool 실배선", () => {
  it("Worker 생성자가 없는 환경(jsdom)에서는 메인 스레드에서 동기 계산하되, 그 사실을 화면에 표시한다(무음 폴백 금지)", async () => {
    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={() => ({ value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 50200)) })}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)"));
    expect(screen.getByTestId("indicator-parity-note-SMA")).toHaveTextContent("워커 미지원");
  });

  it("워커(풀) 오류는 그 지표를 서버 값으로 폴백시키고 클라이언트 값은 절대 그리지 않는다", async () => {
    const failingPool: WorkerPool = {
      submit: vi.fn().mockRejectedValue(new Error("WORKER_POOL_TASK_FAILED: kernel exploded")),
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };

    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        computePool={failingPool}
        resolveServerSeries={() => ({ value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 77)) })}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(server)"));
    expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("77.000000");
    expect(screen.getByTestId("indicator-parity-fallback-SMA")).toBeInTheDocument();
    expect(screen.queryByTestId("indicator-parity-note-SMA")).not.toBeInTheDocument();
  });

  it("입력이 바뀌어 새 계산이 시작된 뒤 이전(stale) 응답이 도착해도 화면에 반영하지 않는다", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn((_task: string, _args: unknown) => new Promise((resolve) => deferred.push({ resolve }))) as unknown as WorkerPool["submit"],
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };
    const catalog = smaVerifiedCatalog();

    const { rerender } = render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={catalog}
        computePool={pool}
        resolveServerSeries={() => ({ value: Array(25).fill(111) })}
      />,
    );
    expect(deferred).toHaveLength(1);

    rerender(
      <IndicatorParityPanel
        candles={manyCandles(30)}
        overlays={[SMA]}
        catalog={catalog}
        computePool={pool}
        resolveServerSeries={() => ({ value: Array(30).fill(222) })}
      />,
    );
    expect(deferred).toHaveLength(2);

    // Resolve the stale (first) request with a value that must never render.
    deferred[0]!.resolve({ value: Array(25).fill(999) });
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByText(/999/)).not.toBeInTheDocument();

    deferred[1]!.resolve({ value: Array(30).fill(222) });
    await waitFor(() => expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("222.000000"));
  });

  it("언마운트 후 도착한 응답은 무시된다(setState 없음)", async () => {
    const deferred: Array<{ resolve: (v: unknown) => void }> = [];
    const pool: WorkerPool = {
      submit: vi.fn((_task: string, _args: unknown) => new Promise((resolve) => deferred.push({ resolve }))) as unknown as WorkerPool["submit"],
      size: 1,
      disposed: false,
      dispose: vi.fn(),
    };
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { unmount } = render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        computePool={pool}
        resolveServerSeries={() => ({ value: Array(25).fill(111) })}
      />,
    );
    expect(deferred).toHaveLength(1);

    unmount();
    deferred[0]!.resolve({ value: Array(25).fill(111) });
    await Promise.resolve();
    await Promise.resolve();

    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });
});
