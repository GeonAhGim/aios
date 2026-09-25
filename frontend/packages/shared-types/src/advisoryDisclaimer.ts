// spec L4_product_experience_and_discovery_v1.0.md UX-15 "자문 오인 표현 금지 검수"의
// 단일 출처. U-9(세무 리포트, spec 138행)도 이 문구·목록을 그대로 재사용한다("계산
// 초안에 '세무 자문 아님' 고지 문구 포함 — UX-15 자문 오인 방지 원칙과 동형 검수").
// 팔로우는 신호를 그대로 복제할 뿐 투자자문·일임이 아니며(spec §2.4 UX-A3, §10
// "팔로우는 PAPER 전용이며 LIVE 확장은 투자자문·일임 규제 검토가 선행돼야 한다"),
// 그 사실을 화면 문구로도 못박아야 한다.

export const NOT_INVESTMENT_ADVICE_DISCLAIMER_KO =
  "이 화면은 참고용 정보만 제공하며 투자자문·일임이 아닙니다. 팔로우는 원본 신호를 그대로 복제할 뿐 특정 종목·매매를 권유하지 않으며, 투자 결정과 그 결과에 대한 책임은 본인에게 있습니다.";

// 자문·일임으로 오인될 수 있는 단정·보장형 표현. "부분 문자열 포함" 여부만 본다 —
// 새 화면이 이 목록에 없는 변형을 쓰면 놓칠 수 있으므로, 목록은 발견될 때마다
// 넓혀 나간다(advisoryDisclaimer.test.ts가 그 확장을 강제하지는 않지만, 각 항목이
// 실제로 검출되는지는 강제한다). NOT_INVESTMENT_ADVICE_DISCLAIMER_KO 자신은 이
// 목록 중 어느 것과도 부분 문자열로 겹치지 않는다(advisoryDisclaimer.test.ts가
// 고정한다) — 그렇지 않으면 고지 문구 자체가 스스로를 위반으로 잡는 자기모순이 된다.
export const BANNED_ADVISORY_PHRASES: readonly string[] = [
  "투자를 추천",
  "매수를 추천",
  "매도를 추천",
  "지금 사세요",
  "지금 파세요",
  "수익을 보장",
  "원금을 보장",
  "손실 없이",
  "확실한 수익",
  "무조건 수익",
  "무조건 오릅니다",
  "무조건 오른다",
  "일임 운용",
];

/** text에 포함된 금지 표현을 전부 반환한다(없으면 빈 배열). */
export function findAdvisoryLanguageViolations(text: string): string[] {
  return BANNED_ADVISORY_PHRASES.filter((phrase) => text.includes(phrase));
}
