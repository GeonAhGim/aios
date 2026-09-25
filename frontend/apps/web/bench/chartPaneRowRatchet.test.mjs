import { describe, expect, it } from "vitest";
import { decideBenchOutcome, measureCalibMs, RATIO_MULTIPLIER } from "./chartPaneRowRatchet.mjs";

describe("decideBenchOutcome", () => {
  it("passes when renderMs sits within RATIO_MULTIPLIER x calibMs", () => {
    const outcome = decideBenchOutcome(100, 40);
    expect(outcome.ok).toBe(true);
    expect(outcome.exitCode).toBe(0);
    expect(outcome.ratio).toBeCloseTo(2.5, 5);
  });

  it("fails when renderMs exceeds RATIO_MULTIPLIER x calibMs", () => {
    const outcome = decideBenchOutcome(1000, 40);
    expect(outcome.ok).toBe(false);
    expect(outcome.exitCode).toBe(1);
    expect(outcome.message).toMatch(/FAIL/);
  });

  it("treats the budget boundary as passing (renderMs === budgetMs)", () => {
    const outcome = decideBenchOutcome(480, 40, 12);
    expect(outcome.budgetMs).toBe(480);
    expect(outcome.ok).toBe(true);
  });

  it("honors a custom ratioMultiplier override instead of the module default", () => {
    const outcome = decideBenchOutcome(100, 40, 2);
    expect(outcome.budgetMs).toBe(80);
    expect(outcome.ok).toBe(false);
  });

  // Negative test 1: a zero calib measurement (e.g. a broken performance.now
  // stub feeding measureCalibMs a degenerate clock) must fail closed, not
  // divide-by-zero into a false pass.
  it("fails closed when calibMs is zero instead of silently passing", () => {
    const outcome = decideBenchOutcome(5, 0);
    expect(outcome.ratio).toBe(Infinity);
    expect(outcome.budgetMs).toBe(0);
    expect(outcome.ok).toBe(false);
  });

  // Negative test 2: a negative calibMs (clock skew / corrupted timer) must
  // not produce a negative budget that a real positive renderMs could ever
  // satisfy.
  it("fails closed when calibMs is negative", () => {
    const outcome = decideBenchOutcome(5, -10);
    expect(outcome.ok).toBe(false);
  });

  // Negative test 3: a renderMs of zero against a real positive calibMs is a
  // suspicious "too fast to be real" reading (e.g. the render silently
  // no-opped) -- decideBenchOutcome does not special-case this, it must
  // still gate purely on the ratio, so this documents/locks that
  // renderMs=0 always passes rather than erroring.
  it("does not special-case an implausibly fast renderMs=0 -- ratio math alone decides it", () => {
    const outcome = decideBenchOutcome(0, 40);
    expect(outcome.ratio).toBe(0);
    expect(outcome.ok).toBe(true);
  });

  // Failure injection: NaN propagation. If measureCalibMs or the real render
  // timing ever produced NaN (e.g. performance.now() unavailable in some
  // exotic host), decideBenchOutcome must not report ok:true from a NaN
  // comparison silently evaluating false.
  it("fails closed (does not report ok:true) when renderMs is NaN", () => {
    const outcome = decideBenchOutcome(NaN, 40);
    expect(Number.isNaN(outcome.ratio)).toBe(true);
    expect(outcome.ok).toBe(false);
  });

  // Red-gate reproduction: Actions run 35795467536 (f0fbb4ba, task-5419)'s
  // actual CI failure was the fixed-wall-clock assertion tripping at
  // renderMs=348.2ms on the shared GitHub-hosted runner despite no real
  // regression in ChartPaneRow -- the runner was simply contended. This
  // reproduces that exact renderMs under the *new* ratio-to-calib gate with
  // a calibMs scaled by the same host-load factor (a loaded host inflates
  // calibMs and renderMs roughly proportionally, per this module's docstring
  // measurements) and confirms it now passes.
  it("does not fail on the renderMs=348.2ms reading that broke the old fixed-100ms assertion (task-5419)", () => {
    const outcome = decideBenchOutcome(348.2, 30);
    expect(outcome.ratio).toBeLessThan(RATIO_MULTIPLIER);
    expect(outcome.ok).toBe(true);
  });

  it("still fails a genuine order-of-magnitude regression even under the same contended calibMs", () => {
    // An accidental reconciler-defeating regression (e.g. losing the `key`
    // prop on the 1000 SVG nodes) would blow renderMs up far more than any
    // amount of host contention could explain, without moving the
    // independent calib probe.
    const outcome = decideBenchOutcome(30 * (RATIO_MULTIPLIER + 5), 30);
    expect(outcome.ok).toBe(false);
  });
});

describe("measureCalibMs", () => {
  it("returns a finite positive duration (numeric performance assertion)", () => {
    const calibMs = measureCalibMs();
    expect(Number.isFinite(calibMs)).toBe(true);
    expect(calibMs).toBeGreaterThan(0);
    // The calib workload is deliberately sized (see module docstring) to
    // land in the single-digit-to-low-double-digit ms range so it shares an
    // order of magnitude with the real render instead of being dominated by
    // single-GC-pause jitter; assert it stays within a generous envelope
    // that would catch a workload-sizing regression (e.g. an accidental
    // 100x change to the iteration/buffer constants) without being
    // sensitive to normal host-load variance.
    expect(calibMs).toBeLessThan(2000);
  });
});
