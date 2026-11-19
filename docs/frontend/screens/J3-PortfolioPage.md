# J3-PortfolioPage — `/portfolio`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/PortfolioPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/portfolio` / `frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-PortfolioPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 개인 트레이더 (보조 후보 P5 자산운용사는 다중 고객 화면이 [미확인]이라 단정하지 않음; `docs/frontend/PERSONAS_AND_JOURNEYS.md:22`, `:26`) |
| 목적(한 문장) | 총 포트폴리오 가치·미배분 현금·배분 내역을 보고 실행별 배분 자본을 조정(리밸런스)한다 (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:115`, `:137`, `:174`) |
| 진입 조건 | `/portfolio`는 `protect()`로 감싸져 `ProtectedRoute`(로그인 가드)만 거친다 (`frontend/apps/web/src/router.tsx:133`, `:70-72`). `AdminRoute`는 없고 기능 플래그 가드도 이 라우트에는 없다. `ProtectedRoute` 내부 동작은 미확인 |
| 사용자 준비물 | 로그인 세션, 배분된 실행(allocation)이 있어야 표에서 재조정 가능(없으면 빈 상태 안내, `PortfolioPage.tsx:181`). 재조정 입력값은 실행별 새 배분 자본(숫자) (`PortfolioPage.tsx:147`, `:160-165`). 그 외 계좌·권한 요건은 미확인 |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-PortfolioPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `usePortfolio` | `frontend/packages/shared-hooks/src/usePortfolio.ts:11` | `useQuery`로 `["portfolio"]` 쿼리 키를 통해 `apiClient.getPortfolio()`를 마운트 시 1회 조회. `refetchInterval` 없음(폴링 안 함). 재시도는 `refetch()` 수동 호출(`PortfolioPage.tsx:53`, `:106`) | `isError`가 true가 되면 `PortfolioPage.tsx:61`에서 `routeApiError(portfolioError)`로 분류. `refetch_retry` 또는 `backoff_retry`일 때만 재시도 버튼(`onRetry`, `:106`) 표시. `EXCHANGE_FATAL`은 안내만(`:63`). 전역 `QueryClient` 기본 설정(`frontend/apps/web/src/main.tsx:15`)은 react-query 기본값 사용 |
| `useRebalancePortfolio` | `frontend/packages/shared-hooks/src/usePortfolio.ts:15` | `useMutation`로 `apiClient.rebalancePortfolio(body, idempotencyKey)`를 `mutateAsync`로 호출(`PortfolioPage.tsx:76`). 성공 시 `onSuccess`에서 `["portfolio"]` 쿼리 무효화(`usePortfolio.ts:25`)로 포트폴리오 데이터 갱신 | `handleRebalance`(`:79-82`)에서 `catch` → `DuplicateSubmitError`면 무시(`:80`). `ApiError`면 `setError(err)`, 아니면 번역 문구 `t("legacy.portfolioPage.t14")`로 `setError`(`:81`). 실패 시 `PortfolioPage.tsx:172`의 `<RebalanceError error={error} />`로 에러 표시. `drafts`는 성공 경로(`:78`)에서만 비워지므로 실패 시 입력값 유지. 전역 `QueryClient` 기본 `retry`는 기본값(미설정) |
| `useIdempotentSubmit` | `frontend/apps/web/src/hooks/useIdempotentSubmit.ts:30` | 재시도 시 같은 `idempotencyKey` 재사용. 성공(`:43`) 또는 4xx 실패(`:17-19`, `isNonRetryableFailure`) 시 `discardKey`로 키 폐기. 5xx·네트워크 실패 시 키 유지(`:58-60`). `inFlightRef`로 중복 제출 가드 — `DuplicateSubmitError` 던짐(`:36-38`) | `DuplicateSubmitError`는 호출부가 조용히 무시(`PortfolioPage.tsx:80`). 그 외 오류는 `catch`로 올라가 `setError`로 화면 전달(`:81`). 키 폐기 후 재시도 가능(새 키 발급). `missing_header`(400/409)는 `console.warn` 후 키 폐기(`:51-57`) |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-PortfolioPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `usePortfolio`(`frontend/packages/shared-hooks/src/usePortfolio.ts:11`)의 `isLoading`이 true (`PortfolioPage.tsx:108`) | `<LoadingState />` 표시 (`PortfolioPage.tsx:109`) | 없음(로딩 완료까지 대기) | `구현됨` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:108-109`) |
| 빈 상태 | `portfolio` 데이터가 존재하지만 `portfolio.allocations.length === 0` (`PortfolioPage.tsx:180`) | `<EmptyState>{t("legacy.portfolioPage.t13")}</EmptyState>` — "배분된 실행이 없습니다." (`PortfolioPage.tsx:181`) | 없음(배분된 실행이 없어 재조정 불가) | `구현됨` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:180-182`, 사실 시트: `legacy.portfolioPage.t13` 배분된 실행이 없습니다.) |
| 정상 | `portfolio` 데이터가 존재하고 `allocations.length > 0` (`PortfolioPage.tsx:110`, `:123`) | 총 포트폴리오 가치(`label2`), 미배분 현금(`label3`), 배분된 실행 수(`label4`) Stat 3개; 자산 배분 차트(`t5`); 배분 내역·재조정 표(`t6`~`t10`) 전략/비중/손익/새 배분 자본 열; 재조정 적용 버튼(`t11`); 결과 메시지(`t12`) | 재조정 입력 후 `<Button>`(`t11`) 클릭 → `handleRebalance`(`PortfolioPage.tsx:65`) → `useIdempotentSubmit`(`:55`) + `useRebalancePortfolio`(`:54`)로 POST (`PortfolioPage.tsx:75-76`) | `구현됨` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:110-189`, 사실 시트: `legacy.portfolioPage.title1`~`t13` 전체) |
| 낡은 데이터(신선도 초과) | `DataFreshness` 컴포넌트(`frontend/apps/web/src/components/DataFreshness.tsx:20`)는 `asOf`가 null일 때 "기준 시각 확인 불가"(`legacy.dataFreshness.t1`)를 표시. 현재 `PortfolioPage.tsx:95`에서 `asOf={null}` 고정 (`apiPaths.ts "portfolio.get" · PLT-19 예정`으로 ApiResponse 봉투 미적용, `task-936 decision`) | `asOf === null`이므로 stale 배지 없이 "기준 시각 확인 불가" 문구만 (`DataFreshness.tsx:24-28`) | 없음(수동 갱신 버튼 없음; react-query가 `refetch` 제공하지만 UI 노출 안 함) | `구현됨(확인 불가 문구)` (`frontend/apps/web/src/components/DataFreshness.tsx:24-28`, `PortfolioPage.tsx:95`); `낡은 데이터 배지`는 `asOf`가 null이라 never 뜨므로 `없음` (`DataFreshness.tsx:34-37`) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-PortfolioPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `statusCode === 400` → `classifyBadRequest` 분기 (`PortfolioPage.tsx:31`) | `BadRequestNotice` 호출. errorCode별: `VALIDATION_INVALID_FIELD`/`VALIDATION_IDEMPOTENCY_KEY_REQUIRED` → `"입력값을 확인해주세요."` (`apiError.ts:92`) | 수정 후 재시도 | 서버 400 표시: `구현됨`. 클라이언트 입력 검증: `없음`(빈 값 필터링 `PortfolioPage.tsx:68,73`, 숫자 형식 검증 `PortfolioPage.tsx:157-158` 없음) |
| 인증 필요/세션 만료(401) | `statusCode === 401` → `classifyBadRequest` null, `classifyForbidden` null → `routeApiError` → `auth_required` (`errorRouting.ts:129`) → `ErrorMessage` (`PortfolioPage.tsx:35`) | `ErrorMessage` 호출, errorCode=`AUTH_TOKEN_EXPIRED`, message=`"세션이 만료되었습니다. 다시 로그인해주세요."` (`apiError.ts:61`) | 로그인 재시작 | 화면 전용 분기: `없음`. 전역 401 처리: `구현됨` (`useAuthStore.ts:49` `configureUnauthorizedHandler` → `logout()`) |
| 권한 없음/MFA 필요(403) | `statusCode === 403` → `classifyForbidden` 분기 (`PortfolioPage.tsx:32`) | `AUTH_MFA_REQUIRED`/`AUTH_TENANT_MISMATCH`/`policy` → `ForbiddenNotice`. 그 외 403 → `ErrorMessage` (`PortfolioPage.tsx:35`) | MFA 완료 / 권한 요청 / 수정 후 재시도 | 403 전용 분기: `부분` (`forbidden.ts:35-36` MFA/TENANT_MISMATCH 전용 처리, 그 외 `ErrorMessage` 폴백) |
| 충돌/상태 불일치(409) | `statusCode === 409` → `classifyBadRequest` null, `classifyForbidden` null → `routeApiError` → `ErrorMessage` (`PortfolioPage.tsx:35`) | `ErrorMessage` 호출. errorCode별: `INTEGRITY_IDEMPOTENCY_CONFLICT` (`apiError.ts:76`), `STATE_CONCURRENCY_CONFLICT` (`apiError.ts:74`) | 새로고침 후 재시도 | 화면 전용 분기: `없음`. 훅 수준에서 `INTEGRITY_IDEMPOTENCY_CONFLICT` 키 폐기 (`useIdempotentSubmit.ts:58-60`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-PortfolioPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 423(LOCKED)·429(RATE_LIMIT_EXCEEDED) | 화면 상단 `Alert tone="warning"` 배너. `RATE_LIMIT_ERROR_CODE` 번역 문구 + 카운트다운 + "다시 시도" 버튼. | `notice.retryNow` (skipWait) — 대기 건너뛰기. 자동 재시도도 있음 (`interruptibleSleep`). | `RateLimitNotice` 컴포넌트 (전역, `main.tsx:29` 마운트)가 `useRateLimitNotice()` 구독. `useRetryableAction.ts:132`에서 429 감지 시 notice 발행. |
| 서버 오류(5xx) | 503(RETRYABLE: EXCHANGE_UNAVAILABLE, DEPENDENCY_NOT_READY)·502(FATAL: EXCHANGE_FATAL)·500(FATAL: INTERNAL_ERROR) | retryable 5xx → `ErrorMessage` 컴포넌트에서 재시도 버튼 표시 (backoff 카운트다운). fatal 5xx → traceId 표시, 재시도 버튼 없음. | retryable만 backoff 재시도 (EXCHANGE_UNAVAILABLE, DEPENDENCY_NOT_READY). fatal은 재시도 불가. | `classifyServerError`(serverError.ts:39-52)가 `RETRYABLE_SERVER_ERROR_CODES`(503) vs `FATAL_SERVER_ERROR_CODES`(502/500) 분류. `classifyRetry`(retryable.ts:27-31)가 backoff/refetch/none 판정. `routeApiError`(errorRouting.ts:116-117)가 `backoff_retry`/`server_fatal` 라우팅. |
| 네트워크 단절 | `navigator.onLine === false` 또는 fetch 실패 | `Alert tone="warning"` 배너 (상단 고정). 번역 키 `legacy.offlineBanner.t1`. 재시도 버튼 없음. | 온라인 복귀 시 자동 재시도 아님 — 사용자가 화면을 다시 interactions. React Query의 retry 옵션으로 개별 쿼리가 재시도할 수 있으나 OfflineBanner 자체는 재시도 버튼 없음. | `OfflineBanner`(OfflineBanner.tsx:7-21)가 `useOnlineStatus()` 구독. `main.tsx:30`에서 루트에 마운트. `useOnlineStatus.ts:4-19`에서 `navigator.onLine` + 이벤트 리스너. |
| 부분 실패(일부 데이터만 실패) | positions 쿼리 성공/실패 독립, nav 쿼리 성공/실패 독립, 저널 쿼리 성공/실패 독립 | 일부 섹션만 에러 배너 표시 (예: positions는 성공했으나 nav만 실패 → nav 에러 배너만). 다른 섹션은 정상 데이터 표시. 에러 난 섹션만 `PositionsQueryError` 렌더 (404→NotFoundState, 403→ForbiddenNotice, 그 외→ErrorMessage). | 각 쿼리별 `refetch()` 배선 (onRetry). | `frontend/apps/web/src/routes/portfolio/PortfolioPositionsLive.tsx`에서 positions 쿼리 에러 (lines 91-100)와 nav 쿼리 에러 (lines 126-134)를 **독립적으로** 처리. 각 섹션이 `PositionsQueryError` 컴포넌트 렌더. `PositionJournalPanel.tsx:89`도 개별 에러 처리. |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-PortfolioPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:173`) — `handleRebalance` (`:65-83`) | 포트폴리오 로드 성공(`:110`), `allocations.length > 0`(`:123`, `:136-138`)일 때만 버튼 렌더링. 입력 칸(`:157-165`) 중 하나 이상이 공백 아닌 값이어야 요청 나감(`:67-73`). 버튼 `loading={rebalance.isPending}`(`:173`)은 요청 중 표시만 — `Button` 구현상 비활성화 여부는 미확인 | `submit`(`:75`) → `rebalance.mutateAsync`(`:76`) 성공 시 `setDrafts({})`(`:78`)로 입력 비움. `onSuccess`(`usePortfolio.ts:25`)에서 `["portfolio"]` 쿼리 무효화해 목록 갱신. `rebalance.data` 있으면 `legacy.portfolioPage.t12`(`:175-177`, "적용됨 {{adjusted}}건, 승인대기 {{pendingApproval}}건") 표시 | `DuplicateSubmitError`(연타): `:80` 조용히 무시. `ApiError`: `setError`(`:81`) → `RebalanceError`(`:30-42`, `:172`)가 400이면 `BadRequestNotice`, 403이면 `ForbiddenNotice`, 그 외 `ErrorMessage`. 그 외 오류: `t("legacy.portfolioPage.t14")`("재조정에 실패했습니다.", `:81`)로 설정된 `Error`를 `ErrorMessage` 표시. 입력값(`drafts`) 실패 시 유지(`:78`은 성공 경로에서만 실행). 키 처리: 4xx는 키 폐기(`useIdempotentSubmit.ts:17-19`, `:58-60`), 5xx·네트워크 오류는 키 유지(`:58-60`) | 이동 없음. `handleRebalance`에 navigate 호출 없고(`:65-83`), 라우트 `/portfolio`(`router.tsx:133`)에 머김 |
| 입력 없음(모든 입력 칸 공백) | `Object.entries(drafts).filter(...)` 결과 `adjustments.length === 0` (`:67-73`) | `return`(`:73`) — 요청 없이 함수 종료, 메시지 없음 | 없음 | 이동 없음 |
| 중복 제출(버튼 연타) | `inFlightRef.current === true` (`useIdempotentSubmit.ts:36-38`) → `DuplicateSubmitError` 던짐 | — | `catch`(`:79-80`)에서 `instanceof DuplicateSubmitError` 확인 후 `return` — 조용히 무시, setError 안 함 | 이동 없음 |
| 401 응답 | `ApiError` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:2`) `statusCode === 401` | — | classifyBadRequest/classifyForbidden 모두 null → routeApiError(`:33`) → ErrorMessage(`:35-40`) 표시. 전역 401 처리 `useAuthStore.ts:49`에서 logout() 호출. 로그아웃 후 이동 대상은 미확인(ProtectedRoute 가드, `router.tsx:70-72`) | 이동 대상 미확인 |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-PortfolioPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-PortfolioPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `Alert` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:5`) | UI primitive — i18n 키 없음(`frontend/packages/ui-web/src/Feedback.tsx:4-19`). `tone`으로 색상만 결정(danger=red, warning=yellow, success=green) | 호출부(BadRequestNotice/ForbiddenNotice/ErrorMessage)가 `<p>`로 문구를 전달. 재행동 없음 | 아니요 — Alert 자체는 trace_id를 알지 못함 |
| `classifyBadRequest` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:31`) | 순수 분류기 — i18n 키 없음(`frontend/packages/shared-types/src/badRequest.ts:34-43`). 400 응답을 `field`/`idempotency_key_required`/`disclosure_retired`/`mfa_invalid`/`unknown` 중 하나로 분류 | 호출부(BadRequestNotice)가 분류 결과에 따라 다른 UI 배너를 표시. 이 함수 자체는 UI를 렌더링하지 않음 | 아니요 — errorCode/statusCode만 검사하고 trace_id를 반환하지 않음 |
| `BadRequestNotice` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:31`) | `getApiErrorMessage(errorCode, message)` — shared-types/apiError.ts EXACT_MESSAGES 1:1 매핑. `disclosure_retired` 갈래: `t("legacy.badRequestNotice.t1")`(새로고침 버튼), `mfa_invalid` 갈래: `t("legacy.badRequestNotice.t2")`(MFA 재입력 버튼) (`frontend/apps/web/src/components/BadRequestNotice.tsx:62-78`) | `field` 갈래: details.fields가 비어있으면 배너 표시(인라인 자리 없음). `disclosure_retired`: "최신 revision 재조회" 버튼. `mfa_invalid`: "MFA 코드 재입력" 버튼. 나머지 갈래: 액션 버튼 없음(재시도 무의미) (`frontend/apps/web/src/components/BadRequestNotice.tsx:48-79`) | 아니요 — trace_id를 받지도 표시하지도 않음 (`frontend/apps/web/src/components/BadRequestNotice.tsx:40-81`) |
| `classifyForbidden` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:32`) | 순수 분류기 — i18n 키 없음(`frontend/packages/shared-types/src/forbidden.ts:31-41`). 403 응답을 `mfa_required`/`tenant_mismatch`/`policy`/`forbidden` 중 하나로 분류 | 호출부(ForbiddenNotice)가 분류 결과에 따라 다른 UI 표시. 이 함수 자체는 UI를 렌더링하지 않음 | 아니요 — errorCode/statusCode만 검사하고 trace_id를 반환하지 않음 |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:32`) | `getApiErrorMessage(errorCode, message)` — shared-types/apiError.ts EXACT_MESSAGES 1:1 매핑. `mfa_required` 갈래: `t("legacy.forbiddenNotice.t1")`(step-up 버튼) (`frontend/apps/web/src/components/ForbiddenNotice.tsx:36-49`) | `policy` 갈래: `DenialReasons` 컴포넌트로 상세 사유 표시. `mfa_required`: "step-up 인증" 버튼. 나머지 갈래: 액션 버튼 없음 | 아니요 — trace_id를 받지도 표시하지도 않음 (`frontend/apps/web/src/components/ForbiddenNotice.tsx:31-50`) |
| `routeApiError` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:33`) | 순수 라우터 — i18n 키 없음(`frontend/packages/shared-types/src/errorRouting.ts:84-133`). `RoutedApiError` 유니온 반환(field_errors/idempotency_new_key/idempotency_missing_header/disclosure_retired/mfa_invalid/mfa_required/tenant_mismatch/policy_denied/forbidden/account_locked/auth_required/not_found/refetch_retry/invalid_transition/data_coverage_missing/wallet_balance_drift/backoff_retry/server_fatal/unknown) | 호출부(RebalanceError/PortfolioPage)가 `kind`에 따라 BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로 분기. `server_fatal` 갈래만 `traceId` 필드 포함 | `server_fatal` 갈래의 `RoutedApiError`에 `traceId?: string` 필드가 있으나, 이 함수는 직접 노출하지 않음. 호출부(ErrorMessage)가 전달받아 표시 |
| `ErrorMessage` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:35`) | `getApiErrorMessage(errorCode, message)` — shared-types/apiError.ts EXACT_MESSAGES 1:1 매핑. trace_id: `t("legacy.errorMessage.t1", { traceId })`. 재시도 카운트다운: `t("legacy.errorMessage.t2", { remainingSec })`. 재시도 버튼: `t("legacy.errorMessage.t3")` (`frontend/apps/web/src/components/ErrorMessage.tsx:36-71`) | `classifyRetry` 결과가 "none"이 아니면 재시도 버튼 표시. `backoff`(RATE_LIMIT_EXCEEDED 등): retryAfterSec 카운트다운 후 버튼 활성화. `refetch`(STATE_CONCURRENCY_CONFLICT): 즉시 활성화. fieldErrors가 있으면 배너 숨김(중복 안내 금지) | 예 — `traceId`가 있으면 `t("legacy.errorMessage.t1", { traceId })`로 하위 텍스트 노출 (`frontend/apps/web/src/components/ErrorMessage.tsx:60`). RebalanceError(`:38`)에서 `error instanceof ApiError ? error.traceId : undefined`로 전달 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-PortfolioPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-PortfolioPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-PortfolioPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
