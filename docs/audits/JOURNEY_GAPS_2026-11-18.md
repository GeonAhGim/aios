# UX_JOURNEYS.md §2 갭 목록(G-1~G-11) 현황 점검 보고서 (2026-11-18)

- 기준: `docs/specs/UX_JOURNEYS.md` §2 갭 목록(2026-10-01 기준). 코드는 수정하지 않았다(읽기 전용, grep·파일 읽기·git log로만 확인).
- 소속 판정: 갭이 근거로 든 §1 표가 J1~J3면 소속, J4~J6(MVP-2)이면 제외. 여러 화면에 걸치면 "공통".
- 이전 시도(attempt1) 초안은 G-1·G-3·G-5·G-6 판정이 근거와 맞지 않아 이번에 처음부터 재확인했다.

| 갭 | 소속 | 현재 상태 | 근거 | 미해소 시 제안 |
|---|---|---|---|---|
| G-1 | J3 (UX_JOURNEYS.md:90 팔로우 행) | 미해소 | `src/api/routers/follow.py` 없음(ls 실패), `router_registry.py`·`contracts/openapi/v1.json`에 follow 0건(grep -c), `apiRoutes.ts:372-383`에 `follow.subscriptions.*`가 `implemented=false`로 남음. 클라이언트 `clients/follow.ts`만 존재 | follow 라우터 신설 + registry·openapi 동기화 후 `implemented` 전환 |
| G-2 | J1 (8 첫 대시보드) | 해소 | `DashboardPage.tsx:3,148` `useNotificationHistory()` 사용, `:248` 로딩/목록 분기 렌더. 커밋 `104584411` "task-7776 G-2 최근 알림 위젯 추가" | — |
| G-3 | J2 (5 스크립트 편집) | 미해소 | `ScriptEditorPage.tsx:102,122` 컴파일 성공 후 버튼 `compile-open-chart`는 `/chart?script_hash=…`로 이동할 뿐 백테스트 진입이 아님. 커밋 `a444642ac`(task-7785)는 차트 CTA까지만 연결. 백테스트 직접 CTA는 확인 못 함 | 컴파일 성공 패널에 백테스트 실행/이동 CTA 추가 |
| G-4 | J3 (2 리스크 판정 표시) | 해소 | `ExecutionControlPage.tsx:34` `RiskVerdictPanel` import, `RiskVerdictPanel.tsx` 존재(커밋 `804cee92e` task-7500). 단 `featureFlags.ts:16` `FF_J3_RISK_PANEL: false`(기본 OFF) — 코드는 해소, 노출은 플래그 의존 | (참고) 플래그 기본값 ON 전환 여부 결정 |
| G-5 | J3 (5 컴플라이언스/위임장) | 미해소 | `MandatePage.tsx`(201줄)·`MandatesPage.tsx`(200줄) 둘 다 존재하고 `useMandateStatus`·`usePauseMandate` 등 동일 훅 사용(grep `:4-5`). 통합/역할 분리 흔적 없음 | 한 화면으로 통합하거나 역할 분리 결정 후 라우트 정리 |
| G-6 | 제외 (J4, UX_JOURNEYS.md:99) | 판단불가 | J4는 MVP-2라 범위 밖. 참고: `DashboardPage.tsx:49-56` J9 전역 긴급 정지 패널(task-10636)은 있으나 안전 통제 *발동 시* 배너 전파는 확인 못 함. `useSafetyControls`는 `admin/SafetyControlsPage*`에서만 사용(grep) | (MVP-2에서 처리) |
| G-7 | 제외 (J4, UX_JOURNEYS.md:100) | 미해소 | J4는 범위 밖. 참고: `ExecutionCard.tsx:39-40` 주석이 pausedBy 필드 부재 명시, `src/`·`shared-types`에 `pausedBy` 응답 필드 0건(백엔드는 `routers/executions.py:161` `paused_by="USER"` 인자만 있음) | (MVP-2에서 처리) |
| G-8 | J1 (데모 온보딩) | 판단불가 | `DemoModePage.tsx`에 "실계좌/미접촉" 문구 없음(grep 0건), 문구는 i18n 키 `demoMode.description`(`:16`)에 있어 catalog 값을 이번에 확인하지 못함. 렌더 문구 미확인 | 데모 화면에 "실계좌 미접촉" 고지 문구 명시 및 catalog 값 확인 |
| G-9 | 공통 (J1~J3 화면 포함) | 미해소 | `ui-web/src/Feedback.tsx`에 ErrorBanner 없음(grep 0건), 라우트별 개별 `*ErrorBanner`(`ai/AiErrorBanner.tsx`, `admin/TrustPage.tsx` 등) 존속 | 공용 EmptyState/ErrorBanner를 ui-web으로 통합 |
| G-10 | 공통 (J1~J3 라우트) | 판단불가 | 정적 확인 한계: `ui-web/src`·`web/src`의 `.ts/.tsx/.css`에서 `@media`/`breakpoint`/`useMediaQuery` grep 결과 없음(Tailwind 반응형 접두어는 미검토) — 렌더 결과는 코드만으로 판정 불가(원문도 동일) | 375/768 뷰포트 수동 점검 후 breakpoint 정의 |
| G-11 | J1 (5 첫 실행 체크리스트) | 부분 해소 → 미해소 | `onboarding/OnboardingProgressWidget.tsx` 존재, 사용처는 `OnboardingFlowPage.tsx`·`ExchangeManagementPage.tsx`뿐(grep -l). `StrategyBuilderPage.tsx`·paper-deployments에는 없음. 커밋 `a55fa68ac`(task-10642)가 `/exchanges`까지만 배선 | 위젯을 `/strategy-builder`·`/system/paper-deployments`에 배선 |

## 요약

- J1~J3 소속 갭: G-1, G-2, G-3, G-4, G-5, G-8, G-11 (+ 공통 G-9, G-10). J4 소속 G-6·G-7은 제외.
- J1~J3 소속 **미해소** 갭: G-1, G-3, G-5, G-9, G-11 → **5건**.
- 판단불가(J1~J3 관련): G-8, G-10 → 2건. 해소: G-2, G-4(플래그 기본 OFF).
