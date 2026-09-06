/**
 * CH-18a — worker pool port: dispatches named compute tasks (e.g.
 * `clientEngine.ts` `CLIENT_ENGINE_COMPUTE_TASK`) so a full-history client
 * indicator recompute doesn't block the render thread.
 *
 * Same dependency-injected port shape as `plugins/indicatorPlugin.ts`
 * `IndicatorCatalogPort` / `layout/persistence.ts` `ChartingPort` (task
 * convention) — this package has no `Worker`/`postMessage` global
 * dependency. `createInlineWorkerPoolBackend` is the only backend this leaf
 * ships: it runs a task on a microtask instead of a real worker thread, so
 * it works in any environment (tests, SSR, a browser without Worker
 * support) and gives every caller the same async contract a real worker
 * would. Wiring an actual `Worker`/`worker_threads` backend — and any
 * fallback between backends — is CH-18b (task-1954) scope; this leaf only
 * proves the pool dispatches, round-robins, and that `dispose()` rejects
 * further submissions.
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

  function submit<TArgs, TResult>(task: string, args: TArgs): Promise<TResult> {
    if (disposed) {
      return Promise.reject(new WorkerPoolError("WORKER_POOL_DISPOSED", task, "pool is disposed"));
    }
    const backend = backends[nextIndex]!;
    nextIndex = (nextIndex + 1) % backends.length;
    return backend.run<TArgs, TResult>(task, args).catch((err: unknown) => {
      if (err instanceof WorkerPoolError) throw err;
      throw new WorkerPoolError("WORKER_POOL_TASK_FAILED", task, err instanceof Error ? err.message : String(err));
    });
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
