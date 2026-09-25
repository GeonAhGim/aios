import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { perfBudgetMs } from "../test/perfBudget";
import {
  formatDate,
  formatMoney,
  formatNumber,
  InvalidDateError,
  InvalidDecimalError,
  parseDecimalParts,
  roundDecimalParts,
} from "./formatters";

describe("formatMoney — happy path", () => {
  it("groups the integer part and keeps the given fraction digits", () => {
    expect(formatMoney("1234567.89")).toBe("1,234,567.89");
  });

  it("prefixes a known fiat currency symbol", () => {
    expect(formatMoney("50000", { currency: "KRW" })).toBe("₩50,000");
    expect(formatMoney("19.99", { currency: "USD" })).toBe("$19.99");
  });

  it("suffixes an unknown ticker (crypto) as-is", () => {
    expect(formatMoney("0.00512345", { currency: "BTC", maxFractionDigits: 8 })).toBe("0.00512345 BTC");
  });

  it("pads to minFractionDigits and rounds down to maxFractionDigits", () => {
    expect(formatMoney("10", { minFractionDigits: 2 })).toBe("10.00");
    expect(formatMoney("10.129", { maxFractionDigits: 2 })).toBe("10.13"); // half-up
  });
});

describe("formatMoney — Decimal fidelity (never a float round-trip)", () => {
  it("preserves every digit of an amount far beyond Number.MAX_SAFE_INTEGER", () => {
    // 2^53 + a tail of digits -- Number(value) would silently lose the low-order
    // digits here. The formatted output must contain every one of them, grouped.
    const huge = "90071992547409999999999999.12345678901234567890";
    const formatted = formatMoney(huge, { maxFractionDigits: 20 });
    expect(formatted.replace(/,/g, "")).toBe(huge);
  });

  it("does not render a rounded-to-zero negative as \"-0\"", () => {
    expect(formatMoney("-0.001", { maxFractionDigits: 2 })).toBe("0.00");
  });
});

describe("formatMoney — negative tests (malformed / out-of-domain input)", () => {
  it("throws InvalidDecimalError on a non-numeric string", () => {
    expect(() => formatMoney("abc")).toThrow(InvalidDecimalError);
  });

  it("throws InvalidDecimalError on an empty string", () => {
    expect(() => formatMoney("")).toThrow(InvalidDecimalError);
  });

  it("throws InvalidDecimalError on a malformed decimal (two dots)", () => {
    expect(() => formatMoney("1.2.3")).toThrow(InvalidDecimalError);
  });
});

describe("formatMoney — failure injection (API sends a raw number instead of a Decimal string)", () => {
  it("refuses to silently coerce a JS number payload", () => {
    // Simulates a backend regression where a money field serializes as a JSON
    // number (e.g. 19.1) instead of the contracted Decimal string "19.10" -- the
    // classic float-precision failure mode this module exists to prevent. A
    // `Number(value) || 0` fallback would misreport this as a real amount instead
    // of surfacing the contract violation.
    const payload: unknown = 19.1;
    expect(() => formatMoney(payload as unknown as string)).toThrow(InvalidDecimalError);
    expect(() => parseDecimalParts(payload)).toThrow(InvalidDecimalError);
  });
});

describe("roundDecimalParts", () => {
  it("carries a round-up through the integer part (99.996 -> 100.00 at 2 digits)", () => {
    const rounded = roundDecimalParts({ negative: false, intPart: "99", fracPart: "996" }, 2);
    expect(rounded).toEqual({ negative: false, intPart: "100", fracPart: "00" });
  });

  it("is a no-op when maxFrac already covers the fraction", () => {
    const parts = { negative: false, intPart: "1", fracPart: "5" };
    expect(roundDecimalParts(parts, 4)).toBe(parts);
  });
});

describe("formatNumber / formatDate", () => {
  it("formatNumber groups without a currency symbol", () => {
    expect(formatNumber("1234567")).toBe("1,234,567");
  });

  it("formatDate formats a valid ISO timestamp and rejects an invalid one", () => {
    expect(formatDate("2026-01-15T00:00:00Z", { locale: "en-US" })).toMatch(/2026/);
    expect(() => formatDate("not-a-date")).toThrow(InvalidDateError);
  });
});

describe("[perf budget] formatMoney throughput", () => {
  it("formats 10,000 amounts under budget", () => {
    const amounts = Array.from({ length: 10_000 }, (_, i) => `${i}.${(i % 100).toString().padStart(2, "0")}`);
    const started = performance.now();
    for (const amount of amounts) formatMoney(amount, { currency: "KRW" });
    const elapsed = performance.now() - started;
    expect(elapsed).toBeLessThan(perfBudgetMs(200));
  });
});

describe("[gate-red repro] formatters.ts never converts a Decimal string through Number/parseFloat", () => {
  // Same idiom as errorSurface.guard.test.ts: a regex guard over the module's own
  // source, plus a test that proves the guard actually catches a regression instead
  // of only ever seeing a green run.
  const SOURCE_PATH = join(dirname(fileURLToPath(import.meta.url)), "formatters.ts");
  const FLOAT_COERCION_PATTERN = /\b(?:Number|parseFloat|parseInt)\s*\(\s*(?:value|parts\.\w+)\b/;

  function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  }

  function findFloatCoercion(source: string): boolean {
    return FLOAT_COERCION_PATTERN.test(stripComments(source));
  }

  it("the guard pattern itself catches an injected float-coercion regression", () => {
    expect(findFloatCoercion("const n = Number(value);")).toBe(true);
    expect(findFloatCoercion("const n = parseFloat(value);")).toBe(true);
    // Number.isNaN(date.getTime()) is a legitimate, unrelated use of Number.* and
    // must not false-positive.
    expect(findFloatCoercion("if (Number.isNaN(date.getTime())) throw x;")).toBe(false);
  });

  it("formatters.ts source has zero float-coercion violations", () => {
    const source = readFileSync(SOURCE_PATH, "utf-8");
    expect(findFloatCoercion(source)).toBe(false);
  });
});
