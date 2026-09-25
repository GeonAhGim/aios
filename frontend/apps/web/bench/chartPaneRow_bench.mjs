#!/usr/bin/env node
/**
 * task-5419 -- replaces the flaky fixed-wall-clock assertion this bench's
 * sibling test file (../src/routes/chart/ChartPaneRow.test.tsx) used to
 * carry ("고밀도 렌더: 매우 큰 plotLayer 결과(노드 1000개+)도 100ms 내로 렌더된다").
 * That assertion failed on the shared GitHub-hosted Actions runner (run
 * 35795467536, f0fbb4ba: expected 348.2 to be less than 100) with no
 * functional regression in ChartPaneRow -- a fixed-ms budget in a unit test
 * is the wrong place for a host-load-sensitive wall-clock figure (same
 * conclusion task-1038/1405/920 reached for chart-engine's density bench,
 * see packages/chart-engine/bench/density_bench.mjs's docstring, and what
 * task-4973 applied to packages/api-client/bench/idempotencyScan_bench.mjs).
 * The functional assertions (1000+ nodes all render, error-free) stay in
 * ChartPaneRow.test.tsx unchanged.
 *
 * Unlike density_bench.mjs/idempotencyScan_bench.mjs, this bench renders a
 * real React component tree and therefore needs both a DOM (jsdom, installed
 * onto `globalThis` below -- Node has no native DOM) and a JSX/TSX transform.
 * Node's `--experimental-strip-types` only strips TypeScript *types*, not
 * JSX syntax, so the resolver-hook approach those two benches use can't load
 * ChartPaneRow.tsx directly. Instead this loads the render workload
 * (chartPaneRowBenchRunner.tsx) through Vite's own SSR module loader
 * (`vite.ssrLoadModule`), reusing this app's real vite.config.ts (the same
 * @vitejs/plugin-react transform the app build and its vitest suite already
 * go through) instead of a second, hand-rolled JSX pipeline that could drift
 * from what actually ships.
 *
 * Ratio-to-calib gate: chartPaneRowRatchet.mjs#decideBenchOutcome. No
 * absolute-ms assertion, no persisted baseline file -- same reasoning as
 * idempotencyScanRatchet.mjs's docstring: this is a single render
 * measurement, not a multi-metric pipeline, so a baseline-drift ratchet
 * would be more machinery than the signal warrants.
 *
 * Usage: npm run bench:chart-pane-row --workspace=apps/web
 * Exit 0 = render finished within RATIO_MULTIPLIER x the same-run calib probe.
 * Exit 1 = render exceeded that budget.
 */
import { dirname, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import { createServer } from "vite";
import { decideBenchOutcome, measureCalibMs } from "./chartPaneRowRatchet.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolvePath(HERE, "..");

/**
 * Minimal `global-jsdom`-style shim: copy every own property of a fresh
 * jsdom Window onto `globalThis` so react-dom/client's `createRoot` sees a
 * DOM (`document`, `Node`, `SVGElement`, ...) exactly like vitest's
 * `environment: "jsdom"` (vite.config.ts) gives the real ChartPaneRow.test.tsx
 * suite. Read-only/already-defined globals (e.g. `global`, `process`) are
 * skipped rather than erroring. `performance` is deliberately left as
 * Node's native implementation, not copied from jsdom -- jsdom's own
 * `Performance.now()` (lib/jsdom/living/hr-time/Performance-impl.js) reads
 * the *global* `performance` unqualified, so once that global itself became
 * the jsdom object it recursed into itself infinitely (observed as a
 * `RangeError: Maximum call stack size exceeded` the moment anything, e.g.
 * Vite's own startup timing, called `performance.now()`). Nothing here
 * needs jsdom's Performance implementation anyway -- `measureCalibMs` and
 * `measureRenderMs` both just want a monotonic clock, which Node's native
 * `performance.now()` already provides. The same self-reference trap hits
 * jsdom's timer implementation (`Window.js`'s `setTimeout`/`setInterval`
 * also call the unqualified global versions of themselves), so those --
 * and their `clear*` counterparts -- are excluded too; Node's native timers
 * work fine for anything react-dom/client schedules here.
 */
const JSDOM_GLOBAL_EXCLUSIONS = new Set([
  "global",
  "globalThis",
  "process",
  "performance",
  "setTimeout",
  "clearTimeout",
  "setInterval",
  "clearInterval",
  "queueMicrotask",
]);

function installJsdomGlobals() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/" });
  for (const key of Object.getOwnPropertyNames(dom.window)) {
    if (JSDOM_GLOBAL_EXCLUSIONS.has(key)) continue;
    try {
      globalThis[key] = dom.window[key];
    } catch {
      // Some Window properties (e.g. a handful of read-only accessors) can't
      // be copied onto globalThis -- harmless, react-dom doesn't need them.
    }
  }
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  return dom;
}

async function main() {
  // Vite's own config bundler (rolldown) and dev-server startup use Node's
  // native `performance`/timer globals; installing jsdom's globals first
  // clobbers those with jsdom's Window-bound Performance implementation,
  // which recurses infinitely once invoked outside jsdom's own window
  // context. Create the server against the real Node globals, then swap in
  // the jsdom DOM only for the render step that actually needs it.
  const vite = await createServer({
    root: APP_ROOT,
    configFile: resolvePath(APP_ROOT, "vite.config.ts"),
    server: { middlewareMode: true },
    appType: "custom",
    logLevel: "warn",
  });
  try {
    installJsdomGlobals();
    const { measureRenderMs } = await vite.ssrLoadModule("/bench/chartPaneRowBenchRunner.tsx");

    const calibMs = measureCalibMs();
    const renderMs = measureRenderMs();

    console.log(`[chart-pane-row-bench] ${1000}-node ChartPaneRow render, calibMs=${calibMs.toFixed(3)}, renderMs=${renderMs.toFixed(3)}`);
    const outcome = decideBenchOutcome(renderMs, calibMs);
    console[outcome.ok ? "log" : "error"](outcome.message);
    return outcome.exitCode;
  } finally {
    await vite.close();
  }
}

main()
  .then((code) => (process.exitCode = code))
  .catch((err) => {
    console.error("[chart-pane-row-bench] error:", err);
    process.exitCode = 1;
  });
