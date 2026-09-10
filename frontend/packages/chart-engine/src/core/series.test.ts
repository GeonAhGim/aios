import { describe, expect, it } from "vitest";
import { createNullRendererBackend, createRenderer } from "./renderer";
import { createChartEngine, sortedInsertOrReplace, type CandlePoint, type SeriesBackend, type SeriesOptions } from "./series";

function candle(time: number, close: number): CandlePoint {
  return { time, open: close, high: close, low: close, close };
}

function trackingBackendFactory() {
  const calls: { setData: CandlePoint[][]; update: CandlePoint[]; removed: string[] } = {
    setData: [],
    update: [],
    removed: [],
  };
  const factory = (options: SeriesOptions): SeriesBackend => ({
    setData(points) {
      calls.setData.push([...points]);
    },
    update(point) {
      calls.update.push(point);
    },
    remove() {
      calls.removed.push(options.id);
    },
  });
  return { factory, calls };
}

describe("ChartEngine series lifecycle", () => {
  it("creates a series and exposes it via getSeries", () => {
    const engine = createChartEngine();
    const handle = engine.createSeries({ id: "s1", type: "candlestick" });

    expect(handle.id).toBe("s1");
    expect(handle.type).toBe("candlestick");
    expect(handle.data).toEqual([]);
    expect(engine.getSeries("s1")).toBe(handle);
  });

  it("rejects creating a series with a duplicate id", () => {
    const engine = createChartEngine();
    engine.createSeries({ id: "dup", type: "line" });
    expect(() => engine.createSeries({ id: "dup", type: "line" })).toThrow(/already exists/);
  });

  it("setData sorts by time and delegates to the series backend", () => {
    const { factory, calls } = trackingBackendFactory();
    const engine = createChartEngine({ seriesBackendFactory: factory });
    const handle = engine.createSeries({ id: "s1", type: "candlestick" });

    handle.setData([candle(200, 20), candle(100, 10)]);

    expect(handle.data).toEqual([candle(100, 10), candle(200, 20)]);
    expect(calls.setData).toEqual([[candle(100, 10), candle(200, 20)]]);
  });

  it("update replaces an existing point at the same time and inserts new points in order", () => {
    const { factory, calls } = trackingBackendFactory();
    const engine = createChartEngine({ seriesBackendFactory: factory });
    const handle = engine.createSeries({ id: "s1", type: "candlestick" });
    handle.setData([candle(100, 10), candle(300, 30)]);

    handle.update(candle(100, 11));
    expect(handle.data).toEqual([candle(100, 11), candle(300, 30)]);

    handle.update(candle(200, 20));
    expect(handle.data).toEqual([candle(100, 11), candle(200, 20), candle(300, 30)]);

    expect(calls.update).toEqual([candle(100, 11), candle(200, 20)]);
  });

  it("removeSeries delegates to the backend and drops it from lookup", () => {
    const { factory, calls } = trackingBackendFactory();
    const engine = createChartEngine({ seriesBackendFactory: factory });
    engine.createSeries({ id: "s1", type: "line" });

    engine.removeSeries("s1");

    expect(calls.removed).toEqual(["s1"]);
    expect(engine.getSeries("s1")).toBeUndefined();
    expect(() => engine.removeSeries("s1")).not.toThrow();
  });

  it("resize propagates to renderer, timeScale, and priceScale together", () => {
    const engine = createChartEngine();
    engine.timeScale.setRange({ from: 0, to: 1000 });
    engine.priceScale.setRange({ min: 0, max: 100 });

    engine.resize({ width: 500, height: 250 });

    expect(engine.renderer.size).toEqual({ width: 500, height: 250 });
    expect(engine.timeScale.width).toBe(500);
    expect(engine.priceScale.height).toBe(250);
    expect(engine.timeScale.timeToX(500)).toBe(250);
    expect(engine.priceScale.priceToY(50)).toBe(125);
  });

  it("dispose removes all series (via backend) and disposes the renderer, then rejects further use", () => {
    const { factory, calls } = trackingBackendFactory();
    const engine = createChartEngine({ seriesBackendFactory: factory });
    engine.createSeries({ id: "s1", type: "line" });
    engine.createSeries({ id: "s2", type: "histogram" });

    engine.dispose();

    expect(calls.removed.sort()).toEqual(["s1", "s2"]);
    expect(() => engine.createSeries({ id: "s3", type: "line" })).toThrow(/disposed/);
    expect(() => engine.resize({ width: 1, height: 1 })).toThrow(/disposed/);
    expect(() => engine.dispose()).not.toThrow();
  });
});

