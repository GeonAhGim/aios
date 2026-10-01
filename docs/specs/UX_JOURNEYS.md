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

#### J1 closeout 적색 재조사 (task-10612, 2026-10-01)

MVP-1 closeout의 `13_user_journeys`가 J1을 적색으로 보고했으나, `frontend/e2e/
journey-j1-onboarding-to-dashboard.spec.ts`를 직접 실행한 결과 코드·테스트 결함은 없다 —
단독 실행 7 passed(23.8s), J1~J2~J3 묶음 실행(`npx playwright test journey-j1 journey-j2
journey-j3 --project=chromium`) 28 passed(25.5s), 둘 다 그린. 원인: `scripts/closeout/
ops.py::check_13_user_journeys`가 자체적으로 Playwright를 실행하지 않고 `--ci-report`로
받은 `pm/ci/latest.json`의 `steps.journeys.ok`만 신뢰하는데(ADR-D "모른다=통과 아님"
원칙), 이번에 참조된 리포트(sha `ef3b15039`, 2026-10-01T07:40:32+00:00)는 `steps`에
`journeys` 키 자체가 없다 — `pm/local_ci.py`가 journeys 단계를 "full" 모드에서만 기록하므로
(task-7145), 이번 실행이 "gate" 모드였던 것으로 보인다. 즉 J1 실패는 프런트엔드 코드나
여정 테스트의 결함이 아니라 CI 오케스트레이션이 full 모드를 안 돌려 리포트에 `journeys`
단계가 비어 있던 것이며, `docs/CI_SYSTEMIC_LOG.md` #13/#30/#51/#55/#85와 동일 계열의
거짓양성(stale/누락 ci_report)이다. `pm/local_ci.py`와 orchestrator는 `C:\aios\pm` 아래에
있어 CLAUDE.md §4가 금지하는 편집 대상이므로 이 리프 범위 밖이다 — "pm/local_ci.py가
journeys 단계를 생략하는 실행 조건(gate vs full)을 점검하거나 closeout 호출 시 항상 full
모드 리포트를 요구하도록" 만드는 별도 리프(PM/오케스트레이터 영역)가 필요하다고 보고한다.

#### J1 사용감 소견 (task-10612, 2026-10-01 — "통과/실패"가 아니라 "쓰기 좋은가" 기준)

여정 자체는 통과하지만(위 재조사 참고), 코드를 직접 읽고 단계를 따라가며 쓰기 불편한
지점을 기록한다. 이 리프에서 개선 구현은 하지 않는다 — 소견만.

