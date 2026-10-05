import { describe, expect, it } from "vitest";

import type { WorkerPoolBackend } from "../workerPool";
import { createBrowserWorkerPoolBackend, createWorkerPool } from "../workerPool";
import { ECHO_TASK, FakeWorker, TerminatingFakeWorker, wait } from "./workerPool.testHelpers";

// --- DEEPEN 6705 (docs/audit/DEPTH_CH.md, task-2039 CH-18e follow-up): the
// "rejects from the worker's { ok: false, error } reply" test in
// workerPool.test.ts proves `createBrowserWorkerPoolBackend` rejects on a
// real `{ ok: false }` message, but nothing in that suite proves the
// assertion is load-bearing — i.e. that a regression which stops honoring
// `event.data.ok` (for example a backend double or a broken reimplementation
// that always resolves, treating every reply as if it were hardcoded
// `ok: true`) would actually be caught. This adds a red/green pair against
// exactly that failure mode, plus two tests that kill a "hardcoded error
// string" mutant and a "pending state leaks across messages" mutant.

describe("DEEPEN 6705 — gate red reproduction: an always-resolve backend (ignores event.data.ok) vs the real ok:false handling", () => {
  /** Reproduces the exact regression the ok:false test guards against: a `Worker`-shaped
   * double that always resolves regardless of `event.data.ok`, as if every reply were
   * hardcoded to `{ ok: true }`. */
  function createAlwaysResolveBrowserBackend(createWorker: () => Worker): { run<TArgs, TResult>(task: string, args: TArgs): Promise<TResult> } {
    const worker = createWorker() as unknown as FakeWorker;
    return {
      run<TArgs, TResult>(_task: string, _args: TArgs): Promise<TResult> {
        return new Promise<TResult>((resolve) => {
          worker.onmessage = (event: MessageEvent) => {
            // Bug under reproduction: ignores `event.data.ok` entirely and resolves
            // with whatever the reply carried, even a `{ ok: false, error }` payload.
            resolve(event.data as TResult);
          };
        });
      },
    };
  }

  it("적색: always-resolve 더블은 { ok: false, error } 응답을 reject 대신 resolve해 실패를 숨긴다", async () => {
    const worker = new FakeWorker();
    const broken = createAlwaysResolveBrowserBackend(() => worker as unknown as Worker);

    const promise = broken.run(ECHO_TASK, null);
    worker.onmessage?.({ data: { ok: false, error: "kernel exploded" } } as MessageEvent);

    // The bug: this resolves (hiding the failure) instead of rejecting.
    await expect(promise).resolves.toEqual({ ok: false, error: "kernel exploded" });
  });

  it("녹색: 실제 createBrowserWorkerPoolBackend는 같은 { ok: false, error } 응답을 reject한다", async () => {
    const worker = new FakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    const promise = backend.run(ECHO_TASK, null);
    worker.onmessage?.({ data: { ok: false, error: "kernel exploded" } } as MessageEvent);

    await expect(promise).rejects.toThrow("kernel exploded");
  });
});

describe("DEEPEN 6705 — the rejected error is read from the reply, not a hardcoded string", () => {
  it("서로 다른 두 번의 ok:false 응답이 각각 자신의 error 문자열로 reject된다 (고정 문자열 뮤턴트를 잡아낸다)", async () => {
    const workerA = new FakeWorker();
    const backendA = createBrowserWorkerPoolBackend(() => workerA as unknown as Worker);
    const promiseA = backendA.run(ECHO_TASK, null);
    workerA.onmessage?.({ data: { ok: false, error: "first failure: rate limited" } } as MessageEvent);
    await expect(promiseA).rejects.toThrow("first failure: rate limited");

    const workerB = new FakeWorker();
    const backendB = createBrowserWorkerPoolBackend(() => workerB as unknown as Worker);
    const promiseB = backendB.run(ECHO_TASK, null);
    workerB.onmessage?.({ data: { ok: false, error: "second failure: out of memory" } } as MessageEvent);
    await expect(promiseB).rejects.toThrow("second failure: out of memory");
  });
});