// DEEPEN(task-3069) of task-1375 (CH-1a, commit 87da8d1), per DEPTH_CH audit
// (task-2729, docs/audit/DEPTH_CH.md): the original leaf had negative-path
// coverage (duplicate id, disposed-after-use) but no failure injection, no
// numeric performance assertion, no gate-red reproduction, and no D3-level
// proof. This block fills those four gaps.
describe("ChartEngine — DEEPEN(task-3069): failure injection, perf, gate-red, D3", () => {
  it("실패 주입: a series backend that throws on setData()/update() propagates the error instead of being silently swallowed", () => {
    const factory = (): SeriesBackend => ({
      setData() {
        throw new Error("vendor rejected malformed bar data");
      },
      update() {
        throw new Error("vendor rejected malformed bar data");
      },
      remove() {},
    });
    const engine = createChartEngine({ seriesBackendFactory: factory });
    const handle = engine.createSeries({ id: "s1", type: "line" });

    expect(() => handle.setData([candle(100, 10)])).toThrow(/vendor rejected malformed bar data/);
    expect(() => handle.update(candle(200, 20))).toThrow(/vendor rejected malformed bar data/);
  });

  it("실패 주입 + 게이트 적색 재현: dispose() finishes tearing down every series and disposes the renderer even when one series' backend.remove() throws, then rethrows that error — the try/catch added around removeSeries() in dispose() is exactly what makes this pass; removing it would abort the loop early and leave s2/renderer undisposed", () => {
    const { factory: goodFactory, calls } = trackingBackendFactory();
    let backendDisposeCalls = 0;
    const rendererBackend = { ...createNullRendererBackend(), dispose: () => { backendDisposeCalls += 1; } };
    const renderer = createRenderer({ backend: rendererBackend });
    const engine = createChartEngine({
      renderer,
      seriesBackendFactory: (options) => {
        if (options.id === "boom") {
          return {
            setData() {},
            update() {},
            remove() {
              throw new Error("vendor pane already torn down");
            },
          };
        }
        return goodFactory(options);
      },
    });
    engine.createSeries({ id: "boom", type: "line" });
    engine.createSeries({ id: "s2", type: "line" });

    expect(() => engine.dispose()).toThrow(/vendor pane already torn down/);

    expect(calls.removed).toEqual(["s2"]);
    expect(engine.getSeries("boom")).toBeUndefined();
    expect(engine.getSeries("s2")).toBeUndefined();
    expect(backendDisposeCalls).toBe(1);
    // Idempotent even after a failed dispose: disposed was already latched true.
    expect(() => engine.dispose()).not.toThrow();
  });

  it("게이트 적색 재현: removing a series and re-creating it under the same id succeeds — if removeSeries() forgot to delete the id from the internal map, this would wrongly throw 'already exists'", () => {
    const engine = createChartEngine();
    engine.createSeries({ id: "s1", type: "line" });
    engine.removeSeries("s1");

    expect(() => engine.createSeries({ id: "s1", type: "candlestick" })).not.toThrow();
    expect(engine.getSeries("s1")?.type).toBe("candlestick");
  });

  it("수치 성능: setData with 10,000 points followed by 500 sequential live-bar updates stays under a 4s budget", () => {
    const points: CandlePoint[] = Array.from({ length: 10_000 }, (_, i) => candle(i * 60, 100 + (i % 50)));
    const engine = createChartEngine();
    const handle = engine.createSeries({ id: "s1", type: "candlestick" });

    const startedAt = performance.now();
    handle.setData(points);
    for (let i = 0; i < 500; i++) handle.update(candle(10_000 * 60 + i * 60, 200 + i));
    const elapsedMs = performance.now() - startedAt;

    expect(handle.data.length).toBe(10_500);
    expect(elapsedMs).toBeLessThan(4000);
  });

  it("수치 성능: sortedInsertOrReplace holds up over 3,000 sequential appends (O(n) per call) within a 3s budget", () => {
    let data: readonly CandlePoint[] = [];
    const startedAt = performance.now();
    for (let i = 0; i < 3000; i++) data = sortedInsertOrReplace(data, candle(i, i));
    const elapsedMs = performance.now() - startedAt;

    expect(data.length).toBe(3000);
    expect(elapsedMs).toBeLessThan(3000);
  });

  it("다중 인스턴스(D3): two independent ChartEngines never leak series or dispose state across each other", () => {
    const { factory: factoryA, calls: callsA } = trackingBackendFactory();
    const { factory: factoryB, calls: callsB } = trackingBackendFactory();
    const engineA = createChartEngine({ seriesBackendFactory: factoryA });
    const engineB = createChartEngine({ seriesBackendFactory: factoryB });

    engineA.createSeries({ id: "shared-id", type: "line" });
    engineB.createSeries({ id: "shared-id", type: "candlestick" });
    engineA.dispose();

    expect(callsA.removed).toEqual(["shared-id"]);
    expect(callsB.removed).toEqual([]);
    expect(engineA.getSeries("shared-id")).toBeUndefined();
    expect(engineB.getSeries("shared-id")?.type).toBe("candlestick");
    expect(() => engineB.createSeries({ id: "other", type: "line" })).not.toThrow();
    expect(() => engineA.createSeries({ id: "other", type: "line" })).toThrow(/disposed/);
  });
});
