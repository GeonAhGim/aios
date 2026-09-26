# UX_JOURNEYS.md — 사용자 여정 명세 (프런트엔드 1차 명세)

ADR-2026-09-24-A Decision 4 이행: 기능 단위로 쌓인 `frontend/apps/web/src/routes`(라우터 등록
65개 경로, `frontend/apps/web/src/router.tsx` 기준 — task note의 "30개"는 상위 폴더 수 기준 구어체
표현이며 실제 라우트 엔트리는 65개다)를 사용자 관점 흐름(J1~J6)으로 다시 묶는다. 근거는 전부
`router.tsx`, 각 라우트 컴포넌트, `frontend/packages/api-client/src/clients/*`를 직접 읽어 확인했다
(추측 없음). 화면은 고치지 않는다 — 문서만.

## 0. 페르소나

**개인 시스템 트레이더/리서처 1명.** 국내/해외 주식·코인을 다루며, 스스로 전략을 만들고
(스크립트 또는 빌더), 페이퍼로 검증한 뒤 실계좌 실행을 감독한다. 팀/고객 개념(GIPS, 멀티 계정)은
MVP-2(U-11) 이후 범위다.

## 1. 여정 단계표 (J1~J6)

범례 — 현재 상태: **있음**(실 API 연동 확인) / **유령경로**(화면은 있으나 백엔드 라우트 미구현,
`isRouteImplemented(...)===false` 단락 패턴) / **오프라인 시뮬레이션**(의도적으로 백엔드 미접촉,
로컬 고정 데이터) / **없음**(화면 자체가 없음).

### J1 — 온보딩 → 계좌/거래소 연결(데모 키) → 첫 대시보드 (MVP-1 필수)

| 단계 | 화면(라우트) | 필요한 API/상태 | 성공 조건 | 현재 상태 |
|---|---|---|---|---|
| 1 가입 | `/signup` (`SignupPage.tsx`) | `signup` mutation (auth client) | 계정 생성 후 로그인 화면 또는 온보딩으로 이동 | 있음 |
| 2 로그인 | `/login` (`LoginPage.tsx`) | `login` mutation | 세션 발급, 대시보드로 리다이렉트 | 있음 |
| 3 MFA 설정 | `/onboarding/mfa-setup` (`MfaSetupPage.tsx`) | `verifyMfa` mutation | MFA 등록 완료 | 있음 |
| 4 위험성향 평가 | `/onboarding/risk-assessment` (`RiskAssessmentPage.tsx`) | `submit` mutation | 평가 결과 저장 | 있음 |
| 5 최초 실행 체크리스트 | `/onboarding/first-run` (`OnboardingFlowPage.tsx`) | `useExchangeCredentials`, `useMyStrategies`, `usePaperDeployments` | 연결·전략·배포 3단계 진행 상태 표시 | 있음(단, "완료" 판정만 하고 화면 자체는 `/exchanges`·`/strategy-builder`·`/system/paper-deployments`를 재사용 — 온보딩 전용 UI 아님) |
| 6a 실계좌 연결 마법사 | `/onboarding/connect` (`ConnectionWizardPage.tsx`) | `beginConnection` mutation | 권한 점검 후 연결 완료 | 있음 (`FeatureFlagGate flag="onboarding_connection_wizard"`, 기본값 `true` — `lib/featureFlags.ts`) |
| 6b 데모 모드 진입 | `/onboarding/demo` (`DemoModePage.tsx`) | 없음 — `demoDataset.ts` 고정 배열 3종 | 실계좌 미접촉으로 샘플 종목 선택 | **오프라인 시뮬레이션** (의도적, 코드 주석 명시 task-2632/U-10. `flag onboarding_demo_mode` 기본 `true`) |
| 6c 데모 차트·백테스트 | `/onboarding/demo/:instrumentId` (`DemoChartPage.tsx`) | 없음 — 로컬 `runDemoBacktest` 동기 함수 | 5분 내 데모 백테스트 완주 | **오프라인 시뮬레이션** (동일) |
| 7 거래소 자격증명 관리 | `/exchanges` (`ExchangeManagementPage.tsx`) | `useExchangeCredentials`(목록), `register` mutation | 등록된 자격증명이 목록에 반영, 오류 시 개별 배너 | 있음 |
| 8 첫 대시보드 | `/dashboard` (`DashboardPage.tsx`) | `usePortfolio()`, `useExecutions()`(5s 폴링) | 포지션·현금·실행 현황 표시, 데이터 없을 때 EmptyState | 있음 — 단, 알림(alerts)은 대시보드에 직접 표시되지 않고 `/alerts`로 분리(아래 갭 G-2) |

