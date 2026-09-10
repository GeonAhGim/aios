// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createKlinechartsChartEngine } from "./klinechartsBackend";
import { AIOS_HISTOGRAM_INDICATOR, AIOS_LINE_INDICATOR } from "./klinechartsSeries";
import { PaneIdConstants } from "./klinecharts";
import type { CandlePoint } from "./series";
import { installVendorDomStubs } from "./testing/vendorDomStubs";

function candle(time: number, close: number): CandlePoint {
  return { time, open: close - 1, high: close + 1, low: close - 2, close, volume: 10 };
}

const BASE: readonly CandlePoint[] = [candle(60_000, 10), candle(120_000, 11), candle(180_000, 12)];
const SIZE = { width: 800, height: 400 };

let restoreStubs: () => void;
let container: HTMLDivElement;

beforeEach(() => {
  restoreStubs = installVendorDomStubs();
  vi.spyOn(console, "log").mockImplementation(() => {}); // vendor prints a welcome banner on init
  container = document.createElement("div");
  document.body.appendChild(container);
});

afterEach(() => {
  container.remove();
  restoreStubs();
  vi.restoreAllMocks();
});

function mountEngine() {
  return createKlinechartsChartEngine({ container, initialSize: SIZE });
}

function vendorTimestamps(engine: ReturnType<typeof mountEngine>): number[] {
  return engine.vendor.chart!.getDataList().map((bar) => bar.timestamp);
}

describe("createKlinechartsChartEngine — vendor delegation", () => {
  it("mounts a real klinecharts instance on the container and tears it down on dispose", () => {
    const engine = mountEngine();

    expect(engine.vendor.chart).not.toBeNull();
    expect(container.getAttribute("k-line-chart-id")).toMatch(/^k_line_chart_/);
    expect(container.querySelectorAll("canvas").length).toBeGreaterThan(0);

    engine.dispose();

    expect(engine.vendor.chart).toBeNull();
    expect(container.getAttribute("k-line-chart-id")).toBeNull();
    expect(() => engine.dispose()).not.toThrow();
  });

  it("candlestick setData lands in the vendor data list", () => {
    const engine = mountEngine();
    const series = engine.createSeries({ id: "main", type: "candlestick" });

    series.setData(BASE);

    expect(vendorTimestamps(engine)).toEqual([60_000, 120_000, 180_000]);
    expect(engine.vendor.chart!.getDataList()[2]).toMatchObject({ close: 12, volume: 10 });
  });

  it("update appends, replaces the tail, and replays for out-of-order points", () => {
    const engine = mountEngine();
    const series = engine.createSeries({ id: "main", type: "candlestick" });
    series.setData(BASE);

    series.update(candle(240_000, 13)); // append via live bar
    expect(vendorTimestamps(engine)).toEqual([60_000, 120_000, 180_000, 240_000]);

    series.update(candle(240_000, 14)); // replace tail via live bar
    expect(engine.vendor.chart!.getDataList().at(-1)).toMatchObject({ close: 14 });

    series.update(candle(90_000, 9)); // earlier bar: full replay keeps order
    expect(vendorTimestamps(engine)).toEqual([60_000, 90_000, 120_000, 180_000, 240_000]);
    expect(engine.vendor.chart!.getDataList()[1]).toMatchObject({ close: 9 });
    expect(series.data.map((p) => p.time)).toEqual(vendorTimestamps(engine));
  });

  it("resize propagates to the vendor chart bounding", () => {
    const engine = mountEngine();

    engine.resize({ width: 640, height: 320 });

    expect(engine.renderer.size).toEqual({ width: 640, height: 320 });
    expect(engine.vendor.chart!.getSize()).toMatchObject({ width: 640, height: 320 });
  });

  it("series created before mount are replayed when the chart appears", () => {
    const engine = createKlinechartsChartEngine();
    const series = engine.createSeries({ id: "main", type: "candlestick" });
    series.setData(BASE);
    expect(engine.vendor.chart).toBeNull();

    engine.renderer.mount(container);

    expect(vendorTimestamps(engine)).toEqual([60_000, 120_000, 180_000]);
  });

  it("removing the candlestick series empties the vendor data list", () => {
    const engine = mountEngine();
    engine.createSeries({ id: "main", type: "candlestick" }).setData(BASE);

    engine.removeSeries("main");

    expect(vendorTimestamps(engine)).toEqual([]);
  });

  it("line and histogram series become vendor indicators fed by the series' closes", async () => {
    const engine = mountEngine();
    engine.createSeries({ id: "main", type: "candlestick" }).setData(BASE);
    const line = engine.createSeries({ id: "ema", type: "line" });
    const bars = engine.createSeries({ id: "vol", type: "histogram" });
    line.setData([candle(60_000, 100), candle(120_000, 101), candle(180_000, 102)]);
    bars.setData([candle(60_000, 5), candle(180_000, 7)]);

    const chart = engine.vendor.chart!;
    const lineIndicator = chart.getIndicators({ name: AIOS_LINE_INDICATOR })[0];
    const barIndicator = chart.getIndicators({ name: AIOS_HISTOGRAM_INDICATOR })[0];
    expect(lineIndicator.paneId).toBe(PaneIdConstants.CANDLE);
    expect(barIndicator.paneId).not.toBe(PaneIdConstants.CANDLE);

    await vi.waitFor(() => {
      expect((lineIndicator.result as Array<{ value?: number }>).map((r) => r.value)).toEqual([100, 101, 102]);
      expect((barIndicator.result as Array<{ value?: number }>).map((r) => r.value)).toEqual([5, undefined, 7]);
    });

    line.update(candle(180_000, 200));
    await vi.waitFor(() => {
      expect((lineIndicator.result as Array<{ value?: number }>).map((r) => r.value)).toEqual([100, 101, 200]);
    });

    engine.removeSeries("ema");
    engine.removeSeries("vol");
    expect(chart.getIndicators({ name: AIOS_LINE_INDICATOR })).toEqual([]);
    expect(chart.getIndicators({ name: AIOS_HISTOGRAM_INDICATOR })).toEqual([]);
  });

  it("timeScale and priceScale delegate coordinate conversion to the vendor once data exists", async () => {
    const engine = mountEngine();
    const chart = engine.vendor.chart!;
    const pane = { paneId: PaneIdConstants.CANDLE };

    // no data yet: linear fallback answers
    engine.timeScale.setRange({ from: 0, to: 800 });
    expect(engine.timeScale.timeToX(400)).toBe(400);

    engine.createSeries({ id: "main", type: "candlestick" }).setData(BASE);

    const x = engine.timeScale.timeToX(120_000);
    const vendorX = (chart.convertToPixel({ timestamp: 120_000 }, pane) as { x?: number }).x;
    expect(Number.isFinite(x)).toBe(true);
    expect(x).toBe(vendorX);
    expect(x).not.toBe(120_000); // clearly not the linear mapping
    expect(engine.timeScale.xToTime(x)).toBe(120_000);

    // The vendor computes the y-axis auto range in its deferred layout pass
    // (microtask-batched `layout()`); until then the linear fallback answers.
    engine.priceScale.setRange({ min: 0, max: 20 });
    expect(engine.priceScale.priceToY(11)).toBe((1 - 11 / 20) * SIZE.height);

    await vi.waitFor(() => {
      expect(Number.isFinite((chart.convertToPixel({ value: 11 }, pane) as { y?: number }).y)).toBe(true);
    });
    const y = engine.priceScale.priceToY(11);
    const vendorY = (chart.convertToPixel({ value: 11 }, pane) as { y?: number }).y;
    expect(y).toBe(vendorY);
    expect(y).not.toBe((1 - 11 / 20) * SIZE.height);
    expect(engine.priceScale.yToPrice(y)).toBeCloseTo(11, 1); // vendor rounds y to whole pixels
  });
});

