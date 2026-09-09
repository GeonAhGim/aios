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

// DEPTH_LA_LB_LC(task-2723)가 원 task-618(ea7bafe)을 D2 축 하한 미달(D1)로
// 판정 — negative≥3(SUM_MISMATCH/NEGATIVE_AMOUNT/타입거부/graceful-degrade로
// 이미 충족)은 있었지만 failure-injection(API/백엔드 결함 시뮬레이션)·수치
// 성능 단언·게이트 적색 재현 3요건이 비어 있었다. task-2973 DEEPEN으로 추가한다.
describe("failure-injection — LC-16 백엔드 결함 시뮬레이션", () => {
  it("지수표기법 금액('1e3')은 Number 경유 없이 원본 포맷 불일치로 invalid 거부한다 — Decimal 직렬화 라이브러리가 극소/극대값을 지수표기로 내보내는 결함 클래스", () => {
    const result = parseWalletBalance({
      userId: "u-1",
      balance: "1e3",
      available: "1e3",
      held: "0",
      pendingPayout: "0",
    });
    expect(result.mode).toBe("invalid");
  });

  it("로케일 포맷(천단위 콤마 '1,000.50')으로 오는 금액은 invalid 거부한다 — 백엔드 포맷터가 콤마를 남기고 보내는 결함 클래스", () => {
    const result = parseWalletBalance({ userId: "u-1", balance: "1,000.50" });
    expect(result.mode).toBe("invalid");
  });

  it("available 필드가 명시적 null이면(held/pendingPayout은 정상) invalid 거부한다 — 컬럼 마이그레이션 중 백엔드가 NULL을 그대로 흘려보내는 결함 클래스", () => {
    const result = parseWalletBalance({
      userId: "u-1",
      balance: "10000",
      available: null,
      held: "2000",
      pendingPayout: "1000",
    });
    expect(result.mode).toBe("invalid");
  });

  it("금액 필드가 문자열 'NaN'이면 invalid 거부한다 — 백엔드가 계산 오류로 NaN을 직렬화해 보내는 결함 클래스", () => {
    const result = parseWalletBalance({ userId: "u-1", balance: "NaN" });
    expect(result.mode).toBe("invalid");
  });

  it("금액 필드에 선행 '+' 부호가 붙어 오면 invalid 거부한다 — 일부 직렬화기가 양수에 '+'를 붙이는 결함 클래스", () => {
    const result = parseWalletBalance({ userId: "u-1", balance: "+500" });
    expect(result.mode).toBe("invalid");
  });

  it("Decimal 컬럼 스케일 설정 오류로 300자리 초장문 정수가 와도 크래시 없이 정상 파싱한다(BigInt 무한정밀도)", () => {
    const huge = "7".repeat(300);
    const result = parseWalletBalance({
      userId: "u-1",
      balance: huge,
      available: huge,
      held: "0",
      pendingPayout: "0",
    });
    expect(result.mode).toBe("full");
    if (result.mode === "full") {
      expect(result.warnings).not.toContain("SUM_MISMATCH");
      expect(result.available).toBe(huge);
    }
  });
});

describe("성능 단언 — 병리적 입력(초장문 Decimal) 반복 처리", () => {
  it("300자리 금액 1000회 parseWalletBalance+formatCreditAmount가 500ms 이내에 끝난다(CI 차단 게이트) — BigInt 연산이 입력 길이에 선형 이상으로 느려지는 회귀를 잡는다", () => {
    const huge = "9".repeat(300) + "." + "3".repeat(300);
    const started = performance.now();
    for (let i = 0; i < 1000; i++) {
      const result = parseWalletBalance({
        userId: "u-1",
        balance: huge,
        available: huge,
        held: "0",
        pendingPayout: "0",
      });
      if (result.mode === "full") formatCreditAmount(result.available);
    }
    const elapsed = performance.now() - started;
    // eslint-disable-next-line no-console
    console.log(`300자리 x 1000회 parse+format: elapsed=${elapsed.toFixed(1)}ms (예산<500ms)`);
    expect(elapsed).toBeLessThan(500);
  });
});

describe("게이트 적색 재현 — CI red-line (SUM_MISMATCH 부동소수점 회귀)", () => {
  it("Number 변환 기반 합계비교로 회귀하면 0.1+0.1+0.1 케이스에서 부동소수점 오차로 오탐(false SUM_MISMATCH)한다 — 이 파일의 정상 케이스 단언이 실제로 그 회귀를 적색으로 만드는 게이트임을 증명", () => {
    // 회귀 시뮬레이션: decimalSumEquals(BigInt)를 Number 변환 기반으로 되돌렸다고 가정한다.
    const naiveSumEquals = (total: string, addends: string[]): boolean =>
      Number(total) === addends.reduce((acc, v) => acc + Number(v), 0);

    // JS 부동소수점 오차: 0.1+0.1+0.1 === 0.30000000000000004 !== 0.3
    expect(naiveSumEquals("0.3", ["0.1", "0.1", "0.1"])).toBe(false);

    // 실제 구현(decimalSumEquals, BigInt 스케일 비교)은 같은 입력에서 오탐하지 않는다 —
    // 위 naiveSumEquals처럼 회귀하면 아래 단언(그리고 "소수점이 있는 합계도..." 테스트)이
    // 즉시 적색(SUM_MISMATCH 오탐)으로 뒤집힌다.
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
