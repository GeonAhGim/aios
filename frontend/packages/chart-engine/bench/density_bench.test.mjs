import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decideBenchOutcome } from "./densityRatchet.mjs";

// DEPTH_CH(task-2729) audit of task-2097 (aceeeca2): the CH-19e commit that
// introduced checkAbsoluteThresholds/measureCalibMs shipped 0 test files and
// density_bench.mjs's own gate orchestration — which of the two gates wins,
// what gets written back to density-baseline.json, whether a first run
// still fails on an absolute-threshold breach — had no negative-path or
// gate-red coverage anywhere (task-3096 covered checkRatchet/
// checkAbsoluteThresholds in isolation, not this wiring). DEEPEN task-3109
// fills that: decideBenchOutcome (extracted from density_bench.mjs's main()
// in this same leaf) is exercised here against fabricated current/baseline
// fixtures instead of the real 100k-candle bench.
describe("decideBenchOutcome — density_bench.mjs gate orchestration (DEEPEN task-3109)", () => {
  const baselineMeta = { candleCount: 100_000, indicatorInstanceCount: 30 };
  const baselinePath = "/fake/density-baseline.json";

  it("first run: creates the baseline and passes when within CH-19e absolute targets", () => {
    const current = { panZoomFrameMsP95: 6.6, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 };
    const outcome = decideBenchOutcome({
      current, baseline: null, absoluteFailures: [], calibRatio: 1, baselineMeta, baselinePath,
    });
    expect(outcome.exitCode).toBe(0);
    expect(outcome.baselineWrite).toEqual({ metrics: current, meta: baselineMeta });
    expect(outcome.logs.some((l) => l.message.includes("BASELINE created"))).toBe(true);
  });

  it("gate-red: first run still fails (and still persists the baseline) when CH-19e absolute targets are breached", () => {
    const current = { panZoomFrameMsP95: 50, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 };
    const absoluteFailures = ["panZoomFrameMsP95 50ms exceeds normalized target 16.700ms (spec target 16.7ms x calib ratio 1.000)"];
    const outcome = decideBenchOutcome({
      current, baseline: null, absoluteFailures, calibRatio: 1, baselineMeta, baselinePath,
    });
    expect(outcome.exitCode).toBe(1);
    expect(outcome.baselineWrite).toEqual({ metrics: current, meta: baselineMeta });
    expect(outcome.logs.some((l) => l.level === "error" && l.message.includes("CH-19e absolute threshold"))).toBe(true);
  });

  it("gate-red: an existing-baseline run fails on the relative ratchet and writes nothing back", () => {
    const baseline = { metrics: { panZoomFrameMsP95: 6.6, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 } };
    const current = { panZoomFrameMsP95: 6.6, indicatorAddMs: 2000, tickUpdateMsP95: 0.013 };
    const outcome = decideBenchOutcome({
      current, baseline, absoluteFailures: [], calibRatio: 1, baselineMeta, baselinePath,
    });
    expect(outcome.exitCode).toBe(1);
    expect(outcome.baselineWrite).toBeNull();
    expect(outcome.logs.some((l) => l.level === "error" && l.message.includes("regression >20%"))).toBe(true);
  });

  it("gate-red: the relative ratchet passing does not mask a CH-19e absolute-threshold failure", () => {
    const baseline = { metrics: { panZoomFrameMsP95: 6.6, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 } };
    // Within 20% of baseline (passes the ratchet) but still exceeds the absolute spec target.
    const current = { panZoomFrameMsP95: 7, indicatorAddMs: 30, tickUpdateMsP95: 0.014 };
    const absoluteFailures = ["tickUpdateMsP95 0.014ms exceeds normalized target 0.013ms (spec target 8ms x calib ratio 0.002)"];
    const outcome = decideBenchOutcome({
      current, baseline, absoluteFailures, calibRatio: 1, baselineMeta, baselinePath,
    });
    expect(outcome.exitCode).toBe(1);
    expect(outcome.baselineWrite).toBeNull();
    expect(outcome.logs.some((l) => l.level === "error" && l.message.includes("CH-19e absolute threshold"))).toBe(true);
  });

  it("an improvement merges only the improved metrics into the persisted baseline, leaving the rest untouched", () => {
    const baseline = { metrics: { panZoomFrameMsP95: 6.6, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 } };
    const current = { panZoomFrameMsP95: 3, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 };
    const outcome = decideBenchOutcome({
      current, baseline, absoluteFailures: [], calibRatio: 1, baselineMeta, baselinePath,
    });
    expect(outcome.exitCode).toBe(0);
    expect(outcome.baselineWrite).toEqual({
      metrics: { panZoomFrameMsP95: 3, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 },
      meta: baselineMeta,
    });
  });

  it("within tolerance: exits 0 and writes nothing back", () => {
    const baseline = { metrics: { panZoomFrameMsP95: 6.6, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 } };
    const current = { panZoomFrameMsP95: 6.7, indicatorAddMs: 28.6, tickUpdateMsP95: 0.014 };
    const outcome = decideBenchOutcome({
      current, baseline, absoluteFailures: [], calibRatio: 1, baselineMeta, baselinePath,
    });
    expect(outcome.exitCode).toBe(0);
    expect(outcome.baselineWrite).toBeNull();
    expect(outcome.logs.some((l) => l.message.includes("within baseline tolerance"))).toBe(true);
  });

  it("negative path: a baseline missing a metric key is not treated as a regression for that key", () => {
    // Simulates a partial/legacy density-baseline.json (e.g. hand-edited or from an older schema)
    // that never recorded tickUpdateMsP95 — checkRatchet must skip it, not crash or false-fail.
    const baseline = { metrics: { panZoomFrameMsP95: 6.6, indicatorAddMs: 28.51 } };
    const current = { panZoomFrameMsP95: 6.6, indicatorAddMs: 28.51, tickUpdateMsP95: 999 };
    const outcome = decideBenchOutcome({
      current, baseline, absoluteFailures: [], calibRatio: 1, baselineMeta, baselinePath,
    });
    expect(outcome.exitCode).toBe(0);
    expect(outcome.baselineWrite).toBeNull();
  });
});

describe("density-baseline.json — committed fixture integrity (DEEPEN task-3109)", () => {
  const HERE = dirname(fileURLToPath(import.meta.url));
  const REAL_BASELINE_PATH = join(HERE, "density-baseline.json");

  it("parses as JSON and carries the three CH-19 metrics as positive finite numbers", () => {
    const raw = readFileSync(REAL_BASELINE_PATH, "utf-8");
    const parsed = JSON.parse(raw);
    expect(parsed.candleCount).toBe(100_000);
    expect(parsed.indicatorInstanceCount).toBe(30);
    for (const key of ["panZoomFrameMsP95", "indicatorAddMs", "tickUpdateMsP95"]) {
      expect(typeof parsed.metrics[key]).toBe("number");
      expect(Number.isFinite(parsed.metrics[key])).toBe(true);
      expect(parsed.metrics[key]).toBeGreaterThan(0);
    }
  });
});
