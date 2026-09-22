import { describe, expect, it } from "vitest";
import { decideBenchOutcome, measureCalibMs, RATIO_MULTIPLIER } from "./idempotencyScanRatchet.mjs";

describe("decideBenchOutcome", () => {
  it("passes when scanMs sits within RATIO_MULTIPLIER x calibMs", () => {
    const outcome = decideBenchOutcome(100, 40);
    expect(outcome.ok).toBe(true);
    expect(outcome.exitCode).toBe(0);
    expect(outcome.ratio).toBeCloseTo(2.5, 5);
  });

  it("fails when scanMs exceeds RATIO_MULTIPLIER x calibMs", () => {
    const outcome = decideBenchOutcome(1000, 40);
    expect(outcome.ok).toBe(false);
    expect(outcome.exitCode).toBe(1);
    expect(outcome.message).toMatch(/FAIL/);
  });

  it("treats the budget boundary as passing (scanMs === budgetMs)", () => {
    const outcome = decideBenchOutcome(480, 40, 12);
    expect(outcome.budgetMs).toBe(480);
    expect(outcome.ok).toBe(true);
  });

  it("honors a custom ratioMultiplier override instead of the module default", () => {
    const outcome = decideBenchOutcome(100, 40, 2);
    expect(outcome.budgetMs).toBe(80);
    expect(outcome.ok).toBe(false);
  });

  // Negative test 1: a zero calib measurement (e.g. a broken Date.now/performance.now
  // stub feeding measureCalibMs a degenerate clock) must fail closed, not divide-by-zero
  // into a false pass.
  it("fails closed when calibMs is zero instead of silently passing", () => {
    const outcome = decideBenchOutcome(5, 0);
    expect(outcome.ratio).toBe(Infinity);
    expect(outcome.budgetMs).toBe(0);
    expect(outcome.ok).toBe(false);
  });

  // Negative test 2: a negative calibMs (clock skew / corrupted timer) must not produce
  // a negative budget that a real positive scanMs could ever satisfy.
  it("fails closed when calibMs is negative", () => {
    const outcome = decideBenchOutcome(5, -10);
    expect(outcome.ok).toBe(false);
  });

  // Negative test 3: a scanMs of zero against a real positive calibMs is a suspicious
  // "too fast to be real" reading (e.g. the scanner silently returned before doing any
  // work) -- decideBenchOutcome does not special-case this, it must still gate purely on
  // the ratio, so this documents/locks that scanMs=0 always passes rather than erroring.
  it("does not special-case an implausibly fast scanMs=0 -- ratio math alone decides it", () => {
    const outcome = decideBenchOutcome(0, 40);
    expect(outcome.ratio).toBe(0);
    expect(outcome.ok).toBe(true);
  });

  // Failure injection: NaN propagation. If measureCalibMs or the real scan ever produced
  // NaN (e.g. performance.now() unavailable in some exotic host), decideBenchOutcome must
  // not report ok:true from a NaN comparison silently evaluating false.
  it("fails closed (does not report ok:true) when scanMs is NaN", () => {
    const outcome = decideBenchOutcome(NaN, 40);
    expect(Number.isNaN(outcome.ratio)).toBe(true);
    expect(outcome.ok).toBe(false);
  });

  // Red-gate reproduction: task-4968/a22cdb09's actual CI failure was the fixed-wall-clock
  // assertion tripping at scanMs=271ms (and up to 550ms under 5-workspace contention) despite
  // no real regression -- the scanner was simply running on a loaded shared CI machine. This
  // reproduces that exact scanMs under the *new* ratio-to-calib gate with a calibMs scaled by
  // the same host-load factor (a loaded host inflates calibMs and scanMs roughly proportionally,
  // per idempotencyScanRatchet.mjs's docstring measurements) and confirms it now passes.
  it("does not fail on the scanMs=271ms reading that broke the old fixed-200ms/1000ms assertion (task-4968)", () => {
    // Observed under 10-way contention: calibMs ~62-71ms, scanMs ~209-256ms, ratio ~3-4x.
    // 271ms falls just above that measured band; pair it with a calibMs from the same regime.
    const outcome = decideBenchOutcome(271, 65);
    expect(outcome.ratio).toBeLessThan(RATIO_MULTIPLIER);
    expect(outcome.ok).toBe(true);
  });

  it("still fails a genuine order-of-magnitude regression even under the same contended calibMs", () => {
    // An accidental O(n^2) rewrite of findCallSites would blow scanMs up far more than any
    // amount of host contention could explain, without moving the independent calib probe.
    const outcome = decideBenchOutcome(65 * (RATIO_MULTIPLIER + 5), 65);
    expect(outcome.ok).toBe(false);
  });
});

describe("measureCalibMs", () => {
  it("returns a finite positive duration (numeric performance assertion)", () => {
    const calibMs = measureCalibMs();
    expect(Number.isFinite(calibMs)).toBe(true);
    expect(calibMs).toBeGreaterThan(0);
    // The calib workload is deliberately sized (see module docstring) to land in the tens-
    // of-ms range so it shares an order of magnitude with the real scan instead of being
    // dominated by single-GC-pause jitter; assert it stays within a generous envelope that
    // would catch a workload-sizing regression (e.g. an accidental 100x change to the
    // iteration/buffer constants) without being sensitive to normal host-load variance.
    expect(calibMs).toBeLessThan(2000);
  });
});