| # | 지점 | 증상 | 개선안 |
|---|---|---|---|
| F-1 | 가입(`SignupPage.tsx`) → MFA 설정(`MfaSetupPage.tsx`) | 가입 직후 바로 인증 앱(Google Authenticator 등) QR 스캔을 요구하는 필수 게이트(FD-11.2)인데, 가입 화면에는 "이 다음 인증 앱이 필요하다"는 사전 안내가 없다 — 처음 쓰는 사용자가 인증 앱을 설치하지 않은 채로 가입하면 그 자리에서 막힌다(기준 6) | `SignupPage.tsx`의 가입 버튼 위/아래에 "다음 단계에서 Google Authenticator 등 인증 앱이 필요합니다" 같은 사전 고지 추가 — **해소(task-10642)**: `SignupPage.tsx`에 `legacy.signupPage.mfaPrenotice` 고지문 추가 |
| F-2 | 가입 → MFA → 위험성향평가 전체 흐름 | 각 단계가 전진 전용이다 — `AuthLayout`/`MfaSetupPage.tsx`/`RiskAssessmentPage.tsx` 어디에도 이전 단계로 돌아가는 링크가 없어, 가입 시 이메일을 잘못 입력했음을 MFA 화면에서 깨달아도 되돌릴 수 없다(기준 3, 되돌리기 불가) | 각 온보딩 단계 상단에 이전 단계로 돌아가는 링크(또는 "처음부터 다시" 버튼) 추가 — **해소(task-10642)**: `AuthLayout`에 `backTo`/`backLabel` prop 추가, `MfaSetupPage.tsx`→`/signup`, `RiskAssessmentPage.tsx`→`/onboarding/mfa-setup` 링크 노출(가입 화면은 기존 "로그인" 링크로 이미 이탈 가능해 변경 없음) |
| F-3 | 위험성향평가(`RiskAssessmentPage.tsx`) | 5개 필드(경력·투자가능비중·손실감내·투자목표·유동성필요)를 다 채운 뒤 제출 시점에 세션이 만료(401 `AUTH_TOKEN_EXPIRED`)되면 "세션이 만료되었습니다. 다시 로그인해주세요." 메시지는 뜨지만(`apiError.ts` EXACT_MESSAGES), 재로그인 후 입력값이 전부 사라져 처음부터 다시 작성해야 한다(기준 3·5, 돈이 걸리진 않지만 되돌릴 수 없는 입력 손실) | 제출 직전 폼 상태를 세션스토리지에 임시 저장하거나, 재로그인 리다이렉트 후 동일 화면으로 복귀하며 값 복원 — **해소(task-10642)**: `RiskAssessmentPage.tsx`가 입력 변경마다 평가 응답(비밀값 아님)을 `sessionStorage`(`aios.riskAssessment.draft.v1`)에 저장하고 마운트 시 복원, 제출 성공 시 즉시 삭제 |
| F-4 | 온보딩 체크리스트(`OnboardingFlowPage.tsx`, `/onboarding/first-run`) | "거래소 연결" CTA를 누르면 `/exchanges`로 이동하는데, 그 화면에는 "온보딩 3단계 중 1단계"라는 맥락이 전혀 남지 않는다 — 완료 후 전체 진행률을 보려면 사용자가 직접 `/onboarding/first-run`으로 되돌아가야 한다(기준 1·3, 불필요한 수동 이동 + 다음 행동 불명확) | `/exchanges`·`/strategy-builder`·`/system/paper-deployments`에 공통 온보딩 진행률 위젯(예: "2/3단계") 노출(기존 갭 G-11과 동일 근본 원인) — **부분 해소(task-10642)**: 공용 `OnboardingProgressWidget.tsx`(`deriveOnboardingProgress` 재사용)를 만들어 `/exchanges`(`ExchangeManagementPage.tsx`)에 노출. 이 리프의 `files` 범위가 `ExchangeManagementPage.tsx`까지만 포함해 `/strategy-builder`·`/system/paper-deployments` 배선은 G-11 잔여 범위로 남김 |
| F-5 | 거래소 자격증명 등록(`ExchangeManagementPage.tsx`) | 등록이 실패하면(`submitRegistration` catch 블록) API Secret/Passphrase 입력값을 보안상 지운다(의도적) — 그런데 실패 사유가 형식 오류처럼 단순 재시도로 고쳐지는 경우에도 매번 Secret/Passphrase를 처음부터 다시 타이핑해야 한다(기준 3, 반복 마찰) | 오류 배너에 "Secret/Passphrase를 다시 입력해주세요"라고 명시해 왜 비워졌는지 설명(보안상 지우는 동작 자체는 유지) — **해소(task-10642)**: `RegisterCredentialForm.tsx`에 `secretsCleared` prop 추가, 실패 직후 `legacy.registerCredentialForm.secretsClearedNotice` 안내 노출(지우는 동작 자체는 유지) |
| F-6 | 첫 대시보드(`DashboardPage.tsx:50-71`) | 포트폴리오 조회가 5xx로 실패하면 `portfolio`가 falsy가 되어 "총 포트폴리오 가치" 카드 섹션이 통째로 사라지고 오류 메시지도, 로딩도, 빈 상태 안내도 없다 — 데이터가 "원래 없는 것"과 "조회에 실패한 것"을 사용자가 구분할 수 없다(기준 4, 무음 실패) | 다른 카드(알림)처럼 `isError`를 받아 `ErrorMessage`로 표면화 — **해소(task-10642)**: `usePortfolio()`의 `isError`/`error`를 받아 알림 카드와 동일하게 `ErrorMessage`로 표면화 |

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

#### J4 교차 확인 (task-10705, 2026-10-01)