### J2 — 종목 발견: 스크리너 → 차트·지표 → 전략/스크립트 → 즉시 백테스트 → 해석 (MVP-1 필수)

| 단계 | 화면(라우트) | 필요한 API/상태 | 성공 조건 | 현재 상태 |
|---|---|---|---|---|
| 1 스크리닝 | `/screener` (`ScreenerPage.tsx`) | 스크리너 검색 `useQuery`(`clients/screener.ts`) | 결과 행 → `/chart?instrument_id=...` 이동 확인 | 있음 (로딩/빈/오류 상태 모두 `ScreenerErrorBanner` 관용으로 구현) |
| 2 심볼/캔들 조회 | `/market/instruments`, `/market/candles` | `createMarketDataClient` 쿼리 | 조회·별칭 검색 정상 | 있음 |
| 3 차트·지표 | `/chart` (`ChartPage.tsx`) | 캔들 `useQuery` + 커버리지 `useQuery` | 지표 오버레이, 레이아웃/드로잉 오류 배너 별도 처리 | 있음 |
| 4 전략 빌더 | `/strategy-builder` (`StrategyBuilderPage.tsx`) | `useCreateStrategy`, `usePreviewStrategy`, `useCandles` | 미리보기·생성 성공 | 있음 |
| 5 스크립트 편집 | `/scripts/editor` (`ScriptEditorPage.tsx`) | `compileScript` mutation | 컴파일 성공/실패 메시지 표시 | 있음 — 단, 컴파일 후 "즉시 백테스트"로 바로 이어지는 버튼/연결이 이 파일 내에는 없음(아래 갭 G-3) |
| 6 백테스트 결과 | `/backtest/sweep-results` (`SweepResultsPage.tsx`) | 스윕 결과 `useQuery`(`clients/backtests.ts`) | 결과 표/차트 렌더, 오류 배너 | 있음 |
| 7 결과 해석 보조(리서치) | `/research` (`ResearchPage.tsx`) | 검색·소스 `useQuery` ×2(`clients/researchData.ts`) | 공시/뉴스 요약 없이 원문 링크만 표시 | 있음 |

### J3 — 페이퍼 주문: 주문 → 리스크/컴플라이언스 판정 → 체결·취소·거부 → 포지션 반영 → 알림 (MVP-1 필수)

| 단계 | 화면(라우트) | 필요한 API/상태 | 성공 조건 | 현재 상태 |
|---|---|---|---|---|
| 1 주문 생성 | `/executions` (`ExecutionControlPage.tsx`) | `useCreateExecution`, `useExecutions` | 생성 성공 시 목록 반영, 400 시 오류 배너 | 있음 |
| 2 리스크/컴플라이언스 판정 표시 | `/executions` 내 `RiskVerdictPanel.tsx`(`ExecutionControlPage.tsx`에 배선) | `useEvaluateRiskGate`(gateKind=`PRE_SUBMIT`) | 판정 결과(승인/거부 사유)가 별도 패널로 표시 | **해소(task-7504·7505 실행 카드 `last_risk_verdict` 패널 + task-7500 제출 직후 사전 평가 패널)** — `RiskVerdictPanel`이 `RiskEvaluationView`(outcome/reasonCodes/ruleVersion/evaluatedAt)를 전용 패널로 표시. `FF_J3_RISK_PANEL` 기본 OFF(아래 갭 G-4 참고). 거부는 여전히 `createExecution` 자체가 4xx일 때만 `BadRequestNotice`/`ErrorMessage`/`ForbiddenNotice` 일반 오류 배너로 별도 표면화(대체 아님, 병행) |
| 3 실행 알고/체결 상세 | `/executions/:parentId/algo`, `/executions/:parentId/tca` | `useAlgoProgress`, `useLatestTca` | 진행률·TCA 계산 결과 표시 | 있음 |
| 4 포지션 반영 | `/portfolio` (`PortfolioPage.tsx`) | `usePortfolio()`, `rebalance` mutation | 체결 후 포지션 즉시 반영 | 있음 |
| 5 컴플라이언스/위임장 상태 | `/compliance`, `/compliance/mandate`, `/mandates` | `useMandateStatus()` + pause/resume/activate 등 | 위임장 상태와 액션 가능 여부 표시 | 있음 — 단 `/compliance/mandate`와 `/mandates`가 동일 훅·동일 mutation 세트로 사실상 중복 화면(아래 갭 G-5) |
| 6 알림 수신 | `/alerts`, `/notifications` | `useMyAlerts`, `useNotificationHistory` | 체결/거부 알림이 두 화면에 반영 | 있음 |
| (참고) 팔로우/구독 | `/follow` (`FollowPage.tsx`) | `createFollowClient` | 팔로우 구독 생성·해지·성과비교 | **유령경로** — `follow.ts` 주석이 명시: `follow.subscriptions.*`가 `apiRoutes.ts`에서 `implemented=false`로 단락됨. 백엔드 라우터(`src/api/routers/follow.py`) 부재 (아래 갭 G-1) |

