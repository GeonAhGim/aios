// UX-1 (task-2685): catalog.ko 골격. 실제 147개 파일의 문자열 추출·치환은 UX-1이
// 의존하는 UX-2(task-2686)의 범위다 -- 여기서는 프레임워크가 실제로 동작함을
// 증명할 최소 네임스페이스만 채운다. 새 화면 문구를 추가할 때는 이 카탈로그에
// 키를 먼저 만들고 컴포넌트에서는 리터럴 대신 useTranslation()의 t(key)만 쓴다
// (scripts/check_i18n_literals.mjs가 새 리터럴을 게이트에서 막는다).
export const catalogKo = {
  common: {
    confirm: "확인",
    cancel: "취소",
    retry: "다시 시도",
    save: "저장",
    loading: "불러오는 중",
  },
  errors: {
    supportCode: "지원코드: {{code}}",
    retryAfterSeconds: "{{seconds}}초 후 재시도 가능",
  },
} as const;

export type CatalogKo = typeof catalogKo;
