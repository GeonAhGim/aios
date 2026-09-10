import { describe, expect, it, vi } from "vitest";

import type { IndicatorCatalogPort } from "../../plugins/indicatorPlugin";
import { loadIndicatorCatalog } from "../../plugins/indicatorPlugin";
import { CLIENT_ENGINE_COMPUTE_TASK, ClientEngineError, KERNEL_FACTORIES, computeIndicatorSeries, createClientIncrementalIndicator } from "../clientEngine";
import { VERIFIED_KERNEL_PINS } from "../verifiedIndicators";
import {
  WorkerPoolError,
  createBrowserWorkerPoolBackend,
  createInlineWorkerPoolBackend,
  createWorkerPool,
} from "../workerPool";

const ECHO_TASK = "test/echo";

/** Minimal `Worker`-shaped double: postMessage records the message, tests drive replies via onmessage/onerror. */
class FakeWorker {
  posted: unknown[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  terminate = vi.fn();

  postMessage(message: unknown): void {
    this.posted.push(message);
  }
}

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

  it("caps concurrently-running backends at the pool size even when 30 requests are queued at once", async () => {
    const poolSize = 4;
    let running = 0;
    let maxRunning = 0;
    const backends = Array.from({ length: poolSize }, () => ({
      run: async (_task: string, args: unknown) => {
        running += 1;
        maxRunning = Math.max(maxRunning, running);
        await new Promise((resolve) => setTimeout(resolve, 5));
        running -= 1;
        return args;
      },
      dispose: vi.fn(),
    }));
    const pool = createWorkerPool(backends);

    const results = await Promise.all(Array.from({ length: 30 }, (_, i) => pool.submit<number, number>(ECHO_TASK, i)));

    expect(results).toHaveLength(30);
    expect(maxRunning).toBeGreaterThan(0);
    expect(maxRunning).toBeLessThanOrEqual(poolSize);
    pool.dispose();
  });
});