describe("DEEPEN 6705 — pending state does not leak across messages (single-flight recovery after ok:false)", () => {
  it("ok:false로 reject된 뒤에도 같은 backend가 다음 요청에서 ok:true를 정상 resolve한다", async () => {
    const worker = new FakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    const first = backend.run(ECHO_TASK, null);
    worker.onmessage?.({ data: { ok: false, error: "transient failure" } } as MessageEvent);
    await expect(first).rejects.toThrow("transient failure");

    const second = backend.run(ECHO_TASK, { n: 7 });
    worker.onmessage?.({ data: { ok: true, result: 14 } } as MessageEvent);
    await expect(second).resolves.toBe(14);
  });
});

// --- DEEPEN 6709 (docs/audit/DEPTH_CH.md, task-2039 CH-18e follow-up): the
// "rejects when the worker itself crashes (onerror)" test in
// workerPool.test.ts proves only that the *first* crashed request rejects —
// it never proves the backend (or the pool sitting on top of it) recovers
// afterwards. A regression that clears `pending` but forgets to reject it,
// or that never clears `pending` at all, would pass that test yet leave the
// backend permanently stuck (the pool's busy-tracking semaphore in
// `createWorkerPool` never frees that slot because `release()` runs in a
// `finally` after the awaited promise settles — if it never settles, the
// slot is gone for good). This adds: (1) a red/green pair reproducing a
// silent-onerror mutant that never settles the pending promise, and (2)
// recovery-path assertions that a backend/pool stays usable for a normal
// request immediately after an onerror crash.

describe("DEEPEN 6709 — gate red reproduction: a silent onerror (never settles) vs the real crash-then-reject handling", () => {
  /** Reproduces a plausible onerror regression: the handler is wired but does nothing,
   * as if a refactor dropped the `pending.reject(...)` call. The in-flight promise then
   * never settles at all — not resolved, not rejected — which is worse than resolving
   * wrong, because the caller (and the pool's busy slot) hangs forever. */
  function createSilentOnErrorBackend(createWorker: () => Worker): { run<TArgs, TResult>(task: string, args: TArgs): Promise<TResult> } {
    const worker = createWorker() as unknown as FakeWorker;
    return {
      run<TArgs, TResult>(_task: string, _args: TArgs): Promise<TResult> {
        return new Promise<TResult>((resolve) => {
          worker.onmessage = (event: MessageEvent) => resolve(event.data as TResult);
          // Bug under reproduction: no `worker.onerror` handler is wired at all, so a
          // worker crash leaves this promise permanently unsettled.
        });
      },
    };
  }

  it("적색: silent onerror 더블은 워커 크래시 후에도 promise가 절대 settle되지 않는다 (500ms 안에 reject/resolve 어느 쪽도 없음)", async () => {
    const worker = new FakeWorker();
    const broken = createSilentOnErrorBackend(() => worker as unknown as Worker);

    const promise = broken.run(ECHO_TASK, null);
    worker.onerror?.({ message: "worker thread died" } as ErrorEvent);

    const raced = await Promise.race([promise.then(() => "settled").catch(() => "settled"), wait(50).then(() => "still-pending")]);
    expect(raced).toBe("still-pending");
  });

  it("녹색: 실제 createBrowserWorkerPoolBackend는 같은 크래시를 즉시 reject해 절대 매달리지 않는다", async () => {
    const worker = new FakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    const promise = backend.run(ECHO_TASK, null);
    worker.onerror?.({ message: "worker thread died" } as ErrorEvent);

    const raced = await Promise.race([promise.then(() => "settled").catch(() => "settled"), wait(50).then(() => "still-pending")]);
    expect(raced).toBe("settled");
    await expect(promise).rejects.toThrow("worker thread died");
  });
});

