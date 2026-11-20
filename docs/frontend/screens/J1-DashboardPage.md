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
| 잠김/속도 제한(423/429) | HTTP 423 (AUTH_ACCOUNT_LOCKED) 또는 429 (RATE_LIMIT_EXCEEDED). 423은 accountLockout.ts:41 `deriveLockout`이 `statusCode===423 && errorCode==='AUTH_ACCOUNT_LOCKED'`일 때 `locked=true` 판정. 429는 retryable.ts:27 `BACKOFF_ERROR_CODES` 세트에 등록됨. | 429: `ErrorMessage.tsx:38-69`에서 `classifyRetry`가 `backoff`로 분류 → `retryAfterSec` 카운트다운 + "재시도" 버튼 활성화. 423: `deriveLockout`이 `account_locked`로 분류 → `errorRouting.ts:126`에서 `RoutedApiError.kind === "account_locked"`. `ErrorMessage`의 `classifyRetry`는 `none` 반환 → 재시도 버튼 없음. | 429: 재시도 버튼 클릭 시 호출부(각 훅)가 다시 조회. 423: 재시도 불가 — 잠금 해지 대기. | 429 구현 확인: `shared-types/src/retryable.ts:27-31` BACKOFF_ERROR_CODES에 RATE_LIMIT_EXCEEDED 등록, `ErrorMessage.tsx:38` classifyRetry 호출, `ErrorMessage.tsx:62-69` 백오프 카운트다운 UI. 423 구현 확인: `shared-types/src/accountLockout.ts:41-49` deriveLockout, `errorRouting.ts:125-126` account_locked 라우팅. |
| 서버 오류(5xx) | HTTP 500 (INTERNAL_ERROR), 502 (EXCHANGE_FATAL), 503 (EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY). serverError.ts:25-30에서 RETRYABLE_SERVER_ERROR_CODES(503 2개) vs FATAL_SERVER_ERROR_CODES(502, 500)로 분류. | 503: `serverError.ts:42-44`에서 `retryable` → `errorRouting.ts:116`에서 `backoff_retry`. `ErrorMessage.tsx:36-69`에서 백오프 카운트다운 + 재시도 버튼. 500/502: `serverError.ts:47-48`에서 `fatal` → `errorRouting.ts:117`에서 `server_fatal`. traceId 표시 (`ErrorMessage.tsx:60`). 재시도 버튼 없음. | 503: 백오프 후 재시도. 500/502: 재시도 불가 — traceId와 함께 오류 메시지 표시. | 503 구현 확인: `serverError.ts:25-28` RETRYABLE_SERVER_ERROR_CODES, `httpErrors.ts:49-52` SERVER_ERROR_BACKOFF_SEC=[1,2], `withGetRetry(httpErrors.ts:70-82)`에서 자동 재시도. 500/502 구현 확인: `serverError.ts:30` FATAL_SERVER_ERROR_CODES → fatal 분류 → traceId 표시. |
| 네트워크 단절 | fetch() 호출 자체가 실패하는 경우 (온라인 → 오프라인 전환, DNS 실패, 연결 끊김). `http.ts:46`의 fetch가 throw하는 네트워크 오류. react-query는 해당 오류를 `error` 필드에 전달. | react-query의 `error` 필드에 `Error` 인스턴스가 들어감. 각 훅 호출부에서 `.error`를 추출해 `ErrorMessage` 컴포넌트에 전달. 네트워크 오류는 errorCode가 없으므로 `errorRouting.ts:132`에서 `unknown`으로 수렴. 메시지: "요청을 처리할 수 없습니다." (`httpErrors.ts:98`). | 네트워크 복구 후 react-query가 자동 재시도 (retry 설정에 따라). 수동 재시도는 재조회 버튼 없음 — 페이지 새로고침 또는 다른 페이지 이동 시 복귀 시 react-query가 refetch. | 구현 확인: `http.ts:46` fetch 호출, 네트워크 오류는 `buildApiError`로 감싸지지 않고 그대로 throw → react-query `error` 필드. `errorRouting.ts:132` fallback "unknown". `ErrorMessage.tsx:36` getApiErrorMessage 호출 — errorCode가 없으면 "요청을 처리할 수 없습니다." 표시. |
| 부분 실패(일부 데이터만 실패) | 대시보드가 4개 독립 쿼리(riskProfile, portfolio, executions, notifications)를 병렬로 조회할 때, 하나만 실패하고 나머지는 성공. DashboardPage.tsx:36-48에서 각 훅을 별도 변수로 분할. | 실패한 쿼리 섹션에만 `<ErrorMessage error={...}>` 배너가 표시됨. 성공한 데이터는 정상 표시. 예: portfolio 조회 실패 → 포트폴리오 요약 섹션에만 오류 배너, 실행 중인 전략 섹션은 정상. | 실패한 섹션의 재조회 (페이지 새로고침 시 react-query 자동 refetch). 다른 섹션은 영향 없음. | 구현 확인: `DashboardPage.tsx:36-48` — riskProfile, portfolio, executions, notifications 각 훅을 별도 const로 분할. `DashboardPage.tsx:51,55,59,63` — 각 섹션에 `<ErrorMessage error={...}>` 개별 배선. `DashboardPage.tsx:142-148` — executions, notifications도 개별 isError/error 필드 사용. |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-DashboardPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:99`) — '재개' 버튼 | `stoppedIds`가 비어 있지 않고, 클릭된 `executionId`가 `resumedIds`에 없음(DashboardPage.tsx:91 `resumedIds.includes(executionId)` 가 false). 해당 실행 건이 현재 `executions` 목록에 존재(DashboardPage.tsx:90 `executions.find(...)`). | `handleResume`(:76-81)이 `startExecution.mutateAsync`(:78)를 호출하고, 성공 시 `setResumedIds`에 `executionId`를 추가(:79). 배지가 `"PAUSED"` → `"RUNNING"`으로 바뀜(:96). `useStartExecution`의 `onSuccess`가 `queryClient.invalidateQueries({ queryKey: ["executions"] })`로 executions 쿼리 무효화(usePortfolio.ts:70). 같은 화면 유지(이동 없음). | `.catch((err) => setStopError(err))`(:80). `stopError`에 예외 객체가 저장되어 `EmergencyStopError`(:86)가 `classifyForbidden` 경로면 `ForbiddenNotice`(:37), 아니면 `ErrorMessage`(:40)를 표시. `handleResume`은 `stopError`를 지우지 않으므로, 이전에 표시된 오류가 이 행에서도 남아 있음. 같은 화면 유지. | 없음(같은 화면). |
| `<Button>` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:118`) — '내 운용 전부 정지' 버튼 | `stoppedIds`가 비어 있음(DashboardPage.tsx:87 `stoppedIds.length > 0` 가 false). `runningExecutions`가 1개 이상 — `executions` 목록에서 `status === "RUNNING"`인 건이 있음(DashboardPage.tsx:64, :113). | `handleStopAll`(:66-74)이 `runningExecutions`의 각 executionId마다 `pauseExecution.mutate`(:72)를 호출. 각 mutation 성공 시 `usePauseExecution.onSuccess`가 `queryClient.invalidateQueries({ queryKey: ["executions"] })`로 executions 쿼리 무효화(usePortfolio.ts:115). `stoppedIds`에 모든 ID를 설정(:70)하여 UI가 정지 목록으로 전환. 배지는 `"PAUSED"`로 고정(:96). 확인 모달 없음(주석 :53-54: "위기 상황에서 추가 단계는 사용감 저해"). 같은 화면 유지. | 건별 `onError: (err) => setStopError(err)`(:72). 여러 건이 실패하면 마지막 오류만 `stopError`에 남음. `stopError`가 null이 되면 `EmergencyStopError`(:86)가 `ForbiddenNotice`(:37) 또는 `ErrorMessage`(:40) 표시. 403이면 `classifyForbidden` 경로로 `ForbiddenNotice`(:37). `stoppedIds`가 이미 설정되어 있으므로 UI는 정지 목록을 표시한 채 오류만 표시. 같은 화면 유지. `stoppedIds` 초기화 코드가 :70 외 없음 — 정지 후 같은 마운트 안에서 정지 버튼(`:118`)은 `stoppedIds.length > 0`이 true가 되어 사라짐. | 없음(같은 화면). |
| `<Button>` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:230`) — '온보딩 시작하기' 버튼 | `executionsLoading`이 false이고 실행 목록이 비어 있음(DashboardPage.tsx:210 `executionsLoading || !recentExecutions` 가 true, :226 빈 상태 분기). | `<Link to="/onboarding/first-run">`(:229)가 `react-router-dom`의 `<Link>` 컴포넌트를 통해 `/onboarding/first-run`으로 페이지 전환. 라우트 정의: `router.tsx:89` `{ path: "/onboarding/first-run", element: protect(<OnboardingFlowPage />) }`. `protect` 함수(`router.tsx:70-72`)가 `<ProtectedRoute>`로 감싸 인증 확인. | 버튼 자체에 실패 경로 없음(클릭 시 `<Link>`가 즉시 이동). 라우트 가드(`ProtectedRoute`)가 토큰/세션/리스크프로필 조건을 위반하면 `/login` 또는 `/onboarding/mfa-setup` 등으로 리다이렉트할 수 있으나, 이 화면에서 확인한 범위 밖이므로 '미확인(ProtectedRoute 가드 참조, `ProtectedRoute.tsx:20-40`)'. | `/onboarding/first-run` 페이지(`OnboardingFlowPage`, `router.tsx:89`). |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-DashboardPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 — 이 화면은 실행 정지(`handleStopAll`, `DashboardPage.tsx:66-74`, A3 전략 일시정지)와 실행 재개(`handleResume`, `:76-81`, A2 전략 시작)라는 두 가지 금전·위험 행동을 제공한다. 정지는 `usePauseExecution.mutate`(:72)를 RUNNING 실행 전체에 반복 호출하고, 재개는 `useStartExecution.mutateAsync`(:78)를 단일 건에 호출한다. 주문 제출·LIVE 전환·위임장 변경·출금은 이 파일에 없음(`:65-83` 범위 검색 결과 없음) |
| 확인 절차 | 정지 — 확인 모달 없음. 주석 `DashboardPage.tsx:53-54` "위기 상황에서 추가 단계는 사용감 저해, UX_JOURNEYS.md §6.3 J9 기준". 클릭 즉시 `handleStopAll`(:66)이 모든 RUNNING 실행에 정지 mutation 발동(:72). 재개 — 확인 모달 없음. 버튼 클릭 시 바로 `handleResume`(:76-81)이 `mutateAsync` 호출. 모든 입력 칸이 비면 아무 메시지 없이 `return`하는 패턴 없음(버튼 클릭이 유일한 진입점) |
| 멱등성 | 정지: `pauseExecution.mutate(executionId)` 직접 호출, 멱등키 없음(MONEY_ACTIONS.md A3 행 "없음 (mutate 직접 호출)"과 일치). 재개: `idempotencyKey: dashboard-resume-${executionId}-${crypto.randomUUID()}`(:78) 전달. `mutateAsync` 사용으로 Promise 반환, `.catch`로 오류 처리(:80). 중복 클릭 방지(`useIdempotentSubmit` 훅)는 미적용 — 직접 `mutateAsync` 호출 |
| 불확실 결과 표시 | 정지/재개 모두 `onError`/`.catch`로 `stopError`에 오류 저장(:72, :80). `stopError`가 null이 되면 `EmergencyStopError`(:86)가 `classifyForbidden` 경로면 `ForbiddenNotice`(:37), 아니면 `ErrorMessage`(:40) 표시. "요청이 반영됐을 수 있음" 같은 불확실 안내 문구 없음. 정지 후 `stoppedIds`가 이미 설정되어 UI는 정지 목록을 표시한 채 오류만 표시(:70). 재개 실패 시 `stopError`가 초기화되지 않아 이전 오류가 남아 있음(:80 주석 참조) |
| 금액 검증 | 이 화면의 금전 행동(정지·재개)은 금액 입력 칸이 없음. 포트폴리오 요약 섹션(:191-193)은 읽기 전용 표시(`Stat` 컴포넌트)이며 입력 폼 없음. 사실 시트 `Form fields` 표 공백(`DashboardPage.facts.md:29-33`)과 일치 |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-DashboardPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:37`) | i18n 키 없음(문구를 만들지 않는 순수 분류 함수, `frontend/packages/shared-types/src/forbidden.ts:31`). 403이면 `mfa_required`/`tenant_mismatch`/`policy`/`forbidden` 중 하나(:35-40), 403이 아니면 `null`(:32). | `null`이면 `routeApiError`→`ErrorMessage` 경로로 진행(`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:38-45`), 403이면 `ForbiddenNotice`로 위임(:37). 자체 안내 없음. | 아니오 — 분류만 수행. |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:37`) | i18n 키 아님: `getApiErrorMessage`의 하드코딩 한국어 고정문(`frontend/apps/web/src/components/ForbiddenNotice.tsx:36`). `AUTHZ_FORBIDDEN`="이 작업을 수행할 권한이 없습니다."(`frontend/packages/shared-types/src/apiError.ts:68`), `AUTH_MFA_REQUIRED`="추가 인증이 필요합니다."(:65), `POLICY_`/`RISK_` 접두 문구(:90-91). 유일한 i18n 키는 step-up 버튼 `legacy.forbiddenNotice.t1`("step-up 인증", `catalog.ko.legacyC.ts:15`; `ForbiddenNotice.tsx:45`). | 정책 거부(`policy`)면 `DenialReasons`가 사유를 추가 표시(`ForbiddenNotice.tsx:41`). `mfa_required`일 때만 `onStepUp`이 있으면 버튼 표시(:42-47). 이 화면은 `onStepUp`을 넘기지 않아(`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:37`) 버튼이 렌더링되지 않는다 — 추가 인증을 시작할 수단이 없음(갭). 그 외 갈래는 재시도 버튼 없음(:9-13 주석). | 아니오 — `traceId`를 받거나 표시하지 않는다(`ForbiddenNotice.tsx:14-50`). |
| `routeApiError` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:38`) | i18n 키 없음(`frontend/packages/shared-types/src/errorRouting.ts:88`, 분류 결과 `kind`만 반환). 이 화면은 `kind === "backoff_retry"`일 때의 `afterSec`만 사용(`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:44`). | `backoff_retry`면 `afterSec`를 `ErrorMessage`의 `retryAfterSec`으로 전달(:44). 나머지 `kind`(`server_fatal` 포함)는 화면 분기에 쓰이지 않고 `ErrorMessage`가 `errorCode`로 문구를 정한다(:41). `server_fatal`의 `traceId`(`errorRouting.ts:117`)는 사용하지 않음. | 아니오 — 분류만 수행. |
| `ErrorMessage` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:40`) | `getApiErrorMessage(errorCode, message)`의 고정 한국어 문구(`frontend/apps/web/src/components/ErrorMessage.tsx:36`, 미지 코드는 접두 문구 또는 기본 문구, 서버 `message`는 `errorCode`가 없을 때만 폴백: `apiError.ts:106-117`). i18n 키: `legacy.errorMessage.t1`("지원코드: {{traceId}}"), `t2`("{{remainingSec}}초 후 재시도 가능"), `t3`("다시 시도")(`catalog.ko.legacyB.ts:10-12`; `ErrorMessage.tsx:60,64,67`). | 재시도 버튼은 `classifyRetry`가 `none`이 아니고 `onRetry`가 있을 때만 표시(`ErrorMessage.tsx:61`). 이 화면은 `onRetry`를 넘기지 않아(`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:40-45`) 버튼이 렌더링되지 않고, 카운트다운 문구(:63-65)도 `onRetry` 블록 안이라 보이지 않는다 — `backoff_retry`의 `afterSec` 전달(:44)이 화면에 아무 효과가 없음(갭). 사용자는 긴급 정지 버튼을 다시 눌러 재시도해야 한다(`handleStopAll`이 `stopError`를 비움, :66-68). | 예 — `ApiError.traceId`가 있으면 "지원코드: …"로 표시(`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:43`, `ErrorMessage.tsx:60`). `ApiError`가 아닌 오류면 `traceId`가 `undefined`라 미표시. `ForbiddenNotice` 경로(403)에서는 미표시. |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-DashboardPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | 모든 상호작용 요소가 네이트 HTML `<button>`/`<Link>` 사용 — 시맨틱 HTML 기본 접근성 활용(`DashboardPage.tsx:99-107`, `:118-126`, `:229-233`). 정지된 실행 목록이 `<ul>/<li>`로 구조화(`:88-112`). `StatusBadge`가 색만으로 상태(RUNNING/PAUSED)를 표시 — WCAG 1.4.1(색 시각장애) 및 1.4.3(대비) 미준수 가능. `data-testid`/`aria-label`/`role` 속성이 전무(facts 테이블 빈 칸). | `StatusBadge`에 `aria-label` 추가(예: `status="RUNNING"` → `"실행 중"`). 테스트용 `data-testid` 누락(8개 Button/EmptyState/LoadingState). 색 대신 아이콘/텍스트 레이블 병기. |
| i18n/지역화 | 모든 고정 문자열이 t() 키로包裹: legacy.dashboardPage.* 16개 키(`:85`, `:106`, `:114`, `:117`, `:125`, `:154`, `:155`, `:168`, `:191-193`, `:209`, `:231`, `:236`, `:242`, `:251`) + onboarding.emptyStateCta(`:231`). 모두 catalog.ko.legacyB.ts:114-129 및 catalog.en.legacyB.ts:112-128에 ko/en 쌍 존재. t("legacy.dashboardPage.t2", { riskProfile: ... }) 파라미터 보간 사용(`:155`). 누락 키 없음. | 없음 |
| 성능 예산 | useExecutions가 5초 폴링(`shared-hooks/src/usePortfolio.ts:42` `refetchInterval: 5000`). useRiskProfile/usePortfolio는 폴링 없음(staleTime 기본). 렌더 성능 테스트 존재(`DashboardPage.depth1.test.tsx:145-160`): 예산 400ms, 실측 9.2~12.6ms(3회 평균). bestOfMs로 worst-of-N 측정(`:149`). | 테스트에 @perf 마커 부재 — vitest 테스트이므로 Python check_perf_marker_guard.py 대상 아님. 폴링 간격 5초가 과도한지(사용자 활동 패턴에 따라 조정 필요) 미확인. |
| 텔레메트리 | `DashboardPage.tsx`에 `reportError`/`captureException`/`telemetry`/`analytics`/`Sentry` 호출이 전무. `ErrorMessage` 컴포넌트(`:40-45`, `:244-247`)가 오류를 로컬 UI로만 표시하고 외부 보고 없음. `ForbiddenNotice`(`:37`)도 로컬 403 처리. | 오류 발생 시 외부 모니터링(Sentry 등)으로 보고하지 않음 — 서버 로그/traceId(`:43`)로만 디버깅 가능. |
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