### J4 — 감시·복구: 워치독/킬스위치 발동 시 사용자가 보는 것과 해제 절차 (MVP-2)

전역 grep(`kill|watchdog|liquidat`, 대소문자 무시)으로 `frontend/apps/web/src` 전체를 확인한
결과, UI 텍스트로 노출된 "킬스위치"·"워치독" 명칭은 **없음**. 가장 가까운 실제 화면:

| 단계 | 화면(라우트) | 필요한 API/상태 | 성공 조건 | 현재 상태 |
|---|---|---|---|---|
| 1 안전 통제 발동 확인 | `/admin/safety-controls` (`SafetyControlsPage.tsx`) | `useSafetyControls`, `useActivateSafetyControl`, `useDeactivateSafetyControl`, `useEvaluateRecovery`, `useEvaluateRiskGate` | 활성 통제 목록·복구 판정·해제 액션 | 있음 — 명칭은 일반 "안전 통제"이며 관리자 전용 화면. 대시보드/실행 화면에 발동 시 능동 알림 없음(아래 갭 G-6) |
| 2 실행 일시정지 사유 구분 | `ExecutionCard.tsx`(`/executions`) | `ExecutionCardResponse` | 사용자 정지 vs 워치독 자동정지 구분 표시 | **없음** — 코드 주석이 명시: 응답 타입에 `pausedBy` 필드가 없어 `status`만으로는 구분 불가(§17.5.2 패턴 주석, 아래 갭 G-7) |
| 3 사후 정합성 확인 | `/admin/reconciliation` (`ReconciliationPage.tsx`) | `useReconciliationStates`, `resolve` mutation | 불일치 목록·해결 액션 | 있음(사후 대사, 실시간 발동 트리거 아님) |

### J5 — 리서치 → 리포트: 데이터 소스 상태 → 요약 → 보고서 생성·다운로드 (MVP-2)

| 단계 | 화면(라우트) | 필요한 API/상태 | 성공 조건 | 현재 상태 |
|---|---|---|---|---|
| 1 리서치 조회 | `/research` | (J2와 동일) | — | 있음 |
| 2 성과 보고서 | `/reports` (`ReportsPage.tsx`) | `useReport(periodStart, periodEnd)` | 기간별 리포트 생성 | 있음 |
| 3 성과 명세서 | `/portfolio/performance-statements` (`PerformanceStatementsPage.tsx`) | `compute`/`correct` mutation, list/detail `useQuery` | 계산·정정·조회 전체 흐름 | 있음 |
| 4 지갑/정산 | `/wallet`, `/wallet/ledger`, `/wallet/payouts` | `useWalletBalance`, 커서 페이지네이션 `useQuery`, `markPaid` mutation | 잔액·원장·정산 내역 | 있음 |

### J6 — 설정·승인·감사: 위임장/승인 흐름, 감사 추적, 개인 모드 (MVP-2)