describe("DEEPEN 6709 — recovery path after onerror: the backend and the pool both stay usable for the next request", () => {
  it("backend 단위: onerror로 크래시한 직후에도 같은 backend가 다음 요청을 정상적으로 resolve한다", async () => {
    const worker = new FakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    const crashed = backend.run(ECHO_TASK, null);
    worker.onerror?.({ message: "worker thread died" } as ErrorEvent);
    await expect(crashed).rejects.toThrow("worker thread died");

    const recovered = backend.run(ECHO_TASK, { n: 9 });
    worker.onmessage?.({ data: { ok: true, result: 18 } } as MessageEvent);
    await expect(recovered).resolves.toBe(18);
  });

  it("pool 단위: onerror로 크래시한 backend가 즉시 idle로 반환되어, 같은 슬롯으로 다음 제출이 막히지 않는다", async () => {
    const worker = new FakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);
    const pool = createWorkerPool([backend]); // single backend: a stuck slot would deadlock every future submit

    const crashed = pool.submit(ECHO_TASK, null);
    // `pool.submit` resolves its idle-backend acquisition via a microtask
    // (`Promise.resolve(index)` inside `acquire()`) before it calls
    // `backend.run(...)`, so `pending` isn't set on this synchronous turn —
    // flush that one tick before the FakeWorker fires its crash.
    await Promise.resolve();
    worker.onerror?.({ message: "worker thread died" } as ErrorEvent);
    await expect(crashed).rejects.toMatchObject({
      code: "WORKER_POOL_TASK_FAILED",
      message: expect.stringContaining("worker thread died"),
    });

    const recovered = pool.submit(ECHO_TASK, { n: 5 });
    await Promise.resolve();
    worker.onmessage?.({ data: { ok: true, result: 10 } } as MessageEvent);
    await expect(recovered).resolves.toBe(10);
    pool.dispose();
  });
});

// --- DEEPEN 6710 (docs/audit/DEPTH_CH.md, task-2039 CH-18e follow-up): the
// "rejects with WORKER_POOL_DISPOSED after dispose" test above proved `run()`
// rejects post-dispose, but never proved *why* that matters — it never
// verified `run()` avoids calling `postMessage` on the now-terminated worker,
// so a regression that dropped the `disposed` guard before `postMessage`
// would have passed unnoticed as long as the promise still eventually
// rejected some other way. This adds a failure-injection test against a
// worker double that throws like a real terminated `Worker` would, plus a
// red/green pair against the naive (unguarded) implementation.

describe("DEEPEN 6710 — failure injection: postMessage throws if it ever reaches a terminated worker", () => {
  it("dispose()-then-run() never touches postMessage, so the real terminated-worker throw is never triggered", async () => {
    const worker = new TerminatingFakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    backend.dispose();

    // If the disposed-guard were missing, this would throw synchronously inside
    // `run()` (a `DOMException`, not a `WorkerPoolError`) instead of rejecting cleanly.
    await expect(backend.run(ECHO_TASK, null)).rejects.toMatchObject({ code: "WORKER_POOL_DISPOSED" });
    expect(worker.posted).toHaveLength(0);
  });
});

describe("DEEPEN 6710 — gate red reproduction (unguarded post-dispose run vs the real disposed-guard)", () => {
  /** Reproduces a `createBrowserWorkerPoolBackend` that forgot the `disposed` check in `run()` —
   * exactly the regression the failure-injection test above guards against. */
  function createUnguardedBrowserBackend(createWorker: () => Worker): WorkerPoolBackend {
    const worker = createWorker();
    return {
      run<TArgs, TResult>(_task: string, args: TArgs): Promise<TResult> {
        // Bug under reproduction: no `if (disposed) return Promise.reject(...)` guard —
        // falls straight through to `postMessage` even after `dispose()`.
        return new Promise<TResult>((resolve) => {
          worker.postMessage({ task: _task, args });
          resolve(undefined as TResult);
        });
      },
      dispose(): void {
        worker.terminate();
      },
    };
  }

  it("적색: 가드 없는 backend는 dispose 후 run()이 terminated worker에 postMessage를 시도해 DOMException으로 reject된다", async () => {
    const worker = new TerminatingFakeWorker();
    const broken = createUnguardedBrowserBackend(() => worker as unknown as Worker);

    broken.dispose();

    // `postMessage`'s throw happens inside the executor, so a naive Promise
    // wrapper turns it into a rejection rather than a synchronous throw — either
    // way it's the real DOMException, not a clean `WORKER_POOL_DISPOSED`.
    await expect(broken.run(ECHO_TASK, null)).rejects.toThrow(DOMException);
  });

  it("녹색: 실제 createBrowserWorkerPoolBackend는 같은 조건에서 postMessage를 시도조차 하지 않고 WORKER_POOL_DISPOSED로 reject한다", async () => {
    const worker = new TerminatingFakeWorker();
    const backend = createBrowserWorkerPoolBackend(() => worker as unknown as Worker);

    backend.dispose();

    await expect(backend.run(ECHO_TASK, null)).rejects.toMatchObject({ code: "WORKER_POOL_DISPOSED" });
    expect(worker.posted).toHaveLength(0);
  });
});
