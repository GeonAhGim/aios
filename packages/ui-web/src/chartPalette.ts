// dataviz 스킬 기준 검증된 카테고리 팔레트(다크 스텝) — 이 앱의 실제
// surface(#12172a)에 대해 validate_palette.js 전체 통과 확인.
export const CATEGORICAL_PALETTE = [
  "#3987e5", // blue
  "#d95926", // orange
  "#199e70", // aqua
  "#c98500", // yellow
  "#d55181", // magenta
  "#008300", // green
  "#9085e9", // violet
  "#e66767", // red
];

export const NEUTRAL_SLOT = "#3a415c"; // "미배분 현금" 등 비-식별 슬롯

// 다이버징(0 기준 위/아래) — blue<->red 쌍, 팔레트 슬롯1/8과 동일 hex 재사용.
export const DIVERGING_UP = CATEGORICAL_PALETTE[0];
export const DIVERGING_DOWN = CATEGORICAL_PALETTE[7];

export const CATEGORICAL_SOFT_CAP = 6;