`docs/audits/AUDIT-6-gap-sweep-2026-10.md`(§5 G5-2/G5-3)가 1단계 "안전 통제 발동"의 백엔드
근거를 보강했다: 킬스위치 활성화(`risk_gate.py:114`)·비활성화(`:147`)에 멱등키가 없어 네트워크
재시도 시 중복 control row가 생길 수 있고(P1), 비활성화가 "이미 INACTIVE" 상태를 명시적으로
재검증하는지 코드상 확인되지 않는다(P1). 두 건 모두 화면(이 문서) 쪽 수정이 아니라 백엔드
리프(감사 §9 권장 4, backend 풀)로 남는다.

### J5 — 리서치 → 리포트: 데이터 소스 상태 → 요약 → 보고서 생성·다운로드 (MVP-2)

| 단계 | 화면(라우트) | 필요한 API/상태 | 성공 조건 | 현재 상태 |
|---|---|---|---|---|
| 1 리서치 조회 | `/research` | (J2와 동일) | — | 있음 |
| 2 성과 보고서 | `/reports` (`ReportsPage.tsx`) | `useReport(periodStart, periodEnd)` | 기간별 리포트 생성 | 있음 |
| 3 성과 명세서 | `/portfolio/performance-statements` (`PerformanceStatementsPage.tsx`) | `compute`/`correct` mutation, list/detail `useQuery` | 계산·정정·조회 전체 흐름 | 있음 |
| 4 지갑/정산 | `/wallet`, `/wallet/ledger`, `/wallet/payouts` | `useWalletBalance`, 커서 페이지네이션 `useQuery`, `markPaid` mutation | 잔액·원장·정산 내역 | 있음 |

#### J5 부록 — "보장 아님" 고지(ADR-2026-10-01-A D4, task-10801)

ADR-2026-10-01-A D4: 수익률·샤프·MDD 등 성과 지표는 보장값이 아니라 측정·공시값으로
노출해야 한다. 아래 표는 리프 착수 시점(2026-10-01) 실제 렌더 결과를 확인한 화면별
고지 유무이며, 없던 화면에는 이 리프에서 `common.performanceNotGuaranteed`
(한/영 i18n 키, `catalog.ko.ts`/`catalog.en.ts` `common` 네임스페이스)를 추가했다.
백테스트 결과 화면에는 "백테스트는 실거래 체결·슬리피지와 다를 수 있음"
(`common.backtestDivergence`)도 함께 추가했다.

| 화면(라우트) | 성과 지표 | 착수 전 고지 유무 | 조치 |
|---|---|---|---|
| `/portfolio` (`PortfolioPage.tsx`) | 총평가액, 배분 비중, 종목별 손익(totalPnl) | 없음 | `common.performanceNotGuaranteed` 추가 |
| `/reports` (`ReportsPage.tsx`) | 수익률(totalReturn), 승률, MDD(maxDrawdown) | 없음 | `common.performanceNotGuaranteed` 추가 |
| `/portfolio/performance-statements` (`PerformanceStatementsPage.tsx`) | 명세서 수익률(returns), 구성요소 손익 | 없음 | `common.performanceNotGuaranteed` 추가 |
| `/backtest/sweep-results` (`SweepResultsPage.tsx`) | 스윕 메트릭(final_equity 등), 안정성 점수 | 없음 | `common.performanceNotGuaranteed` + `common.backtestDivergence` 추가 |

검사: 4개 화면 각각의 컴포넌트 테스트(`*.test.tsx`)에서 고지 문자열이 렌더되는지
확인하고, `e2e/journey-j2-discover-to-backtest.spec.ts`의 스윕 결과 단계에서 두 고지
문자열의 가시성을 e2e로도 검사한다.

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

## 6. 확장 여정(제안) — ADR-2026-10-01-A "편리함" 축 보강

task-10599(사용자 관점 1등급 지표 ADR 초안)의 "편리함" 축 입력 자료. 이 절은 **문서만** — 화면·
코드·테스트는 바꾸지 않았다. 근거는 §1~§5와 동일하게 `router.tsx`·각 라우트 컴포넌트·
`frontend/packages/api-client/src/clients/*`를 직접 읽어 확인했다(추측 없음, 모르면 "미확인").

### 6.1 J1~J3가 덮는 과업 / 안 덮는 과업 (재확인)