| 단계 | 화면(라우트) | 필요한 API/상태 | 성공 조건 | 현재 상태 |
|---|---|---|---|---|
| 1 승인 요청 처리 | `/approval-requests` (`MyApprovalRequestsPage.tsx`) | `useMyApprovalRequests`, `approve`/`reject` | 승인/거부 반영 | 있음 |
| 2 승인 정책 설정 | `/settings/approval` (`ApprovalSettingsPage.tsx`) | `useApprovalSettings`, `update` | 정책 저장 | 있음 |
| 3 결정 이력 조회 | `/decisions/history` (`DecisionHistoryPage.tsx`) | 하위 `ComplianceDecisionLookupPanel`/`EventLineageLookupPanel`이 각자 조회 | 컴플라이언스 판정 + 이벤트 리니지 단일 진입점 | 있음(위임 구조, 자체 API 호출 없음 — 의도적 설계, 코드 주석 task-2706/UX-22) |
| 4 감사 로그 | `/admin/audit-log` (`AuditLogPage.tsx`) | `useAuditLog(grantId, ...)` | 조회 전/후 상태 구분 표시 | 있음 |
| 5 증거 체인 | `/admin/evidence-chain` (`EvidenceChainPage.tsx`) | `verify` mutation, `timeline` query | 검증·타임라인 표시 | 있음 |
| 6 기타 설정 | `/settings/sessions`, `/settings/members`, `/settings/connections`, `/settings/account` | 각 전용 `useQuery`/mutation | CRUD 정상 | 있음 |

## 2. 갭 목록 (리프 후보 ≥10건)

