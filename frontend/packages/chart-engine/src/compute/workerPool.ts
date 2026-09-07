/**
 * CH-18a — worker pool port: dispatches named compute tasks (e.g.
 * `clientEngine.ts` `CLIENT_ENGINE_COMPUTE_TASK`) so a full-history client
 * indicator recompute doesn't block the render thread.
 *
 * Same dependency-injected port shape as `plugins/indicatorPlugin.ts`
 * `IndicatorCatalogPort` / `layout/persistence.ts` `ChartingPort` (task
 * convention) — this module still never calls `new Worker(...)` itself.
 * `createInlineWorkerPoolBackend` runs a task on a microtask instead of a
 * real worker thread, so it works in any environment (tests, SSR, a browser
 * without Worker support) and gives every caller the same async contract a
 * real worker would.
 *
 * CH-18e (task-2039) adds `createBrowserWorkerPoolBackend`: a real-`Worker`
 * backend that still takes its `Worker` instance via an injected factory
 * (bundler-specific `new Worker(new URL(...), import.meta.url)` wiring lives
 * at the call site, e.g. apps/web's `indicatorComputePool.ts`) — this file
 * itself has no static reference to a browser global. `createWorkerPool`'s
 * `submit` now tracks each backend's busy/idle state (a small semaphore)
 * instead of firing-and-forgetting round-robin: a backend is only handed a
 * new task once its previous one settles, so the pool's own backend count is
 * a hard concurrency cap regardless of how many submissions arrive at once —
 * this is what CH-18's "Web Worker 증분 계산" DoD asserts (<=N concurrently
 * executing out of 30 queued requests).
 */

export type WorkerPoolHandler<TArgs = unknown, TResult = unknown> = (args: TArgs) => TResult;

/** One backend "worker": runs a task to completion however it sees fit (inline, real worker, ...). */
export interface WorkerPoolBackend {
  run<TArgs, TResult>(task: string, args: TArgs): Promise<TResult>;
  /** Releases backend resources (a real worker: terminate()). Idempotent. */
  dispose(): void;
}

export type WorkerPoolErrorCode = "WORKER_POOL_DISPOSED" | "WORKER_POOL_TASK_UNKNOWN" | "WORKER_POOL_TASK_FAILED" | "WORKER_POOL_INVALID";

export class WorkerPoolError extends Error {
  readonly code: WorkerPoolErrorCode;
  readonly task: string;

  constructor(code: WorkerPoolErrorCode, task: string, detail: string) {
    super(`${code}: ${detail} (task "${task}")`);
    this.name = "WorkerPoolError";
    this.code = code;
    this.task = task;
  }
}

export interface WorkerPool {
  /** Round-robins across backends. Rejects with `WORKER_POOL_DISPOSED` once disposed. */
  submit<TArgs, TResult>(task: string, args: TArgs): Promise<TResult>;
  readonly size: number;
  readonly disposed: boolean;
  /** Idempotent; disposes every backend. */
  dispose(): void;
}

export function createWorkerPool(backends: readonly WorkerPoolBackend[]): WorkerPool {
  if (backends.length === 0) {
    throw new WorkerPoolError("WORKER_POOL_INVALID", "(pool)", "at least one backend is required");
  }
  let nextIndex = 0;
  let disposed = false;
  const busy = backends.map(() => false);
  const waiters: Array<(index: number) => void> = [];

  /** Round-robins among idle backends only; returns null when every backend is busy. */
  function tryAcquireIdle(): number | null {
    for (let step = 0; step < backends.length; step += 1) {
      const index = (nextIndex + step) % backends.length;
      if (!busy[index]) {
        busy[index] = true;
        nextIndex = (index + 1) % backends.length;
        return index;
      }
    }
    return null;
  }

  function acquire(): Promise<number> {
    const index = tryAcquireIdle();
    if (index !== null) return Promise.resolve(index);
    return new Promise((resolve) => waiters.push(resolve));
  }

  /** Frees `index` and, if a task is queued, hands the next idle slot straight to it. */
  function release(index: number): void {
    busy[index] = false;
    const waiter = waiters.shift();
    if (!waiter) return;
    const nextFree = tryAcquireIdle();
    if (nextFree !== null) waiter(nextFree);
    else waiters.unshift(waiter); // unreachable: `index` was just freed, so one must be idle
  }

  async function submit<TArgs, TResult>(task: string, args: TArgs): Promise<TResult> {
    if (disposed) {
      throw new WorkerPoolError("WORKER_POOL_DISPOSED", task, "pool is disposed");
    }
    const index = await acquire();
    try {
      return await backends[index]!.run<TArgs, TResult>(task, args);
    } catch (err) {
      if (err instanceof WorkerPoolError) throw err;
      throw new WorkerPoolError("WORKER_POOL_TASK_FAILED", task, err instanceof Error ? err.message : String(err));
    } finally {
      release(index);
    }
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    for (const backend of backends) backend.dispose();
  }

  return {
    submit,
    size: backends.length,
    get disposed() {
      return disposed;
    },
    dispose,
  };
}

