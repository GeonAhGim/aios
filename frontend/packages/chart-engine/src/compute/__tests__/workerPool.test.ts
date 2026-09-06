import { describe, expect, it, vi } from "vitest";

import { CLIENT_ENGINE_COMPUTE_TASK, computeIndicatorSeries } from "../clientEngine";
import { VERIFIED_KERNEL_PINS } from "../verifiedIndicators";
import { WorkerPoolError, createInlineWorkerPoolBackend, createWorkerPool } from "../workerPool";

const ECHO_TASK = "test/echo";

describe("createInlineWorkerPoolBackend", () => {
  it("runs the registered handler asynchronously (never on the caller's own turn)", async () => {
    const backend = createInlineWorkerPoolBackend({ [ECHO_TASK]: (args: number) => args * 2 });
    let resolvedSynchronously = true;
    const promise = backend.run<number, number>(ECHO_TASK, 21).then((result) => {
      resolvedSynchronously = false;
      return result;
    });
    expect(resolvedSynchronously).toBe(true);
    await expect(promise).resolves.toBe(42);
    expect(resolvedSynchronously).toBe(false);
  });

  it("rejects with WORKER_POOL_TASK_UNKNOWN for an unregistered task", async () => {
    const backend = createInlineWorkerPoolBackend({});
    await expect(backend.run(ECHO_TASK, null)).rejects.toMatchObject({ code: "WORKER_POOL_TASK_UNKNOWN" });
  });

  it("rejects with WORKER_POOL_DISPOSED once disposed", async () => {
    const backend = createInlineWorkerPoolBackend({ [ECHO_TASK]: (x: number) => x });
    backend.dispose();
    await expect(backend.run(ECHO_TASK, 1)).rejects.toMatchObject({ code: "WORKER_POOL_DISPOSED" });
  });

  it("propagates a handler's thrown error as a rejection", async () => {
    const backend = createInlineWorkerPoolBackend({
      [ECHO_TASK]: () => {
        throw new Error("boom");
      },
    });
    await expect(backend.run(ECHO_TASK, null)).rejects.toThrow("boom");
  });
});

describe("createWorkerPool", () => {
  it("requires at least one backend", () => {
    expect(() => createWorkerPool([])).toThrow(WorkerPoolError);
  });

  it("round-robins submissions across backends", async () => {
    const runA = vi.fn((_task: string, args: unknown) => Promise.resolve(args));
    const runB = vi.fn((_task: string, args: unknown) => Promise.resolve(args));
    const pool = createWorkerPool([
      { run: runA, dispose: vi.fn() },
      { run: runB, dispose: vi.fn() },
    ]);
    await pool.submit(ECHO_TASK, 1);
    await pool.submit(ECHO_TASK, 2);
    await pool.submit(ECHO_TASK, 3);
    expect(runA).toHaveBeenCalledTimes(2);
    expect(runB).toHaveBeenCalledTimes(1);
  });

  it("wraps a backend failure as WORKER_POOL_TASK_FAILED", async () => {
    const pool = createWorkerPool([
      {
        run: () => Promise.reject(new Error("kernel exploded")),
        dispose: vi.fn(),
      },
    ]);
    await expect(pool.submit(ECHO_TASK, null)).rejects.toMatchObject({
      code: "WORKER_POOL_TASK_FAILED",
      message: expect.stringContaining("kernel exploded"),
    });
  });

  it("disposes every backend and refuses further submissions", async () => {
    const disposeA = vi.fn();
    const disposeB = vi.fn();
    const pool = createWorkerPool([
      { run: () => Promise.resolve(null), dispose: disposeA },
      { run: () => Promise.resolve(null), dispose: disposeB },
    ]);
    pool.dispose();
    expect(disposeA).toHaveBeenCalledOnce();
    expect(disposeB).toHaveBeenCalledOnce();
    expect(pool.disposed).toBe(true);
    await expect(pool.submit(ECHO_TASK, null)).rejects.toMatchObject({ code: "WORKER_POOL_DISPOSED" });
    pool.dispose(); // idempotent
  });
});

describe("workerPool + clientEngine wiring", () => {
  it("computes a real indicator series through the pool's inline backend", async () => {
    const catalog = [
      {
        name: "SMA",
        tier: VERIFIED_KERNEL_PINS.SMA!.tier,
        category: "test",
        version: "ind-v1",
        hash: VERIFIED_KERNEL_PINS.SMA!.entryHash,
        inputs: ["close"],
        outputs: ["value"],
      },
    ];
    const backend = createInlineWorkerPoolBackend({ [CLIENT_ENGINE_COMPUTE_TASK]: computeIndicatorSeries });
    const pool = createWorkerPool([backend]);
    const series = await pool.submit(CLIENT_ENGINE_COMPUTE_TASK, {
      name: "SMA",
      params: { timeperiod: 2 },
      bars: [{ close: 1 }, { close: 2 }, { close: 3 }],
      catalog,
    });
    expect(series).toEqual({ value: [null, 1.5, 2.5] });
    pool.dispose();
  });
});