| # | 갭 | 근거 | 리프 후보 제목 | files |
|---|---|---|---|---|
| G-1 | `/follow` 전체가 유령경로 — 백엔드 `follow.subscriptions.*` 라우트 미구현 | `frontend/packages/api-client/src/clients/follow.ts` 주석·`isRouteImplemented` 단락 | `[FA] follow.subscriptions 라우터 구현 — src/api/routers/follow.py 신설` | `src/api/routers/follow.py`(신규), `src/api/router_registry.py`, `contracts/openapi/v1.json`, `frontend/packages/api-client/src/apiPaths.ts` |
| G-2 | 대시보드에 알림(alerts)이 직접 노출되지 않음 — J1 8단계 "포지션·현금·알림" 중 알림만 별도 페이지(`/alerts`)로 분리돼 있어 여정 성공 조건 미충족 | `DashboardPage.tsx`에서 `useMyAlerts`/알림 관련 호출 없음(코드 확인) | `[UX] DashboardPage에 최근 알림 위젯 추가` | `frontend/apps/web/src/routes/dashboard/DashboardPage.tsx` |
| G-3 | 스크립트 컴파일 후 "즉시 백테스트"로 이어지는 명시적 연결 없음(J2 5→6단계 단절) | `ScriptEditorPage.tsx`에 컴파일 mutation만 존재, 백테스트 트리거 부재 | `[UX] ScriptEditorPage 컴파일 성공 시 백테스트 실행 CTA 추가` | `frontend/apps/web/src/routes/scripts/ScriptEditorPage.tsx`, `frontend/apps/web/src/routes/backtest/SweepResultsPage.tsx` |
| G-4 | **해소(task-7504·7505 실행 카드 `last_risk_verdict` 패널 + task-7500 제출 직후 사전 평가 패널)** — 원 갭: 주문 리스크/컴플라이언스 "판정" 전용 UI 부재 — 거부 사유가 일반 오류 배너와 구분되지 않음(U-4 "게이트 미통과 규칙 실행 0건" DoD와 별개로, 통과/거부를 사용자가 명확히 구분해 보는 화면이 없음) | (원 근거) `ExecutionControlPage.tsx`/`ExecutionCard.tsx`에 `riskGate`/`verdict`/`reasonCode` 패턴 부재(grep 0건) — 새 `RiskVerdictPanel`이 `evaluateRiskGate(PRE_SUBMIT)`의 `RiskEvaluationView`(outcome/reasonCodes/ruleVersion/evaluatedAt)를 그대로 표시해 채움(기능 플래그 `FF_J3_RISK_PANEL` 기본 OFF) | 완료 — `[UX] 실행 카드에 리스크/컴플라이언스 판정 패널 추가(승인/거부 사유 명시)` | (원 파일) `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx`, `frontend/apps/web/src/routes/executions/components/ExecutionCard.tsx` — (신규) `frontend/apps/web/src/routes/executions/components/RiskVerdictPanel.tsx`·`.test.tsx`, `frontend/apps/web/src/lib/featureFlags.ts`, `frontend/e2e/journey-j3-paper-order-to-position.spec.ts` |
| G-5 | `/compliance/mandate`와 `/mandates`가 동일 훅(`useMandateStatus`)·동일 mutation 세트를 쓰는 사실상 중복 화면 | `MandatePage.tsx`(201줄) vs `MandatesPage.tsx`(200줄) 구조 비교 | `[UX] MandatePage/MandatesPage 통합 또는 역할 분리 결정` | `frontend/apps/web/src/routes/compliance/MandatePage.tsx`, `frontend/apps/web/src/routes/mandates/MandatesPage.tsx` |
| G-6 | 워치독/안전 통제 발동 시 관리자 페이지(`/admin/safety-controls`) 밖에서는 능동 알림이 없음 — 대시보드/실행 화면에서 사용자가 발견 못 함 | `SafetyControlsPage.tsx` 외 다른 라우트에서 `useSafetyControls`/유사 알림 호출 없음(grep 확인) | `[UX] 안전 통제 발동 시 대시보드/실행 화면에 배너 전파` | `frontend/apps/web/src/routes/dashboard/DashboardPage.tsx`, `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx` |
| G-7 | 실행 일시정지가 워치독 자동정지인지 사용자 수동정지인지 프런트에서 구분 불가 — API 응답 타입에 `pausedBy` 필드 없음 | `ExecutionCard.tsx:37-39` 주석(§17.5.2 패턴), `ExecutionCardResponse` 타입 정의 확인 필요 | `[FA] ExecutionCardResponse에 pausedBy(SAFETY_LAYER/USER) 필드 추가` | `src/api/schemas/executions.py`(또는 해당 응답 스키마), `frontend/packages/shared-types/src`(해당 타입), `frontend/apps/web/src/routes/executions/components/ExecutionCard.tsx` |
| G-8 | `/onboarding/demo*`가 오프라인 시뮬레이션임이 화면상 사용자에게 명시되지 않을 가능성 — "데모"라는 라벨은 있으나 "실계좌 미접촉" 고지 문구 존재 여부 미확인 | `DemoModePage.tsx`/`DemoChartPage.tsx` 코드 주석에는 있으나 렌더된 사용자 문구는 이번 감사에서 미확인 | `[UX] 데모 모드 화면에 "실계좌 미접촉" 고지 배너 명시` | `frontend/apps/web/src/routes/onboarding/DemoModePage.tsx`, `frontend/apps/web/src/routes/onboarding/DemoChartPage.tsx` |
| G-9 | 빈 상태·오류 상태·로딩 규약이 화면마다 개별 컴포넌트명으로 재구현됨(`ScreenerErrorBanner`, `SweepErrorBanner`, `ResearchErrorBanner` 등) — 공통 컴포넌트로 통일 여부 미정 | 각 라우트 폴더에 동일 패턴의 개별 `*ErrorBanner.tsx`/`EmptyState` 다수 존재(코드 확인) | `[UX] 공용 EmptyState/ErrorBanner 컴포넌트를 ui-web 패키지로 통합` | `frontend/packages/ui-web/src`(신규 공용 컴포넌트), 각 라우트의 개별 배너 파일 |
| G-10 | 모바일 폭 대응 여부 — U-13(PWA/반응형)이 MVP-2 대상이라 이번 코드 리딩 감사(정적 텍스트/CSS 미검토)로는 확인 불가 | 범위 밖(코드만으로 반응형 breakpoint 실제 렌더 결과 판단 불가) | `[UX] J1~J3 라우트 모바일 뷰포트(375/768) 수동 점검 및 breakpoint 정의` | `frontend/apps/web/src/routes/**`(J1~J3 대상 라우트), `frontend/packages/ui-web/src`(breakpoint 토큰) |
| G-11 | 온보딩 체크리스트(`/onboarding/first-run`)가 전용 UI 없이 기존 3개 화면 재사용만으로 "완료" 판정 — 단계 간 이동 시 온보딩 맥락(진행률)이 유지되는지 미확인 | `OnboardingFlowPage.tsx` 코드에 단계별 진행 상태 계산은 있으나, 이동 후 복귀 시 컨텍스트 유지 여부는 라우팅 구조상 확인 안 됨 | `[UX] 온보딩 진행률 위젯을 /exchanges, /strategy-builder, /system/paper-deployments에 공통 노출` | `frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx`, `frontend/apps/web/src/routes/exchanges/ExchangeManagementPage.tsx` |