| 사용자 핵심 과업 | J1~J3가 덮는가 | 근거 |
|---|---|---|
| 가입→로그인→MFA→위험성향→거래소 연결(실계좌/데모)→첫 대시보드 | 덮음(J1) | §1 J1 표 |
| 스크리닝→차트/지표→전략 빌더 또는 스크립트→백테스트→리서치 해석 | 덮음(J2), 단 스크립트 컴파일→백테스트 전환은 수동 재진입(G-3) | §1 J2 표, `ScriptEditorPage.tsx` |
| 주문 제출→리스크/컴플라이언스 판정→체결/취소/거부→포지션 반영→알림 | 덮음(J3) | §1 J3 표 |
| 전략 선택·백테스트 확인 → **모의 운용 시작**(페이퍼 배포 요청) | **부분** — `StrategyBuilderPage.tsx`/`SweepResultsPage.tsx`에 `/system/paper-deployments`로의 `navigate`/`Link` 없음(grep 0건). 배포 요청은 `PaperDeploymentsPage.tsx`의 `useRequestPaperDeployment`로 가능하나 전략 생성·백테스트 결과 화면에서 이어지는 CTA가 없어 사용자가 URL을 스스로 찾아가야 함 | `StrategyBuilderPage.tsx`, `PaperDeploymentsPage.tsx:1-12` |
| **모의 운용 → 실운용(LIVE) 전환** | **거의 없음** — 전용 전환 화면이 없고, `ExecutionControlPage.tsx:99,178-180`의 `mode` 드롭다운(`PAPER`/`LIVE`)으로 매 주문마다 선택할 뿐이다. 실계좌 전환임을 알리는 별도 확인 단계·요약(그동안의 페이퍼 성과, 전환 시점부터 실제 자금 사용 고지)은 코드에서 확인되지 않음 | `ExecutionControlPage.tsx:99,178-180` |
| 운용 중 상태 확인·알림(실시간) | **부분** — `DashboardPage.tsx`는 포지션·실행만 폴링하고 알림 위젯이 없음(G-2 재확인, grep 0건). 알림은 `/alerts`·`/notifications`로 분리 | `DashboardPage.tsx`, `AlertsPage.tsx`, `NotificationCenterPage.tsx` |
| **손실·이상 발생 시 즉시 중단** | **해소(task-10636)** — U-3 확정: `ExecutionCard.tsx`에 상태별 개별 중단 액션이 이미 있다(RUNNING→일시정지 `pause.mutate`, RETIRED 전까지 상시 `retire.mutate`). 전역 긴급 정지 화면(`/admin/safety-controls`, `SafetyControlsPage.tsx`)은 여전히 `AdminRoute.tsx:45` `!me?.isPlatformAdmin` → `/dashboard` 리다이렉트로 막혀 있으나, `DashboardPage.tsx`에 "내 운용 전부 정지" 패널을 신설해 `useExecutions()`가 돌려주는(= 본인 소유로 서버가 이미 스코프한) RUNNING 실행 전체에 `usePauseExecution`을 반복 호출하는 방식으로 비관리자도 1클릭에 전역 정지에 도달한다. 백엔드/권한 모델 변경 없이 기존 pause API 재사용 | `AdminRoute.tsx:11,45`, `ExecutionCard.tsx:134-153`, `DashboardPage.tsx`(긴급 정지 패널) |
| 성과 확인(수익률/MDD/샤프 등) | 덮음(J5, `/portfolio`·`/reports`·`/portfolio/performance-statements`) — J1~J3 밖이지만 기존 J5가 충족 | §1 J5 표 |
| 설정 변경·되돌리기 | 덮음(J6, `/settings/*`) — 단 `AccountDeletionPage.tsx`의 탈퇴 액션이 비밀번호 재입력 1단계 확인만 쓰는지, 별도 "정말 삭제" 확인 모달이 있는지는 렌더 결과 미확인(아래 §6.4 U-1) | `AccountDeletionPage.tsx:146-179` |

결론: "첫 사용"·"발견·백테스트"·"페이퍼 주문" 세 과업은 J1~J3로 덮인다. "모의 운용 시작으로의
전환"·"모의→실운용 전환"·"개인 트레이더의 즉시 중단"은 화면 조각은 있으나 **여정으로 이어지지
않거나 페르소나가 접근 불가능**해 별도 여정으로 분리해 추적할 필요가 있다(§6.2).

### 6.2 추가 여정 후보 (J7~J9)

