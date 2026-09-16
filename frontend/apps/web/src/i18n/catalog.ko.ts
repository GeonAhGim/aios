import { catalogKoLegacyA } from "./catalog.ko.legacyA";
import { catalogKoLegacyB } from "./catalog.ko.legacyB";
import { catalogKoLegacyC } from "./catalog.ko.legacyC";
import { catalogKoLegacyD } from "./catalog.ko.legacyD";
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
  // task-2692(UX-8): ScreenerPage.tsx(필터 빌더·결과 표·차트/백테스트 연결) — ai.*
  // 와 동일하게 이 화면도 문구를 전부 t(key)로 조회한다.
  screener: {
    pageTitle: "스크리너",
    filterBuilder: {
      title: "필터 빌더",
      universeLabel: "유니버스",
      universePlaceholder: "예: KRX, BITGET",
      kindLabel: "종류",
      kind: {
        indicator: "지표",
        fundamental: "펀더멘털",
        research: "리서치",
        backtest_stat: "백테스트 통계",
      },
      fieldLabel: "필드",
      operatorLabel: "연산자",
      valueLabel: "값",
      asOfLabel: "기준 시각(as_of)",
      addFilter: "필터 추가",
      removeFilter: "필터 제거",
      sortFieldLabel: "정렬 필드",
      sortDirection: {
        asc: "오름차순",
        desc: "내림차순",
      },
      run: "실행",
    },
    validation: {
      universeRequired: "유니버스를 입력하세요.",
      filtersRequired: "필터를 하나 이상 추가하세요.",
      filterIncomplete: "{{position}}번째 필터의 필드·값을 채우세요.",
      filterResearchAsOfRequired: "{{position}}번째 리서치 필터는 기준 시각(as_of)이 필요합니다(누수 차단).",
    },
    results: {
      title: "결과",
      beforeRun: "필터를 구성하고 실행하세요.",
      empty: "조건에 맞는 종목이 없습니다.",
      truncated: "결과가 1,000행 상한에 도달해 잘렸습니다.",
      viewInChart: "차트/백테스트에서 보기",
      totalLabel: "{{total}}건",
    },
  },
  // task-2696(UX-12): WhatIfPanel.tsx(가상 주문 영향 미리보기)·RebalancePage.tsx
  // (목표 비중 → 리밸런싱 계획) — screener.*와 동일하게 문구를 전부 t(key)로 조회한다.
  whatif: {
    order: {
      title: "가상 주문 영향 미리보기",
      symbolLabel: "종목",
      sideLabel: "매매 구분",
      side: {
        buy: "매수",
        sell: "매도",
      },
      quantityLabel: "수량",
      run: "미리보기",
      beforeRun: "주문을 입력하고 미리보기를 실행하세요.",
    },
    orderValidation: {
      symbolRequired: "종목을 입력하세요.",
      quantityInvalid: "수량은 0보다 큰 숫자여야 합니다.",
    },
    impact: {
      exposureDeltaLabel: "노출 변화",
      concentrationDeltaLabel: "집중도 변화",
      varDeltaLabel: "VaR 변화",
      limitHeadroomDeltaLabel: "한도 여유 변화",
    },
    rebalance: {
      pageTitle: "리밸런싱",
      targetBuilderTitle: "목표 비중",
      symbolLabel: "종목",
      weightLabel: "목표 비중(%)",
      addTarget: "목표 추가",
      removeTarget: "제거",
      run: "계획 생성",
      resultsTitle: "계획",
      beforeRun: "목표 비중을 구성하고 계획을 생성하세요.",
      resultsEmpty: "생성된 거래가 없습니다.",
      turnoverLabel: "회전율 {{value}}%",
      estCostLabel: "예상 비용 {{value}}",
      skippedTitle: "건너뜀",
      previewImpact: "영향 미리보기",
    },
    targetValidation: {
      targetsRequired: "목표 비중을 하나 이상 추가하세요.",
      targetSymbolRequired: "{{position}}번째 목표의 종목을 입력하세요.",
      targetWeightInvalid: "{{position}}번째 목표의 비중은 0~100 사이 숫자여야 합니다.",
      targetSymbolDuplicate: "{{position}}번째 종목이 중복되었습니다.",
    },
  },
  // task-2702(UX-18): NotificationCenterPage.tsx(알림 센터 + 일간 다이제스트
  // 요약) — screener.*/whatif.*와 동일하게 문구를 전부 t(key)로 조회한다.
  notificationCenter: {
    title: "알림 센터",
    filterLabel: "채널",
    channel: {
      ALL: "전체",
      EMAIL: "이메일",
      PUSH: "푸시",
      IN_APP: "인앱",
    },
    empty: "표시할 알림 이력이 없습니다.",
    unknownDate: "날짜 미상",
    totalCount: "{{count}}건",
    stat: {
      total: "전체",
      sent: "발송됨",
      failed: "실패",
    },
  },
  commandPalette: {
    trigger: "명령 팔레트",
    searchTitle: "명령 검색",
    helpTitle: "단축키 도움말",
    showHelp: "단축키 보기",
    showSearch: "검색으로 돌아가기",
    placeholder: "화면 이름으로 검색...",
    empty: "일치하는 화면이 없습니다.",
    shortcut: {
      openSearch: "명령 팔레트 열기",
      openHelp: "단축키 도움말 열기",
      navigate: "목록 이동",
      select: "선택한 화면으로 이동",
      close: "닫기",
    },
  },
  // task-2704(UX-20): OnboardingFlowPage.tsx(거래소 연결 → 전략 → 페이퍼 실행) +
  // DashboardPage 빈 상태 CTA — 신규 화면이라 리터럴 0건 규칙을 그대로 따른다.
  onboarding: {
    pageTitle: "시작하기",
    emptyStateCta: "온보딩 시작하기",
    stepDone: "완료",
    stepUpcoming: "예정",
    completeTitle: "첫 실행을 완주했습니다",
    completeDescription: "거래소 연결, 전략 생성, 페이퍼 실행까지 모두 마쳤습니다.",
    goToDashboard: "대시보드로 이동",
    steps: {
      connectExchange: {
        title: "거래소 연결",
        description: "읽기 전용 API 키로 거래소 계정을 연결하세요.",
        cta: "거래소 연결하기",
      },
      createStrategy: {
        title: "전략 생성",
        description: "전략 편집기에서 조건을 구성하고 첫 전략을 만드세요.",
        cta: "전략 만들기",
      },
      runPaper: {
        title: "페이퍼 실행",
        description: "실계좌 자금 없이 모의 계좌로 전략을 실행해보세요.",
        cta: "페이퍼 실행하기",
      },
    },
  },
  nav: {
    "/dashboard": "대시보드",
    "/onboarding/first-run": "시작하기",
    "/exchanges": "거래소",
    "/market/instruments": "종목",
    "/market/candles": "캔들",
    "/chart": "차트",
    "/screener": "스크리너",
    "/backtest/sweep-results": "스윕 결과",
    "/strategy-builder": "전략편집기",
    "/scripts/editor": "스크립트편집기",
    "/marketplace": "마켓플레이스",
    "/follow": "팔로우",
    "/ai/studio": "AI 스튜디오",
    "/executions": "실행제어판",
    "/portfolio": "포트폴리오",
    "/rebalance": "리밸런싱",
    "/mandates": "위임장",
    "/compliance": "컴플라이언스",
    "/reports": "보고서",
    "/wallet": "지갑",
    "/wallet/ledger": "지갑거래내역",
    "/wallet/payouts": "출금",
    "/alerts": "알림",
    "/notifications": "알림센터",
    "/system/paper-deployments": "페이퍼배포",
    "/approval-requests": "승인대기",
    "/settings/approval": "승인설정",
    "/settings/notifications": "알림설정",
    "/settings/sessions": "세션관리",
    "/settings/members": "멤버관리",
    "/settings/account": "계정삭제",
    "/settings/connections": "계정연동",
    "/admin": "관리자",
    "/admin/system-status": "시스템상태",
    "/admin/verification-queue": "인증대기열",
    "/admin/disputes": "분쟁관리",
    "/admin/users": "사용자관리",
    "/admin/wallet-topups": "충전승인",
    "/admin/marketplace/platform-listings": "플랫폼목록",
    "/admin/approval-requests": "승인요청관리",
    "/admin/safety-controls": "안전통제",
    "/admin/reconciliation": "대사관리",
    "/admin/evidence-chain": "증빙체인검증",
    "/admin/trust": "신뢰멤버십",
  },
  // task-2686 (UX-2): mechanically extracted from the 102 baseline .tsx files
  // (scripts/i18n-literals-baseline.json) via an AST codemod -- one namespace per
  // file (camelCase basename), keys are sequential (t1, t2.../title1, label1...).
  // Rough draft: rename keys/reshape into feature namespaces during future manual
  // review, the codemod's job was safe zero-literal extraction, not naming.
  legacy: { ...catalogKoLegacyA, ...catalogKoLegacyB, ...catalogKoLegacyC, ...catalogKoLegacyD },
} as const;

export type CatalogKo = typeof catalogKo;