## 3. U-1~U-14, E2E-1~5 매핑

| 여정 | 관련 U(MVP-2 사용자 가치 리프) | 관련 E2E(백엔드) |
|---|---|---|
| J1 온보딩·연결 | U-10(연결 마법사/데모 5분 완주) | — |
| J2 발견·백테스트 | U-1(통합 스크리너), U-3(AI 스크립트 초안), U-7(전략 템플릿/DSR·PBO) | E2E-3(시세→차트/지표), E2E-4(스크립트→즉시 백테스트) |
| J3 페이퍼 주문 | U-4(조건-행동 규칙), U-8(리스크 코치) | E2E-1(페이퍼 주문 생명주기) |
| J4 감시·복구 | (없음 — 순수 안전/운영 축, U 목록 밖) | E2E-2(워치독 LIQUIDATE) |
| J5 리서치·리포트 | U-6(국내 수급/이벤트 마커), U-9(세무 초안·월간 리포트) | E2E-5(컴플라이언스 사후 판정→보고서) |
| J6 설정·승인·감사 | U-11(멀티 계정/GIPS), U-13(PWA/푸시), U-14(마켓플레이스 배지) | — |
| (범용) | U-2(계좌 통합 대시보드), U-5(거래 저널), U-12(리플레이 훈련) | — |

## 4. 성공 지표

- **여정 완료율**: J1(가입→대시보드), J2(스크리너→백테스트 결과), J3(주문→포지션 반영) 각각을
  Playwright 시나리오 그린율로 프록시 측정(§5). 목표: closeout 시점 100%(3개 시나리오 전부 그린).
- **단계 소요 시간 p95**: 스크리너 결과 표시 p95 ≤2s(U-1, 기존 예산 재사용), 차트 5k봉 렌더 p95
  ≤200ms(CH 예산 재사용), 첫 화면 상호작용 가능 시간 p95 ≤2.5s(로컬, §2.2 기존 예산).
- **오류 0**: J1~J3 Playwright 시나리오에 실패 주입 케이스(5xx·단절) 최소 1건 포함, 무음 실패(오류
  없이 잘못된 상태로 진행) 0건.

## 5. 부록 — J1~J3 Playwright 시나리오 초안

기존 관용 재사용: `frontend/e2e/support/mockBackend.ts`(page.route로 고정 픽스처 응답, 실 서버
기동 없음), `frontend/e2e/support/fieldControl.ts`(라벨 텍스트로 입력 필드 탐색). 셀렉터는 기존
스펙(`order-submission.spec.ts` 등)과 동일하게 `getByRole`(heading/button) + `getByText` +
`fieldControl(라벨)`을 우선하고, `data-testid`는 텍스트로 특정 불가능한 경우에만 신설한다.

### J1 시나리오 초안 (`frontend/e2e/onboarding-to-dashboard.spec.ts`, 신규)