범례는 §1과 동일. 기존 J1~J6과 겹치는 과업(성과 확인, 설정)은 새 여정으로 만들지 않고 §6.1처럼
기존 여정 표에 흡수했다(저장소 실사로 가감한 결과).

#### J7 — 전략 선택·백테스트 확인 → 모의 운용 시작 (MVP-1 보강 후보, J2의 연장)

| 단계 | 화면(라우트) | 필요한 API/상태 | 현재 상태 |
|---|---|---|---|
| 1 전략 생성/미리보기 | `/strategy-builder` | `useCreateStrategy`, `usePreviewStrategy` | 있음(J2 4단계와 동일) |
| 2 백테스트 결과 확인 | `/backtest/sweep-results` | 스윕 결과 `useQuery` | 있음(J2 6단계와 동일) |
| 3 모의 운용 배포 요청 | `/system/paper-deployments` | `useRequestPaperDeployment`, `useStartPaperDeployment` | 있음 — **단 1→2→3 사이에 화면 이동을 이어주는 CTA/링크가 없음**(G-3·G-11과 동일 패턴) |

#### J8 — 모의 운용 → 실운용(LIVE) 전환 (신규, 현재 상태 "없음"에 가까움)

| 단계 | 화면(라우트) | 필요한 API/상태 | 현재 상태 |
|---|---|---|---|
| 1 페이퍼 성과 확인 | `/portfolio/performance-statements`, `/system/paper-deployments` | 기존 훅 | 있음(화면은 있으나 전환 흐름에 배선되지 않음) |
| 2 실운용 전환 결정·고지 | (없음) | (없음) | **없음** — 전용 화면/모달 미확인 |
| 3 실운용 모드로 주문 | `/executions`(`mode="LIVE"` 드롭다운) | `useCreateExecution` | 있음 — 단 일반 주문 폼과 동일 UI, 실자금 사용 고지·2단계 확인 없음 |

#### J8 교차 확인 (task-10705, 2026-10-01)

`docs/audits/AUDIT-6-gap-sweep-2026-10.md`(§0 P0-3, §5 G5-1/G5-5)가 이 여정의 백엔드 근거를
구체화했다: Execution 생성·LIVE 전환(`execution_service.py:86-174`)이 감사 로그를 남기지 않고
(**P0**, `docs/design/INVARIANTS.md` 2026-10-01 추가 항목 참조), LIVE 실행 시작에 세션
재인증(step-up)도 요구하지 않는다(G5-5, P2, 세션 탈취 전제). §6.5 1번 항목이 요구한 "INVARIANTS.md
교차 확인"은 위 INVARIANTS.md 갱신으로 반영했다(새 I-xx 미채번 — ADR 선행 필요). 화면 쪽 2단계
확인 UI 추가(이 섹션 2행)는 여전히 별도 프런트엔드 리프이고, 백엔드 감사로그 추가는 감사 §9
권장 3(backend 풀)으로 남아있다 — 이 문서 갱신 자체는 코드를 바꾸지 않는다.

#### J9 — 손실·이상 시 즉시 중단 (개인 트레이더 관점, 신규)

| 단계 | 화면(라우트) | 필요한 API/상태 | 현재 상태 |
|---|---|---|---|
| 1 상태 인지 | `/dashboard`, `/alerts` | `usePortfolio`, `useExecutions`, `useMyAlerts` | 있음(수동 새로고침/폴링 기반, 능동 푸시 아님 — G-6과 동일 축) |
| 2 배포 단위 정지 | `/system/paper-deployments` | `usePausePaperDeployment`, `useStopPaperDeployment` | 있음 |
| 3 개별 실행 취소 | `/executions` | `ExecutionCard.tsx`의 pause(RUNNING일 때)/retire(RETIRED 전까지 상시) 버튼 | **확인됨(U-3 해소)** — `ExecutionCard.tsx:134-153` |
| 4 전역 긴급 정지("전부 멈춰라") | `/dashboard`(비관리자용 신설 패널) + `/admin/safety-controls`(관리자 전용, 플랫폼 범위) | `usePauseExecution`(본인 소유 RUNNING 실행 일괄) | **해소(task-10636)** — 비관리자는 `/dashboard`의 "내 운용 전부 정지" 패널로 1클릭 도달, 관리자는 기존 `/admin/safety-controls`로 플랫폼 범위 정지 유지 |

