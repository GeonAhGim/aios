// CH-18e — the browser-only factory that gets IndicatorParityPanel a real Web
// Worker pool for client indicator compute. `workerPool.ts` deliberately stays a
// pure dependency-injected port with no `new Worker(...)` of its own (see its
// module docstring); this is the one call site that supplies that factory, using
// the `new Worker(new URL("./indicatorWorker.ts", import.meta.url))` form Vite's
// worker plugin statically analyzes.
//
// Returns `null` when `Worker` doesn't exist in this environment (SSR, an old
// browser, jsdom in tests) — `useIndicatorParityRows.ts` treats `null` as "compute
// synchronously on the main thread instead" and surfaces that as a visible note,
// never a silent fallback.
import { createBrowserWorkerPoolBackend, createWorkerPool, type WorkerPool } from "@aios/chart-engine/src/compute/workerPool";

export const DEFAULT_INDICATOR_WORKER_POOL_SIZE = 4;

function spawnIndicatorWorker(): Worker {
  return new Worker(new URL("./indicatorWorker.ts", import.meta.url), { type: "module" });
}

export function createIndicatorComputePool(poolSize: number = DEFAULT_INDICATOR_WORKER_POOL_SIZE): WorkerPool | null {
  if (typeof Worker === "undefined") return null;
  const backends = Array.from({ length: poolSize }, () => createBrowserWorkerPoolBackend(spawnIndicatorWorker));
  return createWorkerPool(backends);
}
