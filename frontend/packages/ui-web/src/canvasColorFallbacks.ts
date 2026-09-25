// UX-3: CandlestickChart.tsx는 캔버스 렌더러(lightweight-charts)라 CSS 변수를 직접
// 못 읽고 getComputedStyle로 apps/web/src/index.css의 `@theme` 다크 팔레트 값을
// 읽어온다. 이 상수는 그 조회가 빈 문자열을 돌려줄 때만 쓰이는 폴백이다(document가
// 없는 SSR/테스트 환경, 혹은 CSS가 아직 적용되기 전인 아주 짧은 순간) -- 실제
// 렌더링에서는 항상 index.css의 토큰이 우선한다. index.css 값이 바뀌면 이 상수도
// 같이 맞춰야 한다(두 파일 다 check_hardcoded_colors.mjs의 TOKEN_SOURCE_FILES에
// 등록돼 있어 리터럴 자체는 허용되지만, 값이 벌어지면 폴백 순간에만 색이 어긋난다).
export const CANVAS_COLOR_FALLBACK = {
  text: "#b5a98c", // --color-fg-secondary
  grid: "#2a2620", // --color-border
  up: "#34d399", // --color-success
  down: "#f87171", // --color-danger
} as const;
