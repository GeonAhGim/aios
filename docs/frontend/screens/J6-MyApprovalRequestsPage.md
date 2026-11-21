# J6-MyApprovalRequestsPage — `/approval-requests`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MyApprovalRequestsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/approval-requests` / `frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-MyApprovalRequestsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P7(기관) — 거버넌스·승인 체인, 승인 요청 처리 [확인: PERSONAS_AND_JOURNEYS.md §2 표 P7, `approvals` 라우트] |
| 목적(한 문장) | 사용자가 직접 생성한 FD-10.1(SOLO/DUAL) 승인 요청을 조회하고, 강제 대기시간 이후 본인이 직접 승인 또는 거절한다. [확인: MyApprovalRequestsPage.tsx:15-17 주석, t4 문구] |
| 진입 조건 | `router.tsx:147` — `/approval-requests` 라우트. `protect()` 래퍼가 `ProtectedRoute` 가드 적용. `ProtectedRoute.tsx:20-21`: 미로그인(`!token`) 시 `/login?next=...` 리다이렉트. `ProtectedRoute.tsx:32-33`: MFA 미설정(`!me.mfaEnabled`) 시 `/onboarding/mfa-setup` 리다이렉트. `ProtectedRoute.tsx:35-41`: 위험 프로필 미설정(`!riskProfile`) 시 `/onboarding/risk-assessment` 리다이렉트. [확인] |
| 사용자 준비물 | 로그인 세션, MFA 활성화, 위험 프로필 설정 [확인: ProtectedRoute.tsx 조건 순서] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-MyApprovalRequestsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useApproveMyRequest` | `frontend/packages/shared-hooks/src/useApprovals.ts:12` | `useMutation` 기반 trigger(OnClick). `onSuccess`에서 `qc.invalidateQueries({ queryKey: ["myApprovalRequests"] })`로 목록 자동 갱신(MyApprovalRequestsPage.tsx:46, 60). `mutateAsync` 사용으로 동기 처리(MyApprovalRequestsPage.tsx:60). | `handle` 함수(MyApprovalRequestsPage.tsx:57-65)에서 `try/catch` 후 `error` state에 저장. `RequestActionError` 컴포넌트(MyApprovalRequestsPage.tsx:79)가 `classifyForbidden`/`routeApiError`로 분류해 `ForbiddenNotice`(403) 또는 `ErrorMessage`(기타) 표시. 409(STATE_INVALID_TRANSITION)는 재시도 버튼 없음. |
| `useRejectMyRequest` | `frontend/packages/shared-hooks/src/useApprovals.ts:20` | `useMutation` 기반 trigger(OnClick). `onSuccess`에서 `qc.invalidateQueries({ queryKey: ["myApprovalRequests"] })`로 목록 자동 갱신(MyApprovalRequestsPage.tsx:47, 61). `mutateAsync` 사용으로 동기 처리(MyApprovalRequestsPage.tsx:61). | approve와 동일 처리(MyApprovalRequestsPage.tsx:62). `error` state 저장 → `RequestActionError` 렌더링. |
| `useMyApprovalRequests` | `frontend/packages/shared-hooks/src/useApprovals.ts:5` | `useQuery` 기반 1회 조회(on mount). `queryKey: ["myApprovalRequests"]`, `queryFn: apiClient.listMyApprovalRequests()`(useApprovals.ts:6-9). 폴링/자동 재조회 옵션 미설정 → mount 시 1회만 조회. 수동 `refetch` 함수 expose(MyApprovalRequestsPage.tsx:104). | `isError`일 때(MyApprovalRequestsPage.tsx:112-113): `RequestActionError`에 `error`와 `onRetry={() => refetch()}` 전달. `refetch`는 목록 재조회. 403이면 `ForbiddenNotice`, 그 외는 `ErrorMessage` 표시. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-MyApprovalRequestsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `useMyApprovalRequests`의 `isLoading === true` (마운트 시 API 호출 중) | `LoadingState` 컴포넌트 렌더링 (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:114-115`) | 없음(비활성). 버튼도 `loading` 상태. | 구현됨 (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:114-115`: `{isLoading ? <LoadingState /> : ...}`) |
| 빈 상태 | `requests` 배열이 있고 `length === 0` (조회 성공 but 데이터 없음) | `EmptyState` 컴포넌트에 `t("legacy.myApprovalRequestsPage.t5")` ("대기 중인 승인 요청이 없습니다.") 표시 (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:122-123`) | 없음. 승인 요청이 없으므로 조작 불가. | 구현됨 (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:122-123`: `: <EmptyState>{t("legacy.myApprovalRequestsPage.t5")}</EmptyState>`) |
| 정상 | `requests` 배열이 있고 `length > 0` | 각 항목을 `RequestCard`로 렌더링 (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:116-121`). 각 카드는 요청 액션명, scope/모드 뱃지, 생성일시, 1차 서명 완료 여부, 승인/거절 버튼, 타이머 카운트다운 표시. | 승인 버튼(기다시간 경과 후 활성화, `remaining > 0` 시 disabled) 또는 거절 버튼 클릭. 각 버튼은 `useApproveMyRequest`/`useRejectMyRequest` mutation trigger. 성공 시 목록 자동 갱신. 실패 시 `RequestActionError` 표시. | 구현됨 (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:44-100`: `RequestCard` 컴포넌트, `handle` 함수, 승인/거절 버튼, `useApproveMyRequest`/`useRejectMyRequest` mutation 바인딩) |
| 낡은 데이터(신선도 초과) | `useMyApprovalRequests`는 폴링/자동 재조회 미설정 (`frontend/packages/shared-hooks/src/useApprovals.ts:5-9`: `queryFn` 1회 조회, 폴링 옵션 없음) | 별도 UI 없음. 마운트 시 1회만 조회하므로 페이지 체류 중 백엔드 상태 변경 시 stale 데이터 표시 가능. | `refetch` 함수 expose됨 (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:104`) but 사용자 노출 refetch 버튼 없음. `isError` 시에만 `onRetry={() => refetch()}`. | 없음 (신선도 모니터링 또는 stale-time 갱신 로직 없음. `useQuery` 기본 `staleTime: 0`이므로 다음 render 시 이미 stale로 간주되나 UI 피드백 없음) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-MyApprovalRequestsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `VALIDATION_*` 응답(approve/reject 액션) | `ErrorMessage` 컴포넌트(MyApprovalRequestsPage.tsx:29)에 `routeApiError` 결과(kind: `field_errors`, `idempotency_*`, `mfa_invalid`) 전달. `errorCode`는 `ErrorMessage.tsx:36`의 `getApiErrorMessage`로 매핑(apiError.ts:56-58). 필드별 인라인 표시 없음(MyApprovalRequestsPage.tsx:1-13 import에 `classifyBadRequest`/폼 컴포넌트 없음). | 재시도 버튼: `MyApprovalRequestsPage.tsx:27`에서 `routed.kind === "refetch_retry" || routed.kind === "backoff_retry"`일 때만 `onRetry` 전달. `field_errors`/`idempotency_*`/`mfa_invalid`는 재시도 없음. | 구현됨(일반 경로). `errorRouting.test.ts:23-29`: `VALIDATION_INVALID_FIELD` → `field_errors`, `VALIDATION_IDEMPOTENCY_KEY_REQUIRED` → `idempotency_missing_header`, `VALIDATION_DISCLOSURE_RETIRED` → `disclosure_retired`. 필드별 인라인 표시는 구현되지 않음. |
| 인증 필요/세션 만료(401) | `AUTH_*` 코드(AUTH_REQUIRED, AUTH_INVALID_CREDENTIALS, AUTH_TOKEN_EXPIRED, AUTH_TOKEN_INVALID, AUTH_SESSION_REVOKED) | `RequestActionError`가 `classifyForbidden`에 false(MyApprovalRequestsPage.tsx:25: statusCode≠403) → `ErrorMessage`로 감김(MyApprovalRequestsPage.tsx:29). 문구는 `apiError.ts:59-63` 매핑(예: `AUTH_TOKEN_EXPIRED` → `"세션이 만료되었습니다. 다시 로그인해주세요."`). 전용 '세션 만료' 안내 없음. 재시도 버튼 없음(MyApprovalRequestsPage.tsx:27: `auth_required`는 refetch/backoff 아님). | api-client 전역 401 처리(httpHeaders.ts:53-75: `configureUnauthorizedHandler` + `notifyUnauthorized`). http.ts:104-106에서 `AUTH_TOKEN_EXPIRED`는 refresh 후 재시도, 그 외 AUTH_*는 `notifyUnauthorized`로 핸들러 호출(로그인 리다이렉트). ProtectedRoute.tsx:20-21: 토큰 없으면 `/login?next=...` 리다이렉트. 화면 체류 중 401은 ProtectedRoute에서 처리 안 함(토큰은 있음). | 구현됨(전역 처리). `errorRouting.test.ts:30-34`: 모든 AUTH_* → `auth_required`. api-client 전역 401 처리 확인(httpHeaders.ts:57-74, http.ts:104-117). |
| 권한 없음/MFA 필요(403) | `AUTHZ_FORBIDDEN`, `AUTHZ_ZONE_VIOLATION`, `POLICY_*`, `RISK_*`, `AUTH_MFA_REQUIRED`, `AUTH_TENANT_MISMATCH` | `classifyForbidden` true(MyApprovalRequestsPage.tsx:25: statusCode===403) → `ForbiddenNotice` 렌더링. 문구는 `ForbiddenNotice.tsx:36`의 `getApiErrorMessage` 매핑(apiError.ts:65-69). `ForbiddenNotice.tsx:41`: policy 갈래면 `DenialReasons` 추가 표시. `ForbiddenNotice.tsx:42-46`: mfa_required 갈래면 `onStepUp` 버튼(t("legacy.forbiddenNotice.t1")). | `mfa_required`일 때만 `onStepUp` 버튼(ForbiddenNotice.tsx:42). 나머지는 재시도 버튼 없음(ForbiddenNotice.tsx: 재시도 로직 없음). | 구현됨. `errorRouting.test.ts:36-45`: 403 코드별 갈래 매핑 확인. `forbidden.ts:31-41`: classifyForbidden 판정 조건(statusCode===403, errorCode별 4갈래). |
| 충돌/상태 불일치(409) | `STATE_INVALID_TRANSITION`, `STATE_CONCURRENCY_CONFLICT`, `INTEGRITY_IDEMPOTENCY_CONFLICT`, `DATA_COVERAGE_MISSING`, `INTEGRITY_WALLET_BALANCE_DRIFT` | `ErrorMessage`에 `routeApiError` 결과 전달. `STATE_INVALID_TRANSITION` → kind `invalid_transition`(errorRouting.ts:113, apiError.ts:75: `"현재 상태에서는 수행할 수 없는 작업입니다."`). `STATE_CONCURRENCY_CONFLICT` → kind `refetch_retry`(errorRouting.ts:112, apiError.ts:74: `"다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요."`). `INTEGRITY_IDEMPOTENCY_CONFLICT` → kind `idempotency_new_key`(errorRouting.ts:100). | 재시도 버튼: `STATE_CONCURRENCY_CONFLICT`는 `refetch_retry`이므로 `MyApprovalRequestsPage.tsx:27`에서 `canRetry=true`, onRetry 전달. `STATE_INVALID_TRANSITION`은 재시도 버튼 없음(MyApprovalRequestsPage.tsx:22-23 주석: "자동 재시도 대상 아님"). 목록 미갱신: catch 블록(MyApprovalRequestsPage.tsx:62-64)에서 invalidateQueries 호출 없음. 성공 시(onSuccess)에만 목록 갱신(useApprovals.ts:12, 20). 카드 버튼은 그대로 활성 상태(상태 리플래시 안 됨). | 구현됨(일반 경로). `errorRouting.test.ts:47-52`: 409 코드별 갈래 확인. `MyApprovalRequestsPage.tsx:22-23`: 409 재시도 버튼 제외 주석. 목록 미갱신은 catch 블록(MyApprovalRequestsPage.tsx:62-64)에 invalidate 없음으로 확인. |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-MyApprovalRequestsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 423(계정 잠금) 또는 429(속도 제한) 응답 시, `RequestActionError`(MyApprovalRequestsPage.tsx:24-36)가 `classifyForbidden` false → `routeApiError` 분류 → `ErrorMessage`에 `retryAfterSec` 전달. | `ErrorMessage` 컴포넌트에 `errorCode`/`message`/`retryAfterSec` 전달(MyApprovalRequestsPage.tsx:30-34). `retryAfterSec` 기반 카운트다운 표시. | `ErrorMessage`가 `retryAfterSec` 기반 재시도 버튼 노출. | 구현됨 (MyApprovalRequestsPage.tsx:24-36: `RequestActionError` → `ErrorMessage`에 `retryAfterSec` 전달) |
| 서버 오류(5xx) | 502/503/500 응답 시, `RequestActionError`(MyApprovalRequestsPage.tsx:24-36)가 `classifyForbidden` false → `routeApiError` 분류 → `ErrorMessage`에 `traceId` 또는 `retryAfterSec` 전달. | `ErrorMessage`에 `traceId`(fatal 시, MyApprovalRequestsPage.tsx:32) 또는 `retryAfterSec`(backoff 시, MyApprovalRequestsPage.tsx:33) 전달. | fatal: trace_id 노출 + 재시도 없음. backoff: `retryAfterSec` 기반 백오프 카운트다운 + 재시도 버튼. | 구현됨 (MyApprovalRequestsPage.tsx:24-36: `routeApiError` 결과에 따라 `traceId`/`retryAfterSec` 전달) |
| 네트워크 단절 | React Query(`useQuery`)가 네트워크 오류 감지 → `isError === true`. `useMyApprovalRequests`(MyApprovalRequestsPage.tsx:104). | `isError` 분기(MyApprovalRequestsPage.tsx:112-113): `<RequestActionError error={error} onRetry={() => refetch()} />`. `ErrorMessage`가 `onRetry`로 목록 재조회 가능. | `onRetry={() => refetch()}` 클릭 시 `apiClient.listMyApprovalRequests()` 재호출(useApprovals.ts:8). | 구현됨 (MyApprovalRequestsPage.tsx:112-113: `isError` → `RequestActionError` + `onRetry={() => refetch()}`) |
| 부분 실패(일부 데이터만 실패) | 승인/거부 mutation(`useApproveMyRequest`/`useRejectMyRequest`)이 단일 요청 ID에서만 실패. 전체 목록 조회는 성공. | 각 `RequestCard`가 자체 `error` state(MyApprovalRequestsPage.tsx:49)를 갖고, 실패 시 해당 카드 내부에 `<RequestActionError>` 렌더링(MyApprovalRequestsPage.tsx:79). 다른 카드는 정상 유지. | 승인/거절 버튼 클릭 시 `handle` 함수(MyApprovalRequestsPage.tsx:57-65)가 `try/catch` → `setError`. 성공 시 `onSuccess` 훅(useApprovals.ts:16,24)이 목록 자동 갱신. | 구현됨 (MyApprovalRequestsPage.tsx:49: `useState<unknown>(null)`, 57-65: `handle`, 79: `{error !== null && <RequestActionError error={error} />}`) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-MyApprovalRequestsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:81`) | `remaining > 0` 시 disabled(대기시간 경과 전). `approve.isPending` 시 loading. 대기시간: `waitRemainingSeconds(request)` — `createdAt + mandatoryWaitSeconds * 1000 - Date.now()`(MyApprovalRequestsPage.tsx:39-42). 타이머: `setInterval` 1초 간격으로 `remaining` 갱신(MyApprovalRequestsPage.tsx:51-55). | `handle("approve")` 호출(MyApprovalRequestsPage.tsx:86) → `approve.mutateAsync(request.id)`(useApprovals.ts:12 mutation). `onSuccess` 훅(useApprovals.ts:16): `qc.invalidateQueries({ queryKey: ["myApprovalRequests"] })`로 목록 자동 갱신. | `catch`(MyApprovalRequestsPage.tsx:62-64): `setError(err)`. `RequestActionError` 렌더링(MyApprovalRequestsPage.tsx:79). `classifyForbidden(error)` false → `routeApiError(error)` 분류 → `ErrorMessage` 표시(MyApprovalRequestsPage.tsx:26-35). | 동일 화면 (목록 자동 갱신) |
| `<Button>` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:90`) | disabled 없음(거절은 항상 활성화). `reject.isPending` 시 loading(MyApprovalRequestsPage.tsx:93). | `handle("reject")` 호출(MyApprovalRequestsPage.tsx:94) → `reject.mutateAsync(request.id)`(useApprovals.ts:20 mutation). `onSuccess` 훅(useApprovals.ts:24): `qc.invalidateQueries({ queryKey: ["myApprovalRequests"] })`로 목록 자동 갱신. | 승인 버튼과 동일 처리(MyApprovalRequestsPage.tsx:62-64). `error` state 저장 → `RequestActionError` 렌더링(MyApprovalRequestsPage.tsx:79). | 동일 화면 (목록 자동 갱신) |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-MyApprovalRequestsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음. 이 화면은 승인/거절 요청의 상태 전환만 처리하며, 금액(amount/price/fee)을 표시하거나 연산하지 않는다. `ApprovalRequest` 타입 필드(`requestedAction`, `scope`, `approvalMode`, `createdAt`, `mandatoryWaitSeconds`, `firstApproverId`)에 금액 관련 필드 없음(shared-types의 ApprovalRequest 타입 참조). 금액 조작은 FD-10.1 요청 생성 시점(별도 화면)에서 이미 완료됨. |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-MyApprovalRequestsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:25`, `frontend/apps/web/src/components/ForbiddenNotice.tsx:19`) | 403 응답을 네 갈래(mfa_required/tenant_mismatch/policy/forbidden)로 분류. `shared-types/forbidden.ts:31-41`의 순수 함수. statusCode===403이고 errorCode별 갈래 매핑. | `RequestActionError`가 `<ForbiddenNotice>` 렌더링. 403이 아니면 null 반환 → `ErrorMessage`로 폴백. | trace_id 노출 없음. `ForbiddenNotice` 내부에서 `extractErrorCode`(ForbiddenNotice.tsx:19)로 errorCode만 추출(20-22줄). |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:25`) | 403 응답을 `Alert tone="danger"` 배너로 표시. `ForbiddenNotice.tsx:31-49`. policy 갈래면 `DenialReasons` 추가 표시(41줄). mfa_required일 때 `onStepUp` 버튼(t("legacy.forbiddenNotice.t1")) 표시(42-46줄). | 문구는 `getApiErrorMessage`로 shared-types에서 조회(apiError.ts EXACT_MESSAGES). `Alert` 컴포넌트 렌더링. | trace_id 노출 없음. |
| `routeApiError` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:26`) | `shared-types/errorRouting.ts:84-133`의 단일 진입점. 10단계 우선순위(idempotency→forbidden→stateConflict→data_coverage→wallet_drift→serverError→retry→notFound→lockout→sessionExpired→unknown)로 error_code를 18개 갈래 중 하나로 분류. | `RoutedApiError` 반환(kind + 부가 데이터). 호출부(RequestActionError)가 kind별로 `ErrorMessage` props 구성. | trace_id 노출: `server_fatal` 갈래일 때 `traceId` 전달(MyApprovalRequestsPage.tsx:32). |
| `ErrorMessage` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:29`) | `ErrorMessage.tsx:27-72`. `getApiErrorMessage`로 문구 조회. `classifyRetry`로 재시도 가능 여부 판정(kind: refetch/backoff/none). backoff일 때 `retryAfterSec` 카운트다운(49-53줄). fieldErrors 있으면 렌더링 생략(55줄). | `Alert tone="danger"` 배너. traceId 있으면 t("legacy.errorMessage.t1")로 노출. 재시도 가능하면 버튼 + 카운트다운. | trace_id: `ErrorMessage.tsx:60`에서 `{traceId && <p>...t("legacy.errorMessage.t1", { traceId })</p>}`. backoff_retry일 때 `retryAfterSec` 전달(MyApprovalRequestsPage.tsx:33). |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-MyApprovalRequestsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | `Button` 요소 사용(81, 90줄) — `type="button"` 명시(82, 91줄). `loading` prop으로 비활성화 상태 표시. 승인 버튼은 `remaining > 0` 시 disabled(84줄). `aria-label`/`aria-disabled` 명시 없음(미확인). `PageHeader` 컴포넌트가 `<h1>` 시맨틱 태그 제공 여부 미확인. `RequestCard`의 `<li>`가 `<ul>` 내的正确 위치(117-120줄). 키보드 네비게이션 테스트 없음. | `aria-label` 누락. `aria-live` 영역으로 새로고침 알림 없음. |
| i18n/지역화 | 5개 i18n 키 사용: `legacy.myApprovalRequestsPage.t1`(요청 ID), `t2`(거절), `t3`(title3=페이지 제목), `t4`(설명 문구), `t5`(빈 상태), `t6`(실패 문구). Facts 시트 확인. 모든 하드코딩 문자열은 i18n 키로 감싸짐(MyApprovalRequestsPage.tsx:76, 88, 96, 109, 111, 123). 한국어/영어 이중 제공(facts 시트 확인). | `remaining` 카운트다운 텍스트(MyApprovalRequestsPage.tsx:88: `` `승인 (${remaining}초 대기)` ``) — i18n 키가 아닌 템플릿 문자열. 초 단위 단위명("초")이 하드코딩됨. |
| 성능 예산 | 데이터 조회: `useMyApprovalRequests`는 mount 시 1회 조회(폴링 없음). 승인/거부 mutation은 `mutateAsync`로 동기 처리. `onSuccess`에서 `invalidateQueries`로 목록 자동 갱신. 타이머: 각 `RequestCard`가 `setInterval`(1초 간격)으로 `remaining` 갱신(51-55줄). unmount 시 `clearInterval`로 정리. `requests.length`개의 카드가 각각 독립 타이머 보유 — N개 카드 시 N개의 interval 동시 실행. | 카드 수가 많을 때 타이머 오버헤드. 폴링/optimistic update 없음(보통 경로). |
| 텔레메트리 | 페이지 진입/나선 추적, 승인/거절 버튼 클릭 횟수, 에러 발생 횟수 등 텔레메트리 추적 코드 없음(MyApprovalRequestsPage.tsx 전체 스캔). `Observability` 컴포넌트(import 없음). React Query의 `onSuccess`/`onError` 콜백에 로깅 없음. | 텔레메트리 미구현. |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-MyApprovalRequestsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | `/approval-requests` 라우트로 접근 시 로그인 세션이 없으면 `/login?next=/approval-requests`로 리다이렉트된다. | `ProtectedRoute.tsx:20-21`의 `!token` 분기 + router 테스트 |
| A2 | 승인/거절 버튼 클릭 시 mutation이 호출되고, 성공 시 목록이 `invalidateQueries`로 자동 갱신되며, 실패 시 `RequestActionError`가 해당 카드 내부에 렌더링된다. | `MyApprovalRequestsPage.test.tsx`의 mutation trigger + error surface 테스트 |
| A3 | 강제 대기시간(`mandatoryWaitSeconds`) 경과 전에는 승인 버튼이 비활성화(disabled)되고 카운트다운 텍스트를 표시하며, 경과 후 활성화된다. | `waitRemainingSeconds` 함수(MyApprovalRequestsPage.tsx:39-42) + 타이머 갱신 로직(MyApprovalRequestsPage.tsx:51-55) 검증 |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-MyApprovalRequestsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | 승인/거절 후 목록의 해당 항목이 자동으로 제거되거나 상태 업데이트되어야 함 | mutation `onSuccess` 훅(useApprovals.ts:16,24)이 `invalidateQueries`로 전체 목록을 갱신하지만, 해당 항목의 상태 변경을 직접 처리하지 않음 — 백엔드가 승인/거절 후 해당 요청을 목록에서 제외하는지 여부에 의존 | 하 | 기존 구현으로 충분(백엔드 정책 확인 필요) |
| G2 | 대기 시간 카운트다운 텍스트가 i18n으로 제공되어야 함 | `remaining` 카운트다운(`MyApprovalRequestsPage.tsx:88`: `` `승인 (${remaining}초 대기)` ``)이 i18n 키가 아닌 템플릿 문자열. "초" 단위명이 하드코딩됨 | 중 | i18n 키 추출 필요 (별도 리프) |
| G3 | `RequestCard` 내 `setInterval` 타이머가 unmount 시 정리되어야 함 | `useEffect` cleanup에 `clearInterval(timer)` 포함(MyApprovalRequestsPage.tsx:54). N개 카드 동시 렌더 시 N개 interval 생성·정리. | 하 | 구현됨 |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
