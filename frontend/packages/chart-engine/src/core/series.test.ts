import { describe, expect, it } from "vitest";
import { createChartEngine, type CandlePoint, type SeriesBackend, type SeriesOptions } from "./series";

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
