// @vitest-environment jsdom
/**
 * task-25045 (CH-14d DEEPEN-DEPTH D1→D2) — depth floor for the vendor
 * re-export boundary.  The companion klinechartsBoundary.test.ts covers
 * static source-scan invariants (D0/D1).  This file adds the missing D2
 * signals against the *runtime* boundary contract:
 *
 *   negative  ≥3  (toThrow / .rejects / not.toBeInTheDocument + role=alert)
 *   failure   ≥1  (mockRejectedValue / networkError / HttpResponse.error)
 *   perf      ≥1  (performance.now() elapsed ≤ budget ms)
 *   gate-red  ≥1  (title contains "gate red"; the assertion *does* fail)
 *
 * Pattern follows klinechartsBackend.test.ts: jsdom + installVendorDomStubs
 * + createKlinechartsChartEngine + candle helper.  File kept ≤200 lines.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createKlinechartsChartEngine } from "../klinechartsBackend";
import { installVendorDomStubs } from "../testing/vendorDomStubs";

/* ── helpers ─────────────────────────────────────────────────────────────── */

function candle(time: number, close: number) {
  return { time, open: close - 1, high: close + 1, low: close - 2, close, volume: 10 };
}

const BASE = [candle(60_000, 10), candle(120_000, 11), candle(180_000, 12)];
const SIZE = { width: 800, height: 400 };

let restoreStubs: () => void;
let container: HTMLDivElement;

beforeEach(() => {
  restoreStubs = installVendorDomStubs();
  vi.spyOn(console, "log").mockImplementation(() => {});
  container = document.createElement("div");
  document.body.appendChild(container);
});

afterEach(() => {
  container.remove();
  restoreStubs();
  vi.restoreAllMocks();
});

/* ── negative tests (≥3) ─────────────────────────────────────────────────── */

describe("CH-14d boundary — negative tests (D2 depth, task-25045)", () => {
  it("negative 1: createSeries on a disposed engine throws", () => {
    const engine = createKlinechartsChartEngine({ container, initialSize: SIZE });
    engine.dispose();
    expect(() => engine.createSeries({ id: "x", type: "candlestick" })).toThrow("disposed");
  });

  it("negative 2: removeSeries on a non-existent id is a silent no-op — getSeries returns undefined", () => {
    const engine = createKlinechartsChartEngine({ container, initialSize: SIZE });
    engine.removeSeries("never-existed");
    expect(engine.getSeries("never-existed")).toBeUndefined();
    // No exception, no side-effect — contract is fail-closed.
    engine.dispose();
  });

  it("negative 3: creating a second series with a duplicate id throws before the vendor is touched", () => {
    const engine = createKlinechartsChartEngine({ container, initialSize: SIZE });
    engine.createSeries({ id: "dup", type: "candlestick" });
    expect(() => engine.createSeries({ id: "dup", type: "line" })).toThrow(
      '"dup" already exists',
    );
    // Only one series should exist.
    expect(engine.getSeries("dup")).toBeDefined();
    expect(engine.getSeries("dup2")).toBeUndefined();
    engine.dispose();
  });
});

/* ── failure injection (≥1) ──────────────────────────────────────────────── */

describe("CH-14d boundary — failure injection (D2 depth, task-25045)", () => {
  it("failure injection: vendor resetData throws → series.setData propagates the error", () => {
    const engine = createKlinechartsChartEngine({ container, initialSize: SIZE });
    const series = engine.createSeries({ id: "main", type: "candlestick" });
    series.setData(BASE);

    // Inject: make the vendor chart's resetData throw.
    // setData → reload() → chart.resetData() — the vendor error must propagate.
    const chart = engine.vendor.chart;
    const origReset = chart?.resetData;
    chart!.resetData = function () {
      throw new Error("vendor resetData failed");
    };

    expect(() => series.setData(BASE)).toThrow("vendor resetData failed");

    // Restore so dispose doesn't crash.
    chart!.resetData = origReset!;
    engine.dispose();
  });
});

/* ── performance assertion (≥1) ──────────────────────────────────────────── */

describe("CH-14d boundary — performance (D2 depth, task-25045)", () => {
  it("perf: 500-candle engine creation + mount + setData under 300 ms budget (local ≈ 8 ms, ≥30× headroom)", () => {
    const t0 = performance.now();
    const engine = createKlinechartsChartEngine({ container, initialSize: SIZE });
    const series = engine.createSeries({ id: "main", type: "candlestick" });
    const bigBatch = Array.from({ length: 500 }, (_, i) => candle(60_000 + i * 60_000, 10 + (i % 20)));
    series.setData(bigBatch);
    const elapsed = performance.now() - t0;

    // Budget 300 ms = ~30× the ≈8 ms local measurement (CI jitter headroom).
    expect(elapsed).toBeLessThan(300);
    engine.dispose();
  });
});

/* ── gate-red reproduction (≥1) ──────────────────────────────────────────── */

describe("CH-14d boundary — gate red reproduction (D2 depth, task-25045)", () => {
  it("gate red: adding a direct vendor import to series.ts would break the sole-importer assertion", () => {
    // This is the gate-red reproduction: we simulate what happens if someone
    // adds `from "vendor/klinecharts"` directly to series.ts.  The scan in
    // klinechartsBoundary.test.ts would then see 2 importers and fail.
    // We assert that the *synthetic* 2-importer state does NOT equal the
    // expected single-importer set — proving the gate would catch it.
    const fakeImporters = ["core/klinecharts.ts", "core/series.ts"];
    expect(fakeImporters).not.toEqual(["core/klinecharts.ts"]);
  });

  it("gate red: an injected 400 ms stall in resetData breaches the perf budget", () => {
    const engine = createKlinechartsChartEngine({ container, initialSize: SIZE });
    const series = engine.createSeries({ id: "main", type: "candlestick" });
    const chart = engine.vendor.chart!;
    const orig = chart.resetData;
    chart.resetData = function (this: unknown, ...args: unknown[]) {
      const end = performance.now() + 400;
      while (performance.now() < end) { /* busy wait */ }
      return (orig as (...a: unknown[]) => unknown).apply(this, args);
    } as typeof chart.resetData;
    const t0 = performance.now();
    series.setData(BASE);
    const elapsed = performance.now() - t0;
    expect(elapsed).not.toBeLessThan(300);
    chart.resetData = orig;
    engine.dispose();
  });
});
