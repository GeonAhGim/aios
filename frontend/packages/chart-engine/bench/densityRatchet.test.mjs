import { describe, expect, it } from "vitest";
import { checkRatchet } from "./densityRatchet.mjs";

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
