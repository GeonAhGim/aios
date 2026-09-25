import { describe, expect, it } from "vitest";

import { COVERAGE_SCALE, coverageEnabled, perfBudgetMs, perfScale } from "./perfBudget";

describe("perfBudget — coverage-aware wall-clock budgets", () => {
  it("scale is exactly 1 on a plain run and COVERAGE_SCALE under --coverage", () => {
    expect(perfScale()).toBe(coverageEnabled() ? COVERAGE_SCALE : 1);
    expect(COVERAGE_SCALE).toBeGreaterThan(1);
    expect(COVERAGE_SCALE).toBeLessThanOrEqual(4);
  });

  it("never shrinks a budget and is monotonic in the base", () => {
    expect(perfBudgetMs(2000)).toBeGreaterThanOrEqual(2000);
    expect(perfBudgetMs(3000)).toBeGreaterThan(perfBudgetMs(2000));
    expect(perfBudgetMs(0)).toBe(0);
  });

  it("is a pure function of the run mode (same answer every call)", () => {
    expect(perfBudgetMs(1000)).toBe(perfBudgetMs(1000));
    expect(perfBudgetMs(1000)).toBe(1000 * perfScale());
  });
});