### 6.3 여정별 사용감 합격 기준 (기능 동작이 아니라 "쓰기 좋은가")

판정 방법 열의 "기계"는 Playwright 등으로 자동 판정 가능, "사람"은 스크린샷/수동 리뷰가 필요함을
뜻한다(§2.2 §4 기존 성능 예산과 중복되는 항목은 재사용 표시).

| 여정 | 사용감 기준 | 판정 방법 |
|---|---|---|
| J1 온보딩 | 가입→첫 대시보드 도달까지 **단계 수 ≤ 8**(현재 표의 1~8단계 그대로, 추가 단계 생기면 회귀), 각 화면 전환 체감 지연(로딩 스피너 노출)이 2.5s(§4 기존 예산)를 넘으면 진행률 안내가 보여야 함 | 기계(단계 수·URL 전이는 Playwright로 셀 수 있음) + 사람(로딩 중 안내 문구의 이해 용이성은 사람 눈) |
| J2 발견·백테스트 | 스크리너→차트→전략/스크립트→백테스트까지 **막힘 지점 0**(다음 행동이 불명확한 화면 없음) — 특히 G-3(스크립트 컴파일 후 백테스트로 가는 CTA 부재)이 남아있는 한 이 기준 미충족으로 명시 | 사람(화면에서 "다음에 뭘 눌러야 하는지"는 텍스트/버튼 존재 여부로 1차 기계 판정 가능하나, 발견 용이성은 사람 리뷰 필요) |
| J3 페이퍼 주문 | 주문 제출 후 **승인/거부 사유가 일반 오류 배너와 구분**(G-4는 해소됨, `RiskVerdictPanel` 확인), 돈이 걸린 submit 버튼은 이중 입력(수량·대상)을 보여준 뒤 제출해야 하며 제출 중 재클릭으로 중복 주문이 나지 않아야 함(`useIdempotentSubmit` 패턴 존재 확인 — `PaperDeploymentsPage.tsx` 기준) | 기계(멱등 키 사용 여부는 코드 grep으로 확인 가능, 중복 제출 테스트는 Playwright) + 사람(거부 사유 문구가 원인·해결을 말하는지는 사람 판단) |
| J7 모의 운용 시작 | 백테스트 결과 화면에서 모의 운용 배포까지 **클릭 1~2회**(현재는 URL을 직접 쳐야 하므로 사실상 무한대 — 이 기준 미충족), 배포 요청 성공 시 "어디서 상태를 볼 수 있는지"가 같은 화면에 안내돼야 함 | 기계(CTA 존재·클릭 경로 수는 Playwright로 단계 수 측정 가능) |
| J8 모의→실운용 전환 | 전환 시 **확인 단계 ≥1회**(현재 "없음" — 드롭다운 선택만으로 실자금 주문이 나감, 이 기준 미충족), 전환 직후 화면에 "지금부터 실제 자금이 사용됨" 고지와 되돌리기(다시 PAPER로)가 같은 화면에서 가능해야 함, 전환 전 페이퍼 성과 요약이 보여야 함(막연한 전환 금지) | 사람(고지 문구의 명확성) + 기계(확인 모달/2단계 제출 존재 여부는 DOM으로 판정 가능) |
| J9 즉시 중단 | 이상 인지부터 "모든 운용 정지" 완료까지 **클릭 수 상한 후보 3회**(충족 — `/dashboard` 진입 후 "내 운용 전부 정지" 1클릭으로 완료), 정지 액션은 별도 확인 없이 즉시 발동(손실 상황에서 추가 단계는 사용감 저해 — 단, 오발동 방지를 위한 "실행 취소" 안내는 필요, 재개 버튼이 동일 화면에 있음), 개인 트레이더가 전역 정지에 **접근 가능**해야 함(충족 — `/dashboard` 패널, task-10636) | 사람(위기 상황 UX는 사람 시나리오 리뷰 필요) + 기계(정지 API 호출까지의 클릭 수는 Playwright로 측정 가능 — journey-j9 스펙) |
| (공통) 빈 상태·로딩·실패 | 모든 화면에서 빈 데이터/로딩/오류 3상태 각각 안내 문구 존재(G-9와 연결 — 현재 화면마다 개별 `*ErrorBanner`로 구현돼 있어 문구 일관성은 사람 리뷰 필요) | 기계(컴포넌트 렌더 여부) + 사람(문구 일관성·톤) |
| (공통) 처음 쓰는 사용자가 설명 없이 끝낼 수 있는가 | J1·J7·J8처럼 여러 화면을 넘나드는 여정에서 진행률/다음 단계 안내가 없으면 미충족 — `OnboardingFlowPage.tsx`는 J1에 한해 진행 상태 계산이 있으나(G-11) J7·J8에는 그런 장치 자체가 없음 | 사람(설명 없이 완주 가능 여부는 실제 미경험 사용자 관찰이 필요 — Playwright로 대체 불가, 전량 "사람" 판정) |