describe("createBrowserWorkerPoolBackend", () => {
  it("posts { task, args } and resolves from the worker's { ok: true, result } reply", async () => {
    const worker = new FakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    const promise = backend.run(ECHO_TASK, { n: 1 });
    expect(worker.posted).toEqual([{ task: ECHO_TASK, args: { n: 1 } }]);
    worker.onmessage?.({ data: { ok: true, result: 42 } } as MessageEvent);

    await expect(promise).resolves.toBe(42);
  });

  it("rejects from the worker's { ok: false, error } reply", async () => {
    const worker = new FakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    const promise = backend.run(ECHO_TASK, null);
    worker.onmessage?.({ data: { ok: false, error: "kernel exploded" } } as MessageEvent);

    await expect(promise).rejects.toThrow("kernel exploded");
  });

  it("rejects when the worker itself crashes (onerror)", async () => {
    const worker = new FakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    const promise = backend.run(ECHO_TASK, null);
    worker.onerror?.({ message: "worker thread died" } as ErrorEvent);

    await expect(promise).rejects.toThrow("worker thread died");
  });

  it("rejects with WORKER_POOL_DISPOSED after dispose, and terminates the underlying worker", async () => {
    const worker = new FakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    backend.dispose();

    expect(worker.terminate).toHaveBeenCalledOnce();
    await expect(backend.run(ECHO_TASK, null)).rejects.toMatchObject({ code: "WORKER_POOL_DISPOSED" });
  });

  it("drives a real indicator compute end to end through the pool", async () => {
    const worker = new FakeWorker();
    worker.postMessage = vi.fn((message: unknown) => {
      const { task, args } = message as { task: string; args: unknown };
      const result = task === CLIENT_ENGINE_COMPUTE_TASK ? computeIndicatorSeries(args as never) : null;
      worker.onmessage?.({ data: { ok: true, result } } as MessageEvent);
    });
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);
    const pool = createWorkerPool([backend]);
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

// --- DEEPEN 1953 (docs/audit/DEPTH_CH.md): 원 리프는 워커 콜백 강제 throw
// 2건만 "준-실패주입"으로 인정됐을 뿐 mocked network/DB 실패주입, 수치 성능
// 단언, 게이트 적색 재현이 전부 없었다. 아래 세 축을 test-only로 보강한다.
// (D3는 CH가 안전축 목록에 없어 D2 하한이라 이 리프의 대상이 아니다.)

describe("DEEPEN 1953 — mocked network failure injection (real IndicatorCatalogPort boundary)", () => {
  it("a network failure loading the catalog (IndicatorCatalogPort.listIndicators rejects) fails the whole compute path closed, wrapped by the pool as WORKER_POOL_TASK_FAILED", async () => {
    // `IndicatorCatalogPort` is the actual `@aios/api-client` network boundary
    // this module's catalog ultimately comes from (IND-12 GET /v1/indicators)
    // — unlike the worker-callback throw already covered above, this models a
    // real network/DB-style failure (timeout, 5xx, connection refused), not a
    // synchronous handler exception.
    const listIndicators = vi.fn().mockRejectedValue(new Error("ECONNREFUSED: indicators service unreachable"));
    const port: IndicatorCatalogPort = { listIndicators };

    const loaded = await loadIndicatorCatalog(port);
    expect(loaded.kind).toBe("error");
    // fail-closed: a network failure yields `null`, never a stale/cached catalog.
    const catalog = loaded.kind === "ok" ? loaded.page.items : null;

    const backend = createInlineWorkerPoolBackend({ [CLIENT_ENGINE_COMPUTE_TASK]: computeIndicatorSeries });
    const pool = createWorkerPool([backend]);

    await expect(
      pool.submit(CLIENT_ENGINE_COMPUTE_TASK, {
        name: "SMA",
        params: { timeperiod: 2 },
        bars: [{ close: 1 }, { close: 2 }, { close: 3 }],
        catalog,
      }),
    ).rejects.toMatchObject({
      code: "WORKER_POOL_TASK_FAILED",
      message: expect.stringContaining("CLIENT_ENGINE_INDICATOR_NOT_VERIFIED"),
    });
    expect(listIndicators).toHaveBeenCalledTimes(1);
    pool.dispose();
  });
});

describe("DEEPEN 1953 — numeric performance assertion", () => {
  it("computes a 50,000-bar SMA series through the pool within a 2000ms budget", async () => {
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
    const bars = Array.from({ length: 50_000 }, (_, i) => ({ close: 100 + Math.sin(i / 50) * 10 }));
    const backend = createInlineWorkerPoolBackend({ [CLIENT_ENGINE_COMPUTE_TASK]: computeIndicatorSeries });
    const pool = createWorkerPool([backend]);

    const start = performance.now();
    const series = await pool.submit<
      { name: string; params: Record<string, number>; bars: typeof bars; catalog: typeof catalog },
      Record<string, ReadonlyArray<number | null>>
    >(CLIENT_ENGINE_COMPUTE_TASK, { name: "SMA", params: { timeperiod: 20 }, bars, catalog });
    const elapsedMs = performance.now() - start;

    expect(series.value).toHaveLength(50_000);
    expect(series.value?.[49_999]).not.toBeNull();
    expect(elapsedMs).toBeLessThan(2000);
    pool.dispose();
  });
});

describe("DEEPEN 1953 — gate red reproduction (naive kernel bypass vs the real whitelist gate)", () => {
  it("적색: KERNEL_FACTORIES를 화이트리스트 없이 직접 호출하면 entry_hash 드리프트를 무시하고 계산을 계속한다; 녹색: createClientIncrementalIndicator는 같은 드리프트를 즉시 거부한다", () => {
    const driftedCatalog = [
      {
        name: "SMA",
        tier: VERIFIED_KERNEL_PINS.SMA!.tier,
        category: "test",
        version: "ind-v1",
        hash: "0".repeat(64), // drifted away from VERIFIED_KERNEL_PINS.SMA.entryHash
        inputs: ["close"],
        outputs: ["value"],
      },
    ];

    // Red: a hypothetical regression that reaches straight into KERNEL_FACTORIES
    // (bypassing createClientIncrementalIndicator's catalog check) has no idea
    // the server's canonical spec drifted — it silently keeps computing.
    const bypassed = KERNEL_FACTORIES.SMA!({ timeperiod: 2 }, "SMA");
    expect(bypassed.update({ close: 1 })).toBeNull(); // window not full yet
    expect(bypassed.update({ close: 3 })).toEqual([2]); // computed anyway — no gate consulted

    // Green: the real, only sanctioned entry point refuses the exact same
    // drifted catalog instead of computing a value nobody re-verified.
    expect(() => createClientIncrementalIndicator("SMA", { timeperiod: 2 }, driftedCatalog)).toThrow(ClientEngineError);
    try {
      createClientIncrementalIndicator("SMA", { timeperiod: 2 }, driftedCatalog);
      throw new Error("expected throw");
    } catch (err) {
      expect(err).toBeInstanceOf(ClientEngineError);
      expect((err as ClientEngineError).code).toBe("CLIENT_ENGINE_INDICATOR_NOT_VERIFIED");
    }
  });
});
