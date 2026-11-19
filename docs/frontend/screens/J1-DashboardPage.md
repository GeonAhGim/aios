# J1-DashboardPage — `/dashboard`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/DashboardPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/dashboard` / `frontend/apps/web/src/routes/dashboard/DashboardPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-DashboardPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 개인 트레이더(투자자). 근거: `docs/frontend/PERSONAS_AND_JOURNEYS.md:22` 행 "P1 | 개인 트레이더(투자자) | 전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독 | J1~J6 전체 [확인]". §1 기준선 "개인 시스템 트레이더/리서처 1명" (§9 `docs/specs/UX_JOURNEYS.md` §0). 인용 `DashboardPage.tsx:49-51` 주석 "비관리자(§0 페르소나)"가 긴급 정지를 쓴다는 설명. |
| 목적(한 문장) | 대시보드에서 포트폴리오 요약·실행 중인 전략·최근 알림을 한눈에 보고, 필요하면 내 운용 전부를 즉시 정지하거나 재개한다. 근거: `DashboardPage.tsx:49-55`(긴급 정지 패널 설명), `DashboardPage.tsx:133-148`(데이터 훅 호출 — `useRiskProfile`, `usePortfolio`, `useExecutions`, `useNotificationHistory`), `facts/DashboardPage.facts.md` 화면 문구 목록("포트폴리오 요약", "실행 중인 전략", "최근 알림", "긴급 정지"). |
| 진입 조건 | `frontend/apps/web/src/router.tsx:114` `{ path: "/dashboard", element: protect(<DashboardPage />) }`. `protect` 함수(`router.tsx:70-72`)가 `<ProtectedRoute>`로 감쌈. `ProtectedRoute.tsx:20-21`: `token` 없으면 `/login?next=/dashboard`로 리다이렉트. `ProtectedRoute.tsx:29-30`: `me` 없으면 동일 리다이렉트. `ProtectedRoute.tsx:32-33`: `mfaEnabled` 없으면 `/onboarding/mfa-setup`로. `ProtectedRoute.tsx:35-40`: `mfaEnabled`이고 `riskProfile` 없으면 `/onboarding/risk-assessment`로. `/` 진입 시 `/dashboard`로 리다이렉트(`router.tsx:84`). `AdminRoute`는 이 라우트에 적용되지 않음(`protectAdmin`은 다른 라우트에서 쓰임, `router.tsx:74-80`). 온보딩 라우트(`router.tsx:87-89`)로의 강제 리다이렉트 조건은 `ProtectedRoute`에 따르므로 "미확인(ProtectedRoute 가드 참조)". |
| 사용자 준비물 | 로그인된 세션(인증 필요, `ProtectedRoute.tsx:20-21` 근거). 보유 운용(실행)이 있어야 긴급 정지·실행 중인 전략 카드가 채워진다(`DashboardPage.tsx:64` `RUNNING` 필터, `DashboardPage.tsx:114` 빈 상태 문구 "현재 실행 중인 운용이 없습니다."). 리스크 프로필(`useRiskProfile`, `DashboardPage.tsx:135`) 값이 있으면 "위험등급"이 표시됨(`facts/DashboardPage.facts.md` "위험등급 {{riskProfile}}", `DashboardPage.tsx:155`). 위험 평가 완료 여부가 이 화면의 전제 조건인지는 확인하지 못했으므로 "미확인". 빈 상태 시 온보딩 시작 버튼(`onboarding.emptyStateCta`, `DashboardPage.tsx:231`)은 `/onboarding/first-run`으로 이동(`DashboardPage.tsx:229`). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-DashboardPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `usePauseExecution` | `frontend/packages/shared-hooks/src/usePortfolio.ts:111` | mutation(단일 호출). `mutate(executionId)`로 각 RUNNING 실행 건마다 개별 호출(DashboardPage.tsx:72). 성공 시 `onSuccess`가 `queryClient.invalidateQueries({ queryKey: ["executions"] })`로 executions 쿼리 무효화(usePortfolio.ts:115). | `onError: (err) => setStopError(err)`로 상태 저장(DashboardPage.tsx:72). `stopError`이 null이 되면 `EmergencyStopError` 컴포넌트(DashboardPage.tsx:36-47)가 `classifyForbidden` 경로로 `ForbiddenNotice`(DashboardPage.tsx:37) 또는 `routeApiError`로 `ErrorMessage`(DashboardPage.tsx:38) 표시. trace_id는 `ErrorMessage`에 전달됨(DashboardPage.tsx:43). |
| `useStartExecution` | `frontend/packages/shared-hooks/src/usePortfolio.ts:60` | mutation(단일 호출). `mutateAsync({ executionId, idempotencyKey })`로 재개 시 한 건 호출(DashboardPage.tsx:77-78). idempotencyKey 형식: `dashboard-resume-${executionId}-${crypto.randomUUID()}`. 성공 시 `onSuccess`가 executions 쿼리 무효화(usePortfolio.ts:70). | `.catch((err) => setStopError(err))`로 상태 저장(DashboardPage.tsx:80). 같은 `EmergencyStopError` 경로로 표시(DashboardPage.tsx:86). |
| `useRiskProfile` | `frontend/packages/shared-hooks/src/useSuitability.ts:6` | query(1회 조회). 폴링 없음, staleTime 기본(QueryClient 기본값). `enabled: !!token`(useSuitability.ts:11) — 토큰 있을 때만 조회. queryKey: `["riskProfile"]`. | DashboardPage.tsx:135에서 `data: riskProfile`만 destructures. `isError`/`error` destructuring 없음. 위험등급 뱃지 표시 실패 시 사용자 피드백 없음. |
| `usePortfolio` | `frontend/packages/shared-hooks/src/usePortfolio.ts:11` | query(1회 조회). 폴링 없음, staleTime 기본. queryKey: `["portfolio"]`. | DashboardPage.tsx:139-140에서 `isError`/`error`를 destructures하지만, 포트폴리오 데이터 실패 시 화면에 오류를 표시하는 UI가 없음(포트폴리오 요약 섹션이 단순히 비어 있음). |
| `useExecutions` | `frontend/packages/shared-hooks/src/usePortfolio.ts:37` | query(5초 폴링). `refetchInterval: 5000`(usePortfolio.ts:42). 주석 "FD-16.4 실시간성 요구 — 5초 폴링(Draft, 17번 문서 §17.4와 동일 원칙)". queryKey: `["executions"]`. | DashboardPage.tsx:142에서 `isError`를 destructures하지만(`executionsIsError`), 실행 중인 전략/긴급 정지 섹션에서 isError 분기 없음. executions 데이터 실패 시 빈 상태("현재 실행 중인 운용이 없습니다.")가 대신 표시됨(DashboardPage.tsx:114). |
| `useNotificationHistory` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:45` | query(1회 조회). 폴링 없음, staleTime 기본. queryKey: `["notificationHistory", eventType]`. eventType 인자 없음(전체 알림). | DashboardPage.tsx:146-147에서 `isError`/`error`를 destructures(`notificationsError`/`notificationsErrorObj`). notificationsError가 true면 `<ErrorMessage>` 표시(DashboardPage.tsx:244-247). errorCode와 message 전달. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-DashboardPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | executions·portfolio·notifications 중 하나 이상이 `isLoading` true (`executionsLoading`, `portfolioLoading`, `notificationsLoading`) | 포트폴리오 섹션: `<LoadingState />` (:187). 전략 섹션: `<LoadingState />` (:211). 알림 섹션: `<LoadingState />` (:249). OperationStatusBar 로딩 인디케이터 (:160). | 없음 — 데이터 로딩 중에는 상호작용 비활성. | 구현됨 (`DashboardPage.tsx:160`, `:186`, `:210`, `:248`) |
| 빈 상태 | executions가 빈 배열 또는 null이고, portfolio가 null | 긴급 정지 패널: "현재 실행 중인 운용이 없습니다." (`legacy.dashboardPage.emergencyStopEmpty`, :114). 전략 섹션: "실행 중인 전략이 없습니다." (`legacy.dashboardPage.t8`, :236) + "온보딩 시작하기" 버튼 (`onboarding.emptyStateCta`, :231). 포트폴리오 섹션: 내용 없음 (:205 `: null`). | "온보딩 시작하기" 클릭 시 `/onboarding/first-run`으로 이동 (:229). | 구현됨 (`DashboardPage.tsx:114`, `:227-237`, `:231`, `:205`) |
| 정상 | executions·portfolio·notifications 모두 데이터 있고, 에러 없음 | 포트폴리오 요약: 총 가치/미배분 현금/배분 수 Stat 3개 (:191-193), AllocationBarChart (:196). 실행 중인 전략 목록 (:213-224). 최근 알림 목록 (:254-259). 긴급 정지 패널: 실행 중 전략 목록 + 정지/재개 버튼 (:88-128). | 전체 정지 (:124), 개별 재개 (:104), 전략/알림 확인. | 구현됨 (`DashboardPage.tsx:188-204`, `:212-225`, `:252-260`, `:87-128`) |
| 낡은 데이터(신선도 초과) | executions는 5초 폴링 (:42)이지만, portfolio는 staleTime 기본 (QueryClient 기본값, 보통 staleTime=0) | `DataFreshness` 컴포넌트 (:174)가 `asOf={null}`으로 전달됨 — 서버가 `meta.as_of`를 제공하지 않아 항상 "확인 불가". stale 배지 절대 안 뜨 (:169-173 주석 참조). | 없음 (신선도 표시 비기능) | 부분 구현 (`:174`, `asOf=null`로 stale detection 비활성, PLT-19 예정) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-DashboardPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 폼 입력 검증 오류 — 이 화면에 입력 폼 없음. 긴급 정지 mutation의 400 응답은 `frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:36-47`의 EmergencyStopError 컴포넌트가 `frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:40`의 ErrorMessage로 표시. | ErrorMessage: message, errorCode, traceId 전달받아 표시 | 재시도 버튼 없음. 사용자가 직접 정지 버튼 재클릭 | 있음 (emergency stop mutation 에러 경로만) |
| 인증 필요/세션 만료(401) | `frontend/apps/web/src/router.tsx:70-72`의 ProtectedRoute가 토큰 없으면 /login?next=/dashboard로 리다이렉트. 페이지 내부 401 핸들러 없음. | 리다이렉트 후 로그인 페이지 | 로그인 후 자동 복귀 | 없음 (ProtectedRoute에서 처리) |
| 권한 없음/MFA 필요(403) | `frontend/apps/web/src/router.tsx:74-80`의 ProtectedRoute가 MFA 미설정 시 /onboarding/mfa-setup로 리다이렉트. 긴급 정지 에러 경로에서 `frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:37`의 classifyForbidden이 true면 ForbiddenNotice 표시. | ForbiddenNotice: 403 응답 시 표시 | 없음. 관리자 조치 필요 | 있음 (emergency stop에서만 classifyForbidden→ForbiddenNotice) |
| 충돌/상태 불일치(409) | usePauseExecution mutation이 409를 특별히 처리하지 않음. `frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:38`의 routeApiError로 라우팅되나 409 구분 없음. | ErrorMessage: 일반 오류 문구 표시 | 재시도 — 사용자가 정지 버튼 재클릭 | 없음 (409 전용 처리 없음) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-DashboardPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-DashboardPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:99`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:118`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:230`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-DashboardPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-DashboardPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:37`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:37`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:38`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:40`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-DashboardPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-DashboardPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-DashboardPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