### 6.4 미확인 목록

- U-1: `AccountDeletionPage.tsx`가 비밀번호 재입력 외에 별도 "정말 삭제" 확인 모달/2단계 확인을
  렌더하는지 — i18n 키(`t12`~`t17`)만 확인했고 실제 번역 문자열·추가 모달 컴포넌트 존재 여부는
  미확인.
- U-2: `is_platform_admin` 플래그가 실제 운영 환경에서 개인 트레이더(페르소나, §0)에게도 부여되는
  배포 구성이 있는지 — 여전히 미확인(운영 데이터 접근 범위 밖, 코드 로직 확인만으로는 판정 불가).
  task-10636에서는 이 플래그 값과 무관하게 비관리자도 `/dashboard`의 "내 운용 전부 정지" 패널로
  전역 정지에 도달하게 해 U-2 값에 의존하지 않도록 우회했다 — U-2 자체는 여전히 열려 있으나 J9
  판정(§6.1, §6.3)을 막지 않는다.
- U-3(해소): `ExecutionCard.tsx:134-153` 확인 — RUNNING 상태일 때 `pause.mutate`(일시정지),
  RETIRED 전까지 상시 `retire.mutate`(퇴역) 버튼이 있다. §6.2 J9 3단계에 반영.
- U-4: J1~J3 외 화면의 체감 지연 실측치(스크리너 외) — §4 성능 예산은 스크리너/차트에만 있고
  `/system/paper-deployments`, `/executions` 등의 p95는 이번 문서에서 측정하지 않음.
- U-5: 모바일/반응형 렌더 결과(G-10과 동일 — 코드만으로는 breakpoint 실제 동작 판정 불가).

### 6.5 우선순위 제안

문서 작성자 제안이며 결정은 PM/CTO 검토 사항이다(task-10599 ADR에서 확정).

1. **J8 모의→실운용 전환에 확인 단계 추가** — 실자금이 걸린 행동에 고지·확인이 없는 상태가 가장
   리스크가 크다(§6.1 "모의 운용 → 실운용 전환" 행, §6.3 J8 기준 미충족). `INVARIANTS.md`의
   자금 관련 불변식과 교차 확인 완료(task-10705, 2026-10-01 — 위 "J8 교차 확인" 참조, 공식 I-xx
   미채번·ADR 선행 필요 상태로 기록). 화면 확인 단계 구현 자체는 아직 남은 프런트엔드 리프.
2. **(해소, task-10636) J9 개인 트레이더용 전역 중단 수단 확보** — `/dashboard`에 "내 운용
   전부 정지" 패널을 신설해 기존 `usePauseExecution`을 본인 소유 RUNNING 실행 전체에 반복 호출하는
   방식으로 해결했다(백엔드/권한 모델 변경 없음). 관리자 전용 `/admin/safety-controls`는 플랫폼
   범위 정지 용도로 그대로 유지.
3. **J7 전략→백테스트→모의 운용 CTA 연결(G-3·G-11과 통합 해결)** — 구현 비용이 낮고(라우팅
   링크 추가 수준) 반복적으로 지적된 패턴(§2 갭 목록 G-3, G-11)과 겹친다.
4. **G-2(대시보드 알림 위젯) 해결** — J9 1단계(상태 인지)의 전제 조건이라 J9보다 먼저 또는
   동시에 다루는 편이 합리적이다.
5. 나머지(§6.4 미확인 항목 해소, G-9 공용 배너 통합)는 위 네 가지보다 사용자 체감 영향이
   작아 후순위로 제안한다.
