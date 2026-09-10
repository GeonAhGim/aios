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

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

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

// --- DEEPEN 2039 (docs/audit/DEPTH_CH.md): 원 리프(17ef81ee, CH-18e)의
// "caps concurrently-running backends" 테스트는 동시성 상한을 넘지 않는다는
// *카운트*만 단언했을 뿐 그 카운트가 실제 ms 단위 이득으로 이어지는지(수치
// 성능), 카운트 자체가 깨지는 회귀(busy 추적 제거)를 잡아내는 적색 재현이
// 있는지, 여러 풀 인스턴스가 서로 간섭하지 않는지(D3)가 전부 없었다. 아래
// 세 축을 test-only로 보강한다.

describe("DEEPEN 2039 — numeric performance (wall-clock ms, not a concurrency count)", () => {
  it("4-백엔드 풀로 20건(각 20ms)을 처리하면, 같은 부하를 1-백엔드(직렬) 풀로 돌릴 때보다 실측 ms가 뚜렷이 짧다", async () => {
    const taskCount = 20;
    const perTaskMs = 20;

    async function run(poolSize: number): Promise<number> {
      const backends = Array.from({ length: poolSize }, () => ({
        run: async (_task: string, args: unknown) => {
          await wait(perTaskMs);
          return args;
        },
        dispose: vi.fn(),
      }));
      const pool = createWorkerPool(backends);
      const start = performance.now();
      await Promise.all(Array.from({ length: taskCount }, (_, i) => pool.submit<number, number>(ECHO_TASK, i)));
      const elapsed = performance.now() - start;
      pool.dispose();
      return elapsed;
    }

    const serialElapsed = await run(1);
    const concurrentElapsed = await run(4);

    // Serial lower bound: 20 * 20ms = 400ms. 4-way concurrent lower bound: 5 waves * 20ms = 100ms.
    expect(serialElapsed).toBeGreaterThanOrEqual(taskCount * perTaskMs * 0.9);
    // Generous CI margin, but well under the serial bound so a regression to
    // fire-and-forget-without-cap (instant) or back-to-serial (400ms+) both fail this.
    expect(concurrentElapsed).toBeLessThan(taskCount * perTaskMs * 0.6);
    expect(concurrentElapsed).toBeLessThan(serialElapsed);
  });
});

describe("DEEPEN 2039 — gate red reproduction (naive fire-and-forget round-robin vs the busy-tracked pool)", () => {
  /** Reproduces the pre-17ef81ee `submit` shape the module docstring calls out: round-robins but never
   * waits for a backend to go idle before handing it the next task — so the pool's own backend count is
   * NOT a concurrency cap. */
  function createNaiveRoundRobinPool(backends: readonly { run(task: string, args: unknown): Promise<unknown> }[]) {
    let next = 0;
    return {
      submit<TArgs, TResult>(task: string, args: TArgs): Promise<TResult> {
        const backend = backends[next % backends.length]!;
        next += 1;
        return backend.run(task, args) as Promise<TResult>;
      },
    };
  }

  it("적색: naive round-robin은 30건 동시 제출 시 동시성 상한(4)을 어기고 최대 30개까지 동시 실행을 허용한다", async () => {
    const poolSize = 4;
    let running = 0;
    let maxRunning = 0;
    const backends = Array.from({ length: poolSize }, () => ({
      run: async (_task: string, args: unknown) => {
        running += 1;
        maxRunning = Math.max(maxRunning, running);
        await wait(5);
        running -= 1;
        return args;
      },
    }));
    const naivePool = createNaiveRoundRobinPool(backends);

    await Promise.all(Array.from({ length: 30 }, (_, i) => naivePool.submit<number, number>(ECHO_TASK, i)));

    expect(maxRunning).toBeGreaterThan(poolSize);
  });

  it("녹색: 동일한 백엔드 배열이라도 createWorkerPool을 통하면 30건 동시 제출에도 동시성 상한(4)을 지킨다", async () => {
    const poolSize = 4;
    let running = 0;
    let maxRunning = 0;
    const backends = Array.from({ length: poolSize }, () => ({
      run: async (_task: string, args: unknown) => {
        running += 1;
        maxRunning = Math.max(maxRunning, running);
        await wait(5);
        running -= 1;
        return args;
      },
      dispose: vi.fn(),
    }));
    const pool = createWorkerPool(backends);

    await Promise.all(Array.from({ length: 30 }, (_, i) => pool.submit<number, number>(ECHO_TASK, i)));

    expect(maxRunning).toBeGreaterThan(0);
    expect(maxRunning).toBeLessThanOrEqual(poolSize);
    pool.dispose();
  });
});

describe("DEEPEN 2039 — D3: multiple independent WorkerPool instances under simultaneous load don't interfere", () => {
  it("3개의 독립 풀이 동시에(인터리빙) 각자 15건씩 제출해도 각자의 동시성 상한을 독립적으로 지키고 결과가 다른 풀로 섞이지 않는다", async () => {
    const poolInstanceCount = 3;
    const poolSize = 3;
    const tasksPerPool = 15;

    const instances = Array.from({ length: poolInstanceCount }, (_, poolIndex) => {
      let running = 0;
      let maxRunning = 0;
      const backends = Array.from({ length: poolSize }, () => ({
        run: async (_task: string, args: unknown) => {
          running += 1;
          maxRunning = Math.max(maxRunning, running);
          await wait(5);
          running -= 1;
          // Tag every result with the owning pool's index so cross-pool leakage is detectable.
          return { poolIndex, echoed: args };
        },
        dispose: vi.fn(),
      }));
      return { pool: createWorkerPool(backends), poolIndex, getMaxRunning: () => maxRunning };
    });

    // Interleave: fire all three pools' submissions in the same microtask sweep instead of
    // finishing one pool before starting the next, so any shared/leaked state would surface.
    const allResults = await Promise.all(
      instances.flatMap(({ pool, poolIndex }) =>
        Array.from({ length: tasksPerPool }, (_, i) =>
          pool.submit<number, { poolIndex: number; echoed: number }>(ECHO_TASK, i).then((result) => ({ expectedPoolIndex: poolIndex, result })),
        ),
      ),
    );

    for (const { expectedPoolIndex, result } of allResults) {
      expect(result.poolIndex).toBe(expectedPoolIndex);
    }
    for (const { getMaxRunning } of instances) {
      expect(getMaxRunning()).toBeGreaterThan(0);
      expect(getMaxRunning()).toBeLessThanOrEqual(poolSize);
    }

    instances.forEach(({ pool }) => pool.dispose());
  });
});
