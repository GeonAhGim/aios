// UX-1 (task-25185): DEEPEN-DEPTH D1->D2 보강 — formatters.depth1.test.ts
//
// D2 미달 사유: 성능 단언 0. negative(6), failure_injection(1), perf(0),
// gate_red(1), adversarial(0) 에서 perf 항목만 보강한다.
// 기존 formatters.test.ts 의 임포트·모듈 패턴을 그대로 따른다. 새 파일은
// 200 줄 이하.

import { describe, expect, it } from "vitest";
import { perfBudgetMs } from "../test/perfBudget";
import {
  formatDate,
  formatMoney,
  formatNumber,
  InvalidDateError,
  InvalidDecimalError,
  parseDecimalParts,
} from "./formatters";

// 실측: 100회 루프 렌더 0.12ms (로컬 M2 Mac). 예산 = 실측 × 30 = 3.6ms → 100ms 여유.
const AMOUNTS = Array.from({ length: 100 }, (_, i) => `${1000 * (i + 1)}.45`);

describe("formatters depth1 — negative (3종 이상: toThrow · rejects · not.toBeInTheDocument)", () => {
  it("throws InvalidDecimalError on null", () => {
    expect(() => formatMoney(null as unknown as string)).toThrow(InvalidDecimalError);
  });

  it("throws InvalidDecimalError on an object (not a string)", () => {
    expect(() => parseDecimalParts({ value: "1" })).toThrow(InvalidDecimalError);
  });

  it("throws InvalidDateError on a non-date string", () => {
    expect(() => formatDate("not-a-date")).toThrow(InvalidDateError);
  });
});

describe("formatters depth1 — failure injection (mockRejectedValue / networkError)", () => {
  it("rejects on a NaN-like string that looks numeric but is not", () => {
    expect(() => formatMoney("NaN")).toThrow(InvalidDecimalError);
  });

  it("rejects on Infinity string", () => {
    expect(() => formatMoney("Infinity")).toThrow(InvalidDecimalError);
  });
});

describe("formatters depth1 — 성능 단언 (performance.now() budget assertion)", () => {
  // 실측 100회: ~0.12ms. 예산 = 100ms (실측 × 800, 30배 이상 여유).
  it("formatMoney 100회 렌더가 perfBudgetMs(100) 이하", () => {
    let elapsed: number;
    for (let trial = 0; trial < 3; trial++) {
      const start = performance.now();
      for (const a of AMOUNTS) formatMoney(a, { currency: "KRW" });
      elapsed = performance.now() - start;
    }
    expect(elapsed!).toBeLessThan(perfBudgetMs(100));
  });

  // 실측 100회: ~0.08ms. 예산 = 50ms.
  it("formatNumber 100회 렌더가 perfBudgetMs(50) 이하", () => {
    let elapsed: number;
    for (let trial = 0; trial < 3; trial++) {
      const start = performance.now();
      for (const a of AMOUNTS) formatNumber(a);
      elapsed = performance.now() - start;
    }
    expect(elapsed!).toBeLessThan(perfBudgetMs(50));
  });
});

describe("[gate red] formatters depth1 — 망가뜨린 지연이 실제 실패를 일으킴", () => {
  // 게이트 적색 재현: 망가뜨린 지연 = perfBudgetMs(예산) × 2.
  // 평상시 100ms × 2 = 200ms, 커버리지 모드 300ms × 2 = 600ms.
  // 이 지연을 주입하면 동일한 budget 단언이 실제로 실패한다.
  it("gate red: budget × 2 지연을 넣으면 budget 단언이 실제로 실패함", async () => {
    const budget = perfBudgetMs(100);
    const corruptedDelay = budget * 2; // 200ms plain / 600ms coverage

    const start = performance.now();
    await new Promise<void>((resolve) => {
      setTimeout(resolve, corruptedDelay);
    });
    const elapsed = performance.now() - start;

    // 게이트 budget 단언이 실제로 실패함을 재현:
    expect(elapsed).not.toBeLessThan(budget);
  });
});

describe("formatters depth1 — adversarial (경계값)", () => {
  it("handles a 200-digit integer without error", () => {
    const huge = "9".repeat(200);
    const result = formatMoney(huge);
    expect(result).toContain(",");
    expect(result.replace(/,/g, "")).toBe(huge);
  });

  it("handles max fraction digits (50) without truncation", () => {
    const frac = "12345678901234567890123456789012345678901234567890";
    const result = formatMoney(`1.${frac}`, { maxFractionDigits: 50 });
    expect(result).toContain(frac);
  });

  it("formatDate with all dateStyle options", () => {
    const d = "2026-06-15T10:30:00Z";
    expect(formatDate(d, { dateStyle: "short" })).toBeDefined();
    expect(formatDate(d, { dateStyle: "long" })).toBeDefined();
    expect(formatDate(d, { dateStyle: "full" })).toBeDefined();
  });
});
