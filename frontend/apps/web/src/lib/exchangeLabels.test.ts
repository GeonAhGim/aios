import { describe, expect, it } from "vitest";
import { exchangeLabel } from "./exchangeLabels";

describe("exchangeLabel", () => {
  it("bitget은 'Bitget'으로 표시한다", () => {
    expect(exchangeLabel("bitget")).toBe("Bitget");
  });

  it("kis는 '한국투자증권(KIS)'으로 표시한다", () => {
    expect(exchangeLabel("kis")).toBe("한국투자증권(KIS)");
  });

  it("negative: 매핑에 없는 거래소 코드는 원문 그대로 반환한다(추측 금지)", () => {
    expect(exchangeLabel("unknown_exchange")).toBe("unknown_exchange");
  });

  it("negative: 빈 문자열도 예외 없이 그대로 반환한다", () => {
    expect(exchangeLabel("")).toBe("");
  });
});
