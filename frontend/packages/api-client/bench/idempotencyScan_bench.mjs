#!/usr/bin/env node
/**
 * task-4973 — replaces the flaky fixed-wall-clock assertion this bench's
 * sibling test file (../src/idempotencyScan.test.ts) used to carry
 * ("clients/*.ts 전수 스캔이 200ms 이내", later relaxed to 1000ms in
 * a22cdb09/task-4968). That assertion failed under `npm run test
 * --workspaces` (5 workspaces' vitest worker threads racing for the same
 * CPU inflate the TypeScript compiler's JIT/GC time non-linearly) even
 * though the scanner itself had not regressed -- a unit test is the wrong
 * place for a host-load-sensitive wall-clock budget (same conclusion
 * task-1038/1405/920 reached for chart-engine's density bench, see
 * packages/chart-engine/bench/density_bench.mjs's docstring).
 *
 * Ratio-to-calib gate: idempotencyScanRatchet.mjs#decideBenchOutcome. No
 * absolute-ms assertion, no persisted baseline file -- see that module's
 * docstring for why this scan doesn't need either.
 *
 * Usage: node --experimental-strip-types bench/idempotencyScan_bench.mjs
 * Exit 0 = scan finished within RATIO_MULTIPLIER x the same-run calib probe.
 * Exit 1 = scan exceeded that budget.
 */
import { register } from "node:module";
import { decideBenchOutcome, measureCalibMs } from "./idempotencyScanRatchet.mjs";

register("../../../scripts/ts_esm_loader.mjs", import.meta.url);

const { listClientSourceFiles, scanCallSites } = await import("../src/idempotencyScan.ts");
const { API_ROUTES } = await import("../src/apiPaths.ts");

async function main() {
  const calibMs = measureCalibMs();

  const files = listClientSourceFiles();
  const t0 = performance.now();
  const result = scanCallSites(files, API_ROUTES);
  const scanMs = performance.now() - t0;

  if (
    result.markedWithoutIdempotentCall.length > 0
    || result.markedButNonIdempotentCall.length > 0
    || result.idempotentCallButUnmarked.length > 0
  ) {
    // 기능 회귀는 idempotencyScan.test.ts의 몫이지만, bench가 깨진 스캐너로
    // 조용히 "빠르다"고 통과 처리하지 않도록 여기서도 fail-closed로 막는다.
    console.error("[idempotency-scan-bench] FAIL: scan produced violations (see idempotencyScan.test.ts):", JSON.stringify(result));
    return 1;
  }

  console.log(`[idempotency-scan-bench] ${files.length} clients/*.ts files, calibMs=${calibMs.toFixed(3)}, scanMs=${scanMs.toFixed(3)}`);
  const outcome = decideBenchOutcome(scanMs, calibMs);
  console[outcome.ok ? "log" : "error"](outcome.message);
  return outcome.exitCode;
}

main()
  .then((code) => (process.exitCode = code))
  .catch((err) => {
    console.error("[idempotency-scan-bench] error:", err);
    process.exitCode = 1;
  });