// DEEPEN(task-3072) of task-1501 (CH-1b, commit 03f5929), per DEPTH_CH audit
// (task-2729, docs/audit/DEPTH_CH.md): the original leaf had 8 passing tests
// exercising real vendor delegation through jsdom stubs, but zero
// negative-path coverage, no failure injection, no numeric performance
// assertion, and no gate-red reproduction. This block fills those gaps
// against the *real* vendor Chart object (never mocked) — failure injection
// spies on genuine vendor methods to prove KlinechartsBackend never swallows
// a vendor error, and the gate-red tests would fail if the mount/remount
// branching in klinechartsBackend.ts were simplified away.
describe("createKlinechartsChartEngine — DEEPEN(task-3072): negative path, failure injection, perf, gate-red, D3", () => {
  it("negative: renderer.mount(null) tears the chart down instead of throwing", () => {
    const engine = mountEngine();

    expect(() => engine.renderer.mount(null)).not.toThrow();

    expect(engine.vendor.chart).toBeNull();
    expect(container.getAttribute("k-line-chart-id")).toBeNull();
  });

  it("negative: removeSeries on an id that was never created is a silent no-op against the vendor", () => {
    const engine = mountEngine();

    expect(() => engine.removeSeries("missing")).not.toThrow();

    expect(engine.vendor.chart!.getDataList()).toEqual([]);
  });

  it("negative: creating a second series with a duplicate id throws before the vendor is touched", () => {
    const engine = mountEngine();
    engine.createSeries({ id: "main", type: "candlestick" });
    const indicatorsBefore = engine.vendor.chart!.getIndicators().length;

    expect(() => engine.createSeries({ id: "main", type: "line" })).toThrow(/already exists/);

    expect(engine.vendor.chart!.getIndicators().length).toBe(indicatorsBefore);
  });

  it("실패 주입: a real vendor createIndicator() failure propagates out of createSeries instead of being swallowed", () => {
    const engine = mountEngine();
    engine.createSeries({ id: "main", type: "candlestick" }).setData(BASE);
    vi.spyOn(engine.vendor.chart!, "createIndicator").mockImplementation(() => {
      throw new Error("vendor: indicator engine exhausted");
    });

    expect(() => engine.createSeries({ id: "ema", type: "line" })).toThrow(/indicator engine exhausted/);
  });

  it("실패 주입: a real vendor resetData() failure propagates out of series.setData instead of being swallowed", () => {
    const engine = mountEngine();
    const series = engine.createSeries({ id: "main", type: "candlestick" });
    vi.spyOn(engine.vendor.chart!, "resetData").mockImplementation(() => {
      throw new Error("vendor: loader rejected");
    });

    expect(() => series.setData(BASE)).toThrow(/loader rejected/);
  });

  it("실패 주입: a real vendor setDataLoader() failure propagates out of createSeries when attaching a candlestick binding", () => {
    const engine = mountEngine();
    vi.spyOn(engine.vendor.chart!, "setDataLoader").mockImplementation(() => {
      throw new Error("vendor: loader rejected on attach");
    });

    expect(() => engine.createSeries({ id: "main", type: "candlestick" })).toThrow(/loader rejected on attach/);
  });

  it("실패 주입: a real vendor resize() failure propagates out of engine.resize instead of being swallowed", () => {
    const engine = mountEngine();
    vi.spyOn(engine.vendor.chart!, "resize").mockImplementation(() => {
      throw new Error("vendor: canvas context lost");
    });

    expect(() => engine.resize({ width: 100, height: 100 })).toThrow(/canvas context lost/);
  });

  it("게이트 적색 재현: remounting to a different container disposes the old vendor chart instead of leaking it", () => {
    const engine = mountEngine();
    const oldChart = engine.vendor.chart!;
    const containerB = document.createElement("div");
    document.body.appendChild(containerB);

    engine.renderer.mount(containerB);

    expect(container.getAttribute("k-line-chart-id")).toBeNull(); // old container cleaned up, not leaked
    expect(engine.vendor.chart).not.toBe(oldChart);
    expect(engine.vendor.chart!.getDataList()).toEqual([]);

    engine.dispose();
    containerB.remove();
  });

  it("게이트 적색 재현: remounting the exact same container is a no-op that preserves vendor chart identity and data", () => {
    const engine = mountEngine();
    engine.createSeries({ id: "main", type: "candlestick" }).setData(BASE);
    const chartBefore = engine.vendor.chart;

    engine.renderer.mount(container);

    expect(engine.vendor.chart).toBe(chartBefore); // not torn down and recreated
    expect(vendorTimestamps(engine)).toEqual([60_000, 120_000, 180_000]);
  });

  it("수치 성능: 2,000 live-bar updates through the real vendor candle loader stay under a 3000ms budget", () => {
    const engine = mountEngine();
    const series = engine.createSeries({ id: "main", type: "candlestick" });
    series.setData(BASE);

    const startedAt = performance.now();
    for (let i = 0; i < 2000; i++) {
      series.update(candle(180_000 + (i + 1) * 60_000, 12 + (i % 7)));
    }
    const elapsedMs = performance.now() - startedAt;

    expect(vendorTimestamps(engine)).toHaveLength(3 + 2000);
    expect(elapsedMs).toBeLessThan(3000);
  });

  it("어드버서리얼(D3): two independently mounted engines never share vendor chart state or DOM ids", () => {
    const containerB = document.createElement("div");
    document.body.appendChild(containerB);
    const engineA = mountEngine();
    const engineB = createKlinechartsChartEngine({ container: containerB, initialSize: SIZE });

    engineA.createSeries({ id: "main", type: "candlestick" }).setData(BASE);
    engineB.createSeries({ id: "main", type: "candlestick" }).setData([candle(60_000, 99)]);

    expect(engineA.vendor.chart).not.toBe(engineB.vendor.chart);
    expect(container.getAttribute("k-line-chart-id")).not.toBe(containerB.getAttribute("k-line-chart-id"));
    expect(engineA.vendor.chart!.getDataList()).toHaveLength(3);
    expect(engineB.vendor.chart!.getDataList()).toHaveLength(1);

    engineA.dispose();
    expect(engineB.vendor.chart).not.toBeNull();
    expect(engineB.vendor.chart!.getDataList()).toHaveLength(1);

    engineB.dispose();
    containerB.remove();
  });
});