```
test("가입 → 거래소 연결 → 대시보드에 포지션이 보인다", async ({ page }) => {
  await mockBackend(page, { /* signup/login/exchangeCredentials/portfolio 고정 픽스처 */ });
  await page.goto("/signup");
  // 셀렉터: fieldControl(page, "이메일"), fieldControl(page, "비밀번호")
  await page.getByRole("button", { name: "가입" }).click();
  await expect(page).toHaveURL(/\/(login|onboarding)/);
  // 로그인 또는 자동 로그인 후:
  await page.goto("/exchanges");
  await expect(page.getByRole("heading", { name: /거래소|Exchange/ })).toBeVisible();
  // 등록 폼: fieldControl(page, "API Key") 등 RegisterCredentialForm.tsx 라벨 확인 필요
  await page.getByRole("button", { name: "등록" }).click();
  await expect(page.getByText(/등록됨|Connected/)).toBeVisible();
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: /대시보드|Dashboard/ })).toBeVisible();
  // 포지션 카드 텍스트로 확인(EmptyState가 아닌 실제 포지션 픽스처 반환 시)
});

test("[실패 주입] 거래소 자격증명 등록 5xx 시 오류 배너를 보이고 목록에 반영하지 않는다", async ({ page }) => {
  await mockBackend(page, { registerCredentialResponse: { status: 500, body: { error_code: "INTERNAL", message: "..." } } });
  // ExchangeCredentialErrors.tsx가 렌더하는 오류 텍스트로 검증
});
```
사전 확인 필요: `SignupPage.tsx`/`RegisterCredentialForm.tsx`의 정확한 라벨 텍스트(한국어 라벨명)는
구현 시 소스에서 재확인 — 이 초안은 구조와 셀렉터 전략만 제시한다.

### J2 시나리오 초안 (`frontend/e2e/discovery-to-backtest.spec.ts`, 신규)

```
test("스크리너 결과 → 차트 진입 → 백테스트 결과까지 이어진다", async ({ page }) => {
  await mockBackend(page, { /* screener/candles/backtest 고정 픽스처 */ });
  await page.goto("/screener");
  await expect(page.getByRole("heading", { name: /스크리너/ })).toBeVisible();
  await page.getByRole("button", { name: /검색|실행/ }).click();
  await page.getByText(/AAPL|005930/).first().click(); // 결과 행 → /chart?instrument_id=...
  await expect(page).toHaveURL(/\/chart\?instrument_id=/);
  await expect(page.getByRole("heading", { name: /차트/ })).toBeVisible();
  await page.goto("/backtest/sweep-results");
  await expect(page.getByText(/결과 없음/)).not.toBeVisible(); // 픽스처가 결과를 반환하는 경우
});
```
(`chart-indicator-overlay.spec.ts`, `backtest-run.spec.ts` 기존 스펙과 중복되지 않도록, 이 시나리오는
"이동 경로"만 이어 붙이는 여정 테스트로 좁힌다 — 개별 화면 상세 동작은 기존 스펙이 이미 커버.)

### J3 시나리오 초안 (`frontend/e2e/order-to-position.spec.ts`, 신규 — `order-submission.spec.ts` 확장)

```
test("주문 제출 → 체결 → 포지션 반영 → 알림까지 이어진다", async ({ page }) => {
  await mockBackend(page, { /* createExecution 성공 + portfolio 갱신 + alerts 픽스처 */ });
  await page.goto("/executions");
  await fieldControl(page, "전략 ID").fill("e2e-momentum-strategy");
  await fieldControl(page, "버전").fill("1.0.0");
  await fieldControl(page, "배분 자본(USDT)").fill("500");
  await page.getByRole("button", { name: "실행 생성" }).click();
  await expect(page.getByText("e2e-momentum-strategy")).toBeVisible();
  await page.goto("/portfolio");
  await expect(page.getByText(/포지션/)).toBeVisible(); // 픽스처 반영 확인
  await page.goto("/alerts");
  await expect(page.getByText(/체결|주문/)).toBeVisible(); // 알림 픽스처 확인
});

test("[실패 주입] 리스크 게이트 거부 시 오류 배너를 보이고 포지션에 반영되지 않는다", async ({ page }) => {
  // 기존 order-submission.spec.ts의 400 VALIDATION 패턴 재사용,
  // 갭 G-4(전용 판정 UI 부재)로 인해 "판정 패널" 대신 일반 오류 배너로 검증한다.
});
```

이 세 초안이 그린이면 MVP-1 closeout 13항(`13_user_journeys`)을 충족한다(ADR-2026-09-24-A Decision
4). 실제 라벨 텍스트·픽스처 스키마는 구현 리프에서 각 컴포넌트 소스와 `mockBackend.ts`의
`MockBackendOptions`를 재확인해 채운다.
