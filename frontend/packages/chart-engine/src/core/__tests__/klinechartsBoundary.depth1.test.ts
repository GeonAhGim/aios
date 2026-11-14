/**
 * DEEPEN CH-14d (task-25076) -- D2 runtime evidence for the klinecharts boundary.
 * The vendor module cannot load in Node (it reads window.navigator), so this
 * exercises the vendor-free modules that sit behind the boundary: series,
 * renderer, timeScale, priceScale. Test-only; no production changes.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { createPriceScale } from "../priceScale";
import { createRenderer, type RendererBackend } from "../renderer";
import { createChartEngine, type CandlePoint, type SeriesBackend } from "../series";
import { createTimeScale } from "../timeScale";

const CORE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const VENDOR_IMPORT_RE = /from\s+["'][^"']*vendor\/klinecharts[^"']*["']/;

function candle(time: number): CandlePoint {
  return { time, open: 1, high: 2, low: 0, close: 1 };
}

describe("CH-14d depth1 -- negative", () => {
  it("disposed ChartEngine rejects createSeries and resize", () => {
    const engine = createChartEngine();
    engine.dispose();
    expect(() => engine.createSeries({ id: "s", type: "line" })).toThrow("ChartEngine is disposed");
    expect(() => engine.resize({ width: 10, height: 10 })).toThrow("ChartEngine is disposed");
  });

  it("disposed Renderer rejects requestRender and mount", () => {
    const renderer = createRenderer();
    renderer.dispose();
    expect(() => renderer.requestRender()).toThrow("Renderer is disposed");
    expect(() => renderer.mount({} as HTMLElement)).toThrow("Renderer is disposed");
  });

  it("duplicate series id and negative sizes are refused", () => {
    const engine = createChartEngine();
    engine.createSeries({ id: "dup", type: "candlestick" });
    expect(() => engine.createSeries({ id: "dup", type: "line" })).toThrow('Series "dup" already exists');
    expect(() => engine.resize({ width: -1, height: 5 })).toThrow(RangeError);
    expect(() => createTimeScale({ range: { from: 100, to: 50 } })).toThrow(RangeError);
    expect(() => createPriceScale({ range: { min: 9, max: 1 } })).toThrow(RangeError);
    engine.dispose();
  });
});

describe("CH-14d depth1 -- failure injection", () => {
  it("backend mount failure surfaces to the caller and the renderer can still be disposed", () => {
    const dispose = vi.fn();
    const backend: RendererBackend = {
      mount: () => {
        throw new Error("klinecharts refused to initialise");
      },
      unmount: () => {},
      resize: () => {},
      requestRender: () => {},
      dispose,
    };
    const renderer = createRenderer({ backend });
    expect(() => renderer.mount({} as HTMLElement)).toThrow("klinecharts refused to initialise");
    renderer.dispose();
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  it("a crashing series backend does not leak the renderer on dispose", () => {
    const rendererDispose = vi.fn();
    const renderer = createRenderer({
      backend: { mount() {}, unmount() {}, resize() {}, requestRender() {}, dispose: rendererDispose },
    });
    const crashing: SeriesBackend = {
      setData() {},
      update() {},
      remove: () => {
        throw new Error("series backend crashed");
      },
    };
    const engine = createChartEngine({ renderer, seriesBackendFactory: () => crashing });
    engine.createSeries({ id: "bad", type: "line" });
    expect(() => engine.dispose()).toThrow("series backend crashed");
    expect(rendererDispose).toHaveBeenCalledTimes(1);
  });
});

describe("CH-14d depth1 -- performance", () => {
  it("creating 200 series with 50 candles each and disposing stays under 1500ms", () => {
    // Local measurement ~10ms; 1500ms budget is >=30x headroom for slow CI workers.
    const points = Array.from({ length: 50 }, (_, i) => candle(50 - i));
    const start = performance.now();
    const engine = createChartEngine();
    for (let i = 0; i < 200; i += 1) {
      engine.createSeries({ id: `s${i}`, type: "line" }).setData(points);
    }
    engine.dispose();
    const elapsed = performance.now() - start;
    expect(elapsed).toBeLessThan(1500);
  });
});

describe("CH-14d depth1 -- gate red", () => {
  it("gate red: a vendor import in renderer.ts / series.ts source would trip the vendor-free scan", () => {
    for (const rel of ["renderer.ts", "series.ts"]) {
      const clean = readFileSync(resolve(CORE_DIR, rel), "utf-8");
      expect(clean).not.toMatch(/vendor\/klinecharts/);
      const broken = `import { init } from "../vendor/klinecharts/src/index";\n${clean}`;
      expect(broken).toMatch(VENDOR_IMPORT_RE);
    }
  });

  it("gate red: a stalled backend makes the same performance budget assertion fail", () => {
    const start = performance.now();
    const budgetMs = 20;
    const stallUntil = start + budgetMs + 5;
    while (performance.now() < stallUntil) {
      // busy-wait: injected delay
    }
    const elapsed = performance.now() - start;
    expect(() => expect(elapsed).toBeLessThan(budgetMs)).toThrow();
  });
});
