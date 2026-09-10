import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  CALIB_BASE_MS,
  CH19_ABSOLUTE_TARGET_MS,
  checkAbsoluteThresholds,
  checkRatchet,
  loadBaseline,
  writeBaseline,
} from "./densityRatchet.mjs";

describe("checkRatchet", () => {
  it("flags a real regression at calibRatio=1 (idle reference host)", () => {
    const { failures, improved } = checkRatchet({ panZoomFrameMsP95: 10 }, { panZoomFrameMsP95: 6.6 }, 1);
    expect(failures.length).toBe(1);
    expect(Object.keys(improved).length).toBe(0);
  });

  it("does not flag a contended host running the same code (task-2479)", () => {
    // Mirrors the reproduced CI failure: calibRatio ~20.7 with metrics scaled ~6-13x
    // uniformly vs baseline — a loaded host, not an actual regression.
    const current = { panZoomFrameMsP95: 39.696, indicatorAddMs: 271.289, tickUpdateMsP95: 0.172 };
    const baseline = { panZoomFrameMsP95: 6.6, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 };
    const { failures } = checkRatchet(current, baseline, 20.669);
    expect(failures).toEqual([]);
  });

  it("still catches a real regression even on a contended host", () => {
    // Same calibRatio as above, but one metric regressed far beyond what host load explains.
    const current = { panZoomFrameMsP95: 39.696, indicatorAddMs: 2000, tickUpdateMsP95: 0.172 };
    const baseline = { panZoomFrameMsP95: 6.6, indicatorAddMs: 28.51, tickUpdateMsP95: 0.013 };
    const { failures } = checkRatchet(current, baseline, 20.669);
    expect(failures.length).toBe(1);
    expect(failures[0]).toMatch(/indicatorAddMs/);
  });

  it("reports improvements normalized to the reference host scale", () => {
    const { improved } = checkRatchet({ panZoomFrameMsP95: 20 }, { panZoomFrameMsP95: 6.6 }, 10);
    expect(improved.panZoomFrameMsP95).toBeLessThan(6.6);
    expect(improved.panZoomFrameMsP95).toBe(2);
  });
});

// DEPTH_CH(task-2729) audit of task-1959 (4226858): checkRatchet had no unit
// tests at all at audit time, negative<3, and no gate-red reproduction using
// the spec's own fixed ms/fps absolute targets (CH19_ABSOLUTE_TARGET_MS) —
// only the relative ratchet was exercised. checkAbsoluteThresholds and the
// baseline file's own failure modes were entirely untested. DEEPEN task-3096
// fills those: real gate-red reproduction against the actual CH-19 spec
// constants, plus real (not mocked-network — this file has none) fs failure
// injection for the baseline load/write path.
describe("checkAbsoluteThresholds — CH-19e fixed ms/fps gate (DEEPEN task-3096)", () => {
  it("flags a metric that exceeds the spec's own absolute target at calibRatio=1 (idle reference host)", () => {
    // panZoomFrameMsP95's real spec target is 16.7ms (CH19_ABSOLUTE_TARGET_MS) — this uses
    // that exact constant, not a fabricated threshold, so the assertion is anchored to §9.11 CH-19.
    const current = { panZoomFrameMsP95: 20, indicatorAddMs: 50, tickUpdateMsP95: 5 };
    const { failures, calibRatio } = checkAbsoluteThresholds(current, CALIB_BASE_MS);
    expect(calibRatio).toBe(1);
    expect(failures.length).toBe(1);
    expect(failures[0]).toMatch(/panZoomFrameMsP95/);
    expect(failures[0]).toContain(`${CH19_ABSOLUTE_TARGET_MS.panZoomFrameMsP95}`);
  });

  it("passes when every metric sits within the spec's absolute targets", () => {
    const current = { panZoomFrameMsP95: 10, indicatorAddMs: 50, tickUpdateMsP95: 5 };
    const { failures } = checkAbsoluteThresholds(current, CALIB_BASE_MS);
    expect(failures).toEqual([]);
  });

  it("scales the target up for a contended host instead of failing on host load alone", () => {
    // calibMs 5x CALIB_BASE_MS -> calibRatio=5 -> panZoomFrameMsP95 target becomes 16.7*5=83.5ms.
    // 80ms would fail the raw (unscaled) spec target but passes once host load is accounted for.
    const current = { panZoomFrameMsP95: 80, indicatorAddMs: 50, tickUpdateMsP95: 5 };
    const { failures, normalized } = checkAbsoluteThresholds(current, CALIB_BASE_MS * 5);
    expect(normalized.panZoomFrameMsP95).toBeCloseTo(83.5, 5);
    expect(failures).toEqual([]);
  });

  it("still catches a regression beyond what host-load normalization explains (gate-red on a contended host)", () => {
    const current = { panZoomFrameMsP95: 10, indicatorAddMs: 50, tickUpdateMsP95: 100 };
    const { failures } = checkAbsoluteThresholds(current, CALIB_BASE_MS * 5);
    expect(failures.length).toBe(1);
    expect(failures[0]).toMatch(/tickUpdateMsP95/);
  });

  it("never tightens the target below spec on a fast/idle host (ratio floored at 1)", () => {
    // calibMs far below CALIB_BASE_MS (a very fast/idle host) must not lower the target below
    // the raw spec value -- CH-19 is already the floor, not something a fast host can shrink.
    const current = { panZoomFrameMsP95: 5, indicatorAddMs: CH19_ABSOLUTE_TARGET_MS.indicatorAddMs, tickUpdateMsP95: 5 };
    const { failures, normalized, calibRatio } = checkAbsoluteThresholds(current, CALIB_BASE_MS / 10);
    expect(calibRatio).toBe(1);
    expect(normalized.indicatorAddMs).toBe(CH19_ABSOLUTE_TARGET_MS.indicatorAddMs);
    expect(failures).toEqual([]);
  });
});

describe("loadBaseline/writeBaseline — real fs failure injection (DEEPEN task-3096)", () => {
  let dir;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = undefined;
  });

  it("throws on a corrupted baseline file instead of silently treating it as 'no baseline' (crashed prior run)", () => {
    // A real fs fault, not a numeric-value deviation: a prior bench run crashed mid-write and
    // left truncated/invalid JSON on disk. Swallowing this would make loadBaseline() === null,
    // which density_bench.mjs treats as "first run" and re-baselines over a real regression.
    dir = mkdtempSync(join(tmpdir(), "density-ratchet-test-"));
    const corruptPath = join(dir, "density-baseline.json");
    writeFileSync(corruptPath, "{ not valid json", "utf-8");
    expect(() => loadBaseline(corruptPath)).toThrow();
  });

  it("loadBaseline returns null only when the file is genuinely absent, not when it exists but is corrupted", () => {
    dir = mkdtempSync(join(tmpdir(), "density-ratchet-test-"));
    const missingPath = join(dir, "does-not-exist.json");
    expect(loadBaseline(missingPath)).toBeNull();
  });

  it("writeBaseline throws (fails loud) when the target directory does not exist", () => {
    // Real ENOENT from node:fs, injected via a path outside any created directory --
    // a bench run misconfigured to write outside its own package must not fail silently.
    const unwritablePath = join(tmpdir(), "density-ratchet-test-missing-dir", "density-baseline.json");
    expect(() => writeBaseline(unwritablePath, { panZoomFrameMsP95: 1 }, {})).toThrow();
  });
});
