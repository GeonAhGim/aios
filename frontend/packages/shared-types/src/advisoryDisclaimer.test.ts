// UX-15 "자문 오인 표현 금지 검수" — findAdvisoryLanguageViolations 자체의 단위
// 테스트. FollowPage.test.tsx는 이 함수를 실제 렌더된 화면 텍스트에 대해 돌려
// "검수"를 완성하고(advisoryDisclaimer.ts 상단 주석), 여기서는 함수 자체의
// 검출·비검출 경계를 고정한다.
import { describe, expect, it } from "vitest";
import {
  BANNED_ADVISORY_PHRASES,
  findAdvisoryLanguageViolations,
  NOT_INVESTMENT_ADVICE_DISCLAIMER_KO,
} from "./advisoryDisclaimer";

describe("findAdvisoryLanguageViolations", () => {
  it("금지 표현이 전혀 없으면 빈 배열을 반환한다", () => {
    expect(findAdvisoryLanguageViolations("팔로우 구독 목록을 불러오는 중입니다.")).toEqual([]);
  });

  it("빈 문자열도 빈 배열을 반환한다(크래시 없음)", () => {
    expect(findAdvisoryLanguageViolations("")).toEqual([]);
  });

  it.each(BANNED_ADVISORY_PHRASES)("목록의 각 금지 표현 '%s'는 그 표현이 포함된 문장에서 검출된다", (phrase) => {
    expect(findAdvisoryLanguageViolations(`안내: ${phrase}합니다.`)).toContain(phrase);
  });

  it("한 문장에 금지 표현이 여러 개면 전부 반환한다", () => {
    const text = "이 전략은 매수를 추천합니다. 수익을 보장합니다.";
    expect(findAdvisoryLanguageViolations(text)).toEqual(
      expect.arrayContaining(["매수를 추천", "수익을 보장"]),
    );
    expect(findAdvisoryLanguageViolations(text)).toHaveLength(2);
  });

  // 게이트적색 재현: 고지 문구 자신이 스스로를 위반으로 잡으면(예: 목록에 "투자자문"
  // 처럼 고지 문구에도 등장하는 단어를 넣는 회귀) 이 테스트가 즉시 적색이 된다 —
  // findAdvisoryLanguageViolations 구현이 아니라 BANNED_ADVISORY_PHRASES 목록
  // 자체의 회귀를 잡는 테스트다.
  it("게이트적색 재현: 공식 고지 문구(NOT_INVESTMENT_ADVICE_DISCLAIMER_KO) 자신은 위반 0건이다", () => {
    expect(findAdvisoryLanguageViolations(NOT_INVESTMENT_ADVICE_DISCLAIMER_KO)).toEqual([]);
  });
});
