import { describe, expect, it } from "vitest";
import { formatCreditAmount, parseWalletBalance } from "./walletBalance";

describe("parseWalletBalance", () => {
  it("available/held/pendingPayout이 모두 있고 합이 맞으면 full 모드로 3분할한다", () => {
    const result = parseWalletBalance({
      userId: "u-1",
      balance: "10000",
      available: "7000",
      held: "2000",
      pendingPayout: "1000",
    });
    expect(result).toEqual({
      mode: "full",
      userId: "u-1",
      balance: "10000",
      available: "7000",
      held: "2000",
      pendingPayout: "1000",
      hasHold: true,
      canPurchase: true,
      warnings: [],
    });
  });

  it("held가 0이면 hasHold는 false다", () => {
    const result = parseWalletBalance({
      userId: "u-1",
      balance: "7000",
      available: "7000",
      held: "0",
      pendingPayout: "0",
    });
    expect(result.mode).toBe("full");
    if (result.mode === "full") {
      expect(result.hasHold).toBe(false);
      expect(result.canPurchase).toBe(true);
    }
  });

  it("available/held/pendingPayout이 전혀 없으면 balance만으로 legacy 모드 폴백한다(구버전 서버)", () => {
    const result = parseWalletBalance({ userId: "u-1", balance: "5000" });
    expect(result).toEqual({
      mode: "legacy",
      userId: "u-1",
      balance: "5000",
      canPurchase: true,
      warnings: [],
    });
  });

  it("balance가 0이면 legacy 모드에서 canPurchase는 false다", () => {
    const result = parseWalletBalance({ userId: "u-1", balance: "0" });
    expect(result.mode).toBe("legacy");
    if (result.mode === "legacy") expect(result.canPurchase).toBe(false);
  });

  it("음수 금액 문자열은 throw하지 않고 NEGATIVE_AMOUNT 경고를 붙인다(full 모드)", () => {
    const result = parseWalletBalance({
      userId: "u-1",
      balance: "-500",
      available: "-500",
      held: "0",
      pendingPayout: "0",
    });
    expect(result.mode).toBe("full");
    if (result.mode === "full") {
      expect(result.warnings).toContain("NEGATIVE_AMOUNT");
      expect(result.canPurchase).toBe(false);
    }
  });

  it("음수 금액 문자열은 legacy 모드에서도 경고로 표기한다", () => {
    const result = parseWalletBalance({ userId: "u-1", balance: "-100" });
    expect(result.mode).toBe("legacy");
    if (result.mode === "legacy") {
      expect(result.warnings).toContain("NEGATIVE_AMOUNT");
      expect(result.canPurchase).toBe(false);
    }
  });

  it("balance 필드 누락은 invalid를 반환한다(throw 금지)", () => {
    const result = parseWalletBalance({ userId: "u-1" });
    expect(result).toEqual({ mode: "invalid", reason: expect.any(String) });
  });

  it("userId 필드 누락은 invalid를 반환한다", () => {
    const result = parseWalletBalance({ balance: "100" });
    expect(result.mode).toBe("invalid");
  });

  it("available/held/pendingPayout 중 일부만 있으면 invalid다(불완전한 부분 응답)", () => {
    const result = parseWalletBalance({ userId: "u-1", balance: "100", available: "100" });
    expect(result.mode).toBe("invalid");
  });

  it("balance != available+held+pendingPayout 불일치는 조용히 감추지 않고 SUM_MISMATCH 경고로 표기한다", () => {
    const result = parseWalletBalance({
      userId: "u-1",
      balance: "10000",
      available: "7000",
      held: "2000",
      pendingPayout: "500", // 합이 9500 != 10000
    });
    expect(result.mode).toBe("full");
    if (result.mode === "full") {
      expect(result.warnings).toContain("SUM_MISMATCH");
      // 불일치가 있어도 값 자체는 감추지 않고 그대로 반환한다.
      expect(result.available).toBe("7000");
    }
  });

  it("소수점이 있는 합계도 부동소수점 오차 없이 정확히 비교한다", () => {
    const result = parseWalletBalance({
      userId: "u-1",
      balance: "0.3",
      available: "0.1",
      held: "0.1",
      pendingPayout: "0.1",
    });
    expect(result.mode).toBe("full");
    if (result.mode === "full") expect(result.warnings).not.toContain("SUM_MISMATCH");
  });

  it("객체가 아닌 입력(null·문자열·숫자)은 invalid를 반환한다", () => {
    expect(parseWalletBalance(null).mode).toBe("invalid");
    expect(parseWalletBalance("5000").mode).toBe("invalid");
    expect(parseWalletBalance(undefined).mode).toBe("invalid");
  });

  it("금액이 숫자 타입(string이 아님)이면 invalid다 — 서버가 Decimal을 number로 보내도 반올림 없이 거부한다", () => {
    const result = parseWalletBalance({ userId: "u-1", balance: 5000 });
    expect(result.mode).toBe("invalid");
  });
});

describe("formatCreditAmount", () => {
  it("정수부를 천단위로 구분한다", () => {
    expect(formatCreditAmount("1234567")).toBe("1,234,567");
  });

  it("소수부는 반올림 없이 그대로 보존한다", () => {
    expect(formatCreditAmount("1234567.891011")).toBe("1,234,567.891011");
  });

  it("음수 부호를 보존한다", () => {
    expect(formatCreditAmount("-1000")).toBe("-1,000");
  });

  it("세 자리 미만 정수는 구분자 없이 그대로다", () => {
    expect(formatCreditAmount("500")).toBe("500");
  });

  it("형식이 잘못된 문자열은 원본 그대로 반환한다(throw 금지)", () => {
    expect(formatCreditAmount("not-a-number")).toBe("not-a-number");
  });
});