/**
 * Always-available fallback backend: runs registered handlers on a
 * microtask rather than synchronously, so callers can never observe a
 * timing difference from a real worker (and thus can't accidentally depend
 * on one).
 */
export function createInlineWorkerPoolBackend(handlers: Readonly<Record<string, WorkerPoolHandler>>): WorkerPoolBackend {
  let disposed = false;
  return {
    run<TArgs, TResult>(task: string, args: TArgs): Promise<TResult> {
      if (disposed) {
        return Promise.reject(new WorkerPoolError("WORKER_POOL_DISPOSED", task, "backend is disposed"));
      }
      const handler = handlers[task];
      if (!handler) {
        return Promise.reject(new WorkerPoolError("WORKER_POOL_TASK_UNKNOWN", task, "no handler registered for this task"));
      }
      return new Promise<TResult>((resolve, reject) => {
        queueMicrotask(() => {
          try {
            resolve(handler(args) as TResult);
          } catch (err) {
            reject(err instanceof Error ? err : new Error(String(err)));
          }
        });
      });
    },
    dispose(): void {
      disposed = true;
    },
  };
}

/** Wire message shape a real `Worker` backend posts/receives — shared with apps/web's worker script. */
export interface IndicatorWorkerMessage {
  readonly task: string;
  readonly args: unknown;
}

export type IndicatorWorkerResponse =
  | { readonly ok: true; readonly result: unknown }
  | { readonly ok: false; readonly error: string };

/**
 * Real-`Worker` backend: posts `{ task, args }` and settles from the worker's
 * `{ ok, result | error }` reply. `createWorker` is injected — this module
 * has no static `new Worker(...)` of its own (see module docstring) — and
 * this backend only ever has one message in flight, so pairing the reply
 * with the request needs no correlation id. That single-flight rule is also
 * what makes `createWorkerPool`'s busy-tracking an accurate concurrency cap:
 * it never calls `run` again on this backend before the previous call
 * settles.
 */
export function createBrowserWorkerPoolBackend(createWorker: () => Worker): WorkerPoolBackend {
  const worker = createWorker();
  let disposed = false;
  let pending: { readonly resolve: (value: unknown) => void; readonly reject: (err: unknown) => void } | null = null;

  worker.onmessage = (event: MessageEvent<IndicatorWorkerResponse>) => {
    const current = pending;
    if (!current) return;
    pending = null;
    if (event.data.ok) current.resolve(event.data.result);
    else current.reject(new Error(event.data.error));
  };
  worker.onerror = (event: ErrorEvent) => {
    const current = pending;
    if (!current) return;
    pending = null;
    current.reject(new Error(event.message || "worker crashed"));
  };

  return {
    run<TArgs, TResult>(task: string, args: TArgs): Promise<TResult> {
      if (disposed) {
        return Promise.reject(new WorkerPoolError("WORKER_POOL_DISPOSED", task, "backend is disposed"));
      }
      return new Promise<TResult>((resolve, reject) => {
        pending = { resolve: resolve as (value: unknown) => void, reject };
        const message: IndicatorWorkerMessage = { task, args };
        worker.postMessage(message);
      });
    },
    dispose(): void {
      if (disposed) return;
      disposed = true;
      worker.terminate();
    },
  };
}
