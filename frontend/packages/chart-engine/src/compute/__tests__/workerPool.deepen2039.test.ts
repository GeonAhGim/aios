import { describe, expect, it, vi } from "vitest";

import { createWorkerPool } from "../workerPool";
import { ECHO_TASK, wait } from "./workerPool.testHelpers";

// --- DEEPEN 2039 (docs/audit/DEPTH_CH.md, task-2039 CH-18e follow-up): the
// "caps concurrently-running backends" test in workerPool.test.ts asserted
// only that the concurrency cap isn't exceeded (a *count*), not that the cap
// actually translates into a measurable ms-level gain, nor that a regression
// which drops busy-tracking would be caught, nor that independent pool
// instances don't interfere (D3). The three describe blocks below cover each
// axis with test-only additions.

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
