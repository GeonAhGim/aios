// task-5419 -- the actual render workload for chartPaneRow_bench.mjs, kept as
// its own .tsx module (loaded via `vite.ssrLoadModule`, see the driver
// script's docstring for why Vite SSR instead of a raw node loader) so the
// JSX/TSX here goes through the same @vitejs/plugin-react transform the app
// and its vitest suite already use -- no hand-rolled JSX transform, no risk
// of the bench exercising a different code path than production.
import "../src/i18n";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { ChartPaneRow } from "../src/routes/chart/ChartPaneRow";
import type { PlotLayerResult } from "../src/routes/chart/ChartPlotLayer";

/** Matches ChartPaneRow.test.tsx's former "고밀도 렌더" fixture size (1000+ nodes). */
const NODE_COUNT = 1000;

function buildHugePlotLayer(): PlotLayerResult {
  const nodes = Array.from({ length: NODE_COUNT }, (_, i) => (
    <circle key={i} cx={i} cy={100 + Math.sin(i) * 50} r={1} fill="#1f77b4" />
  ));
  return { nodes, issues: [] };
}

/**
 * Mounts ChartPaneRow with a 1000-node plotLayer and times the initial
 * commit. Outside of `@testing-library/react`'s `act()` wrapper (which the
 * old wall-clock test relied on, and which forces a synchronous flush),
 * React 19's `root.render` schedules the first commit through the
 * `scheduler` package instead of committing inline -- confirmed by probing
 * this exact jsdom setup: `root.render()` returns with the container still
 * empty, and the commit only lands on the next macrotask. `flushSync` is
 * `act()`'s own underlying mechanism for forcing that commit to happen
 * synchronously, so wrapping the call in it keeps this measuring the same
 * "time to commit" the removed test asserted on. Returns the elapsed ms.
 */
export function measureRenderMs(): number {
  const container = document.createElement("div");
  document.body.appendChild(container);
  try {
    const root = createRoot(container);
    const plotLayer = buildHugePlotLayer();
    const t0 = performance.now();
    flushSync(() => {
      root.render(
        <ChartPaneRow
          paneId="bench"
          rectHeight={240}
          heightRatio={0.6}
          isMain
          mainContent={<div>CANDLE_CHART</div>}
          subLabel="RSI"
          plotLayer={plotLayer}
          crosshairTimeMs={1_700_000_000_000}
          onMouseMove={() => {}}
          onMouseLeave={() => {}}
        />,
      );
    });
    const elapsed = performance.now() - t0;
    if (container.querySelectorAll("circle").length !== NODE_COUNT) {
      throw new Error(
        `measureRenderMs: expected ${NODE_COUNT} rendered <circle> nodes, found ${container.querySelectorAll("circle").length} -- render did not actually commit`,
      );
    }
    root.unmount();
    return elapsed;
  } finally {
    document.body.removeChild(container);
  }
}
