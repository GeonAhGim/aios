// CH-18e — the code that runs inside the Web Worker thread `indicatorComputePool.ts`
// spins up for client indicator compute. Vite builds this as a separate worker
// bundle (`new Worker(new URL("./indicatorWorker.ts", import.meta.url))`), so this
// file's module graph is a fresh worker realm, not the main thread's — it re-imports
// clientEngine.ts rather than sharing any state with the main-thread copy.
import {
  computeIndicatorSeries,
  CLIENT_ENGINE_COMPUTE_TASK,
  type ComputeIndicatorSeriesArgs,
} from "@aios/chart-engine/src/compute/clientEngine";
import type { IndicatorWorkerMessage, IndicatorWorkerResponse } from "@aios/chart-engine/src/compute/workerPool";

self.onmessage = (event: MessageEvent<IndicatorWorkerMessage>) => {
  const { task, args } = event.data;
  let response: IndicatorWorkerResponse;
  try {
    if (task !== CLIENT_ENGINE_COMPUTE_TASK) {
      throw new Error(`indicatorWorker: unknown task "${task}"`);
    }
    response = { ok: true, result: computeIndicatorSeries(args as ComputeIndicatorSeriesArgs) };
  } catch (err) {
    response = { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
  (self as unknown as Worker).postMessage(response);
};
