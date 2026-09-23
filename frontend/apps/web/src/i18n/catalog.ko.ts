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
    featureDisabled: "이 기능은 현재 비활성화되어 있습니다.",
    notFound: "찾을 수 없음",
    progress: "진행률",
  },
  errors: {
    supportCode: "지원코드: {{code}}",
    retryAfterSeconds: "{{seconds}}초 후 재시도 가능",
  },
  // task-4003(FE-OPS-10c): ExchangePositionsCard의 새 문구.
  exchangePositions: {
    title: "보유 포지션",
    selectPrompt: "거래소를 선택하면 보유 포지션을 볼 수 있습니다.",
    notFoundTitle: "이 거래소의 자격증명이 없습니다.",
    empty: "{{exchange}}에 보유 포지션이 없습니다.",
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
  // task-2718(RD-17): ResearchPage.tsx(검색·소스 상태·종목 연결 표시) —
  // screener.*와 동일하게 문구를 전부 t(key)로 조회한다.
  research: {
    pageTitle: "리서치 데이터",
    kind: {
      filing: "공시",
      news: "뉴스",
      macro: "거시",
      alt: "대안 데이터",
    },
    searchForm: {
      title: "검색",
      queryLabel: "검색어",
      instrumentLabel: "종목(instrument_id)",
      asOfLabel: "기준 시각(as_of)",
      run: "검색",
    },
    validation: {
      queryRequired: "검색어를 입력하세요.",
    },
    results: {
      title: "결과",
      beforeRun: "검색어를 입력하고 검색하세요.",
      empty: "조건에 맞는 항목이 없습니다.",
      truncated: "결과가 상한에 도달해 잘렸습니다.",
      viewInChart: "차트에서 보기",
      unmappedNoKey: "미매핑(확정 키 없음)",
      unmappedNotFound: "미매핑(종목 없음)",
      totalLabel: "{{total}}건",
    },
    sources: {
      title: "소스 상태",
      empty: "등록된 소스가 없습니다.",
      redistribution: {
        storeFull: "본문 저장",
        storeExcerpt: "발췌 저장",
        linkOnly: "링크만",
      },
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
  // task-2632(U-10): connections API(FE-OPS-5) 위에 얹는 3단계 마법사 —
  // provider/key/권한 입력 → 읽기 전용 권한 점검 → 검토·제출. 키 원문은 이
  // 네임스페이스가 아니라 컴포넌트의 maskKey()가 마스킹해 다룬다.
  connectWizard: {
    pageTitle: "연결 마법사",
    stepLabel: "{{step}}/{{total}}단계",
    steps: {
      provider: {
        title: "1. 공급자 선택",
        providerLabel: "공급자 코드",
        keyLabel: "계정 키(opaque_account_ref)",
        keyPlaceholder: "발급받은 키를 붙여넣으세요",
        keyHint: "키는 서버 KeyRing에만 저장되며 화면에는 마스킹되어 표시됩니다.",
        capabilityLegend: "요청 권한(읽기 전용만 가능)",
        next: "다음",
      },
      permissions: {
        title: "2. 권한 점검",
        readonlyNotice: "선택한 권한은 모두 읽기 전용입니다. 주문·출금 권한은 요청되지 않습니다.",
        confirmLabel: "읽기 전용 권한만 요청됨을 확인했습니다.",
        back: "이전",
        next: "다음",
      },
      review: {
        title: "3. 검토 및 연결",
        providerLabel: "공급자",
        keyLabel: "계정 키",
        capabilityLabel: "권한",
        back: "이전",
        submit: "연결하기",
        success: "연결 요청을 보냈습니다. 설정 > 연결에서 상태를 확인하세요.",
        goToConnections: "연결 목록으로 이동",
      },
    },
  },
  // task-2632(U-10): 데모 모드 — 고정 샘플 데이터셋(1년 M1 3종목)으로 실 연결
  // 없이 차트·백테스트를 체험한다. 데이터 자체는 demoDataset.ts가 결정론적으로
  // 생성하고, 여기는 화면 문구만 갖는다.
  demoMode: {
    pageTitle: "데모 모드",
    description: "고정 샘플 데이터(1년치 1분봉, 3종목)로 실제 연결 없이 차트와 백테스트를 체험합니다.",
    instrumentListLabel: "샘플 종목",
    sampleTag: "샘플",
    startCta: "데모 시작",
    chart: {
      pageTitle: "데모 차트",
      backLink: "데모 종목 목록으로",
      runBacktest: "지금 백테스트 실행",
      summary: {
        heading: "백테스트 요약",
        finalEquity: "최종 자산",
        trades: "거래 횟수",
        maxDrawdown: "최대 낙폭",
      },
      unknownInstrument: "알 수 없는 데모 종목입니다.",
    },
  },
  // task-2706 (UX-22): 결정 이력 뷰어. 새 판정/집계 로직 없음 — 기존
  // ComplianceDecisionLookupPanel(CM-18)과 PositionJournalPanel(LB-19)을 그대로
  // 재사용하고, 이 네임스페이스는 그 둘을 한 화면에 담는 페이지·진입점 문구만
  // 갖는다(AiStudioPage와 같은 관용 — legacy 코드모드 대상이 아닌 신규 화면).
  decisions: {
    pageTitle: "결정 이력 뷰어",
    eventLineage: {
      heading: "이벤트 계보 조회(position_key)",
      label: "포지션 키(position_key)",
      button: "조회",
      validationEmpty: "포지션 키를 입력하세요.",
    },
  },
  nav: {
    "/dashboard": "대시보드",
    "/onboarding/first-run": "시작하기",
    "/onboarding/connect": "연결 마법사",
    "/onboarding/demo": "데모 모드",
    "/exchanges": "거래소",
    "/market/instruments": "종목",
    "/market/candles": "캔들",
    "/chart": "차트",
    "/screener": "스크리너",
    "/research": "리서치 데이터",
    "/backtest/sweep-results": "스윕 결과",
    "/strategy-builder": "전략편집기",
    "/scripts/editor": "스크립트편집기",
    "/marketplace": "마켓플레이스",
    "/follow": "팔로우",
    "/ai/studio": "AI 스튜디오",
    "/executions": "실행제어판",
    "/portfolio": "포트폴리오",
    "/portfolio/performance-statements": "성과 명세서",
    "/rebalance": "리밸런싱",
    "/mandates": "위임장",
    "/compliance": "컴플라이언스",
    "/decisions/history": "결정 이력",
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
  // task-5597 (EMS-18 CI 정정): ExecutionAlgoPage/TcaPage 새 문구 -- i18n-literals
  // ratchet(scripts/check_i18n_literals.mjs)이 baseline에 없는 새 리터럴을 막는다.
  executionAlgoPage: {
    pageTitle: "알고리즘 집행 진행률",
    missingOrderId: "주문 ID가 없습니다.",
    notFoundDescription: "알고리즘 주문이 존재하지 않습니다.",
    unavailable: "데이터를 불러올 수 없습니다.",
    cardTitle: "집행 진행 상태",
    totalSlices: "전체 슬라이스",
    submittedSlices: "제출됨",
    pendingSlices: "대기 중",
    status: "상태",
    remainingQty: "미체결 수량",
    demotedToTwap: "TWAP로 강등됨",
    refresh: "새로고침",
  },
  tcaPage: {
    pageTitle: "거래비용분석(TCA)",
    missingOrderId: "주문 ID가 없습니다.",
    notComputedYet: "TCA 데이터가 아직 계산되지 않았습니다.",
    startCompute: "TCA 계산 시작",
    noData: "TCA 데이터가 없습니다.",
    resultTitle: "TCA 분석 결과",
    computedAt: "계산 시간: {{time}}",
    close: "닫기",
    recompute: "다시 계산",
    recomputeTitle: "TCA 재계산",
    compute: "계산",
    cancel: "취소",
  },
  performanceStatementsPage: {
    title: "실적 명세서",
    computeTitle: "명세서 계산",
    scope: "스코프",
    scopeAll: "전체",
    periodStart: "기간 시작",
    periodEnd: "기간 종료",
    methodologyVersion: "방법론 버전",
    methodologyVersionPlaceholder: "예: v1.0 (미입력 시 기본값)",
    computeSubmit: "계산 요청",
    listTitle: "명세서 목록",
    portfolioId: "포트폴리오 ID",
    portfolioIdPlaceholder: "선택 입력",
    listEmpty: "실적 명세서가 없습니다.",
    asOf: "기준 시각: {{asOf}}",
    component: "구성 요소",
    amount: "금액",
    grossPnl: "총손익",
    fees: "수수료",
    slippage: "슬리피지",
    funding: "펀딩",
    fx: "환율",
    cashflowsNet: "순현금흐름",
    estimatedTax: "추정 세금",
    netPnl: "순손익",
    returns: "수익률",
    annualized: "연환산",
    estimated: "추정",
    identityMismatch: "구성 요소 합계가 순손익과 일치하지 않습니다 (잔차: {{residual}}).",
    correctReasonLabel: "정정 사유",
    correctReasonPlaceholder: "정정 사유를 입력하세요",
    correctSubmit: "정정 요청",
    correctSuccess: "정정 요청이 반영되었습니다.",
    notFoundTitle: "명세서를 찾을 수 없습니다",
    notFoundDescription: "해당 실적 명세서가 존재하지 않습니다.",
  },
  // task-2686 (UX-2): mechanically extracted from the 102 baseline .tsx files
  // (scripts/i18n-literals-baseline.json) via an AST codemod -- one namespace per
  // file (camelCase basename), keys are sequential (t1, t2.../title1, label1...).
  // Rough draft: rename keys/reshape into feature namespaces during future manual
  // review, the codemod's job was safe zero-literal extraction, not naming.
  legacy: { ...catalogKoLegacyA, ...catalogKoLegacyB, ...catalogKoLegacyC, ...catalogKoLegacyD },
} as const;

export type CatalogKo = typeof catalogKo;
