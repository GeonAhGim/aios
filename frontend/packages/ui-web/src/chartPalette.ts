// dataviz 스킬 기준 검증된 카테고리 팔레트(다크 스텝) — 이 앱의 실제
// surface(#141210, 검은색+골드 팔레트)에 대해 validate_palette.js 전체
// 통과 확인. 골드를 0번 슬롯(가장 먼저/자주 쓰이는 카테고리)에 둬 브랜드
// 악센트와 일치시키고, 이후 슬롯은 위험색/경고색(danger/warning)과
// 헷갈리지 않는 순서로 배치했다 — red/green/orange가 서로 바로 옆에
// 오면 적록색맹 시뮬레이션에서 구분이 어려워져(protan/deutan) 사이에
// teal/violet/blue 같은 "안전한" 색을 끼워 넣는다.
export const CATEGORICAL_PALETTE = [
  "#b8860f", // gold (brand)
  "#199e70", // teal
  "#e66767", // red
  "#9085e9", // violet
  "#d95926", // orange
  "#3987e5", // blue
  "#008300", // green
  "#d55181", // magenta
];

export const NEUTRAL_SLOT = "#4a4438"; // "미배분 현금" 등 비-식별 슬롯

// 다이버징(수익/손실) — 통화·카테고리와 무관하게 금융 관행(이익=초록,
// 손실=빨강)을 그대로 따른다. success/danger 토큰과 동일 색.
export const DIVERGING_UP = "#34d399";
export const DIVERGING_DOWN = "#f87171";

export const CATEGORICAL_SOFT_CAP = 6;
