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
  // task-2657(AI-22): AiStudioPage.tsx는 이 카탈로그가 골격 이상으로 실제 채워진
  // 첫 화면이다 — 새 화면 문구는 리터럴 대신 여기 키를 만들고 t(key)만 쓴다
  // (scripts/check_i18n_literals.mjs가 새 리터럴을 게이트에서 막는다).
  ai: {
    pageTitle: "AI 연구 스튜디오",
    sections: {
      providers: "공급자 설정",
      tokens: "에이전트 토큰",
      proposals: "제안 목록",
      experiments: "실험 비교",
    },
    providers: {
      name: {
        anthropic: "Anthropic",
        openaiCompatible: "OpenAI 호환(로컬 LLM 포함)",
        gemini: "Google Gemini",
        externalAgent: "외부 에이전트(Claude Code·Codex CLI 등)",
      },
      enabledLabel: "사용",
      dailyBudgetLabel: "일일 비용 상한(USD)",
      empty: "등록된 공급자가 없습니다.",
    },
    tokens: {
      issueTitle: "새 토큰 발급",
      scope: {
        read: "read(지표·데이터 조회)",
        research: "research(백테스트·스윕 실행)",
        propose: "propose(제안 제출)",
        paper: "paper(PAPER 실행 요청)",
      },
      instrumentsLabel: "허용 종목(쉼표로 구분, 비우면 전체)",
      notionalCapLabel: "명목가 상한",
      expiresAtLabel: "만료 시각(ISO 8601)",
      issue: "발급",
      revoke: "폐기",
      expiresPrefix: "만료",
      empty: "발급된 토큰이 없습니다.",
    },
    proposals: {
      empty: "제안이 없습니다.",
      providerPrefix: "공급자",
      promote: "PAPER 승격",
      disclaimer: "제안은 컴파일·검증·리스크 게이트를 코드로 통과해야 하며, PASS인 제안만 PAPER로 승격할 수 있습니다.",
    },
    promote: {
      confirmTitle: "PAPER 승격 확인",
      digestLabel: "확인 digest",
      expiresPrefix: "만료",
    },
    experiments: {
      compareA: "비교 실험 A",
      compareB: "비교 실험 B",
      metricColumn: "지표",
      empty: "실험이 없습니다.",
      noMetrics: "비교할 지표가 없습니다.",
    },
  },
} as const;

export type CatalogKo = typeof catalogKo;
