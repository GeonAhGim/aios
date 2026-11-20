# J5-PayoutsPage — `/wallet/payouts`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/PayoutsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/wallet/payouts` / `frontend/apps/web/src/routes/wallet/PayoutsPage.tsx`
- 여정 소속: J5


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J5-PayoutsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | **P3 전략 판매자** — 전략을 마켓에 상장해 수익을 내고 정산을 받는 역할 (§2 P3, "전략을 마켓에 상장, 가격·구독 조건, 정산·분쟁") |
| 목적(한 문장) | 보류(홀드) 중인 정산 금액과 정산 배치 목록을 확인하고, 브레이크글래스 그랜트 ID로 정산 배치를 지급 확정한다 |
| 진입 조건 | `frontend/apps/web/src/router.tsx:143` — `/wallet/payouts` 라우트, `protect()` 게이트(`ProtectedRoute`) 통과 = 로그인 세션 필요 |
| 사용자 준비물 | 로그인 세션, 계좌 코드(accountcode), 정산 배치 목록 조회 권한 |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J5-PayoutsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useCursorPage` | `frontend/apps/web/src/hooks/useCursorPage.ts:25` | 네트워크 없음 — 상태 훅. 이전/다음 클릭 시 `cursor` 변경(`useCursorPage.ts:32-42`), 그 커서로 `useQuery`가 재조회(`PayoutsPage.tsx:208-209`) | 해당 없음(상태 훅, 오류 경로 없음) |
| `useQuery`(홀드/정산 배치 목록) | `PayoutsPage.tsx:207-210` (`CursorListSection` 내부) | 1회 조회. 커서별 키 `[testId, cursorPage.cursor]`(`PayoutsPage.tsx:208`). refetchInterval 없음. 기본 `QueryClient`는 `main.tsx:15`의 `new QueryClient()`(옵션 없음)이므로 전역 재시도/포커스 재조회는 라이브러리 기본값 | 기본 fetcher는 항상 reject(`PayoutsPage.tsx:37-41`)이므로 현재 빌드에서는 항상 오류 경로. 오류는 `query.isError`일 때 `ErrorMessage`(`PayoutsPage.tsx:227-234`)로 전달됨. 재시도는 `routeApiError`가 `refetch_retry`/`backoff_retry`일 때만 `query.refetch`(`PayoutsPage.tsx:216-233`) |
| `useMutation`(정산 확정 `markPaid`) | `PayoutsPage.tsx:119-125` (`PayoutBatchCard` 내부) | 변경 요청 — 사용자 클릭 시 `markPayoutPaid` 1회 호출(`PayoutsPage.tsx:170`). 성공 시 `['payoutBatches']` 무효화(`PayoutsPage.tsx:124`) → 목록 재조회 | `MarkPaidActionError`(`PayoutsPage.tsx:96-109`): 403이면 `ForbiddenNotice`, 그 외는 `ErrorMessage`. 재시도 버튼은 `classifyServerError`가 `retryable`일 때만 표시(`PayoutsPage.tsx:106`) |
| 기본 구현 `markPayoutPaidDefault` → `apiClient.markPayoutPaid` | `PayoutsPage.tsx:54-55` | 서버 라우트 `POST /admin/ledger/payouts/{batch_id}/paid`(`PayoutsPage.tsx:43-45` 주석 인용) | `MarkPaidActionError` 경로(`PayoutsPage.tsx:96-109`) |
| `fetchHoldsPageDefault`, `fetchPayoutBatchesPageDefault` (목록 GET 두 개) | `PayoutsPage.tsx:37-41` | 서버 미구현. 두 함수 모두 `Promise.reject(new Error("...아직 제공되지 않습니다."))`(`PayoutsPage.tsx:38,41`). 주석: `PayoutsPage.tsx:21-24` | 항상 reject → `ErrorMessage`(`PayoutsPage.tsx:227-234`) |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J5-PayoutsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `query.isLoading === true` (`PayoutsPage.tsx:235`) | `<LoadingState />` 컴포넌트 표시 (`PayoutsPage.tsx:236`) | 없음 — 데이터 조회 대기 중 | 구현됨 (`PayoutsPage.tsx:235-236`) |
| 빈 상태 | `items.length === 0`且`!query.isLoading`且`!query.isError` (`PayoutsPage.tsx:237`) | `<EmptyState>`에 섹션별 빈 문구 — 홀드: "보류 중인 홀드가 없습니다." (`PayoutsPage.tsx:279`), 정산 배치: "정산 배치가 없습니다." (`PayoutsPage.tsx:288`) | 없음 — 표시할 데이터 없음 | 구현됨 (`PayoutsPage.tsx:237-238`) |
| 정상 | `items.length > 0`且`!query.isLoading`且`!query.isError` (`PayoutsPage.tsx:239`) | 각 항목을 `HoldCard`(`PayoutsPage.tsx:75-88`) 또는 `PayoutBatchCard`(`PayoutsPage.tsx:132-182`)로 렌더링. 페이지네이션 버튼 보임 (`PayoutsPage.tsx:243-250`) | 정산 배치 RELEASED 상태 시 외부 참조번호·브레이크글래스 그랜트 ID 입력 후 "정산 확정" 버튼 클릭 (`PayoutsPage.tsx:147-172`). 이전/다음 페이지네이션 (`PayoutsPage.tsx:245-248`) | 구현됨 (`PayoutsPage.tsx:239-240, 243-250`) |
| 낡은 데이터(신선도 초과) | `deriveFreshness(asOf, now).isStale === true` (`DataFreshness.tsx:22,34`). 기본 `staleAfterSec=300` (`DataFreshness.tsx:20`) | `DataFreshness` 컴포넌트가 "N분 전" 문구 (`DataFreshness.tsx:33`) + `isStale`일 때 "데이터가 오래되었습니다." 경고 배지 (`DataFreshness.tsx:35-36`) 표시 | 없음 — 표시 전용. 명시적 갱신 버튼 없음(에러 경로에서만 refetch) | 구현됨 (`DataFreshness.tsx:20-40`, `PayoutsPage.tsx:224,280,289`) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J5-PayoutsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `markPaid.isError`且에러가 400(VALIDATION_*) (`PayoutsPage.tsx:175`) | `MarkPaidActionError`(`PayoutsPage.tsx:96-109`) → `classifyForbidden`(`:97`) null → `routeApiError`(`:98`) `{kind:"field_errors"}` 또는 `{kind:"idempotency_missing_header"}` → `ErrorMessage`(`:101-107`)에 errorCode/message/traceId 전달. apiError.ts(`shared-types/src/apiError.ts:106`)의 에러 문구 매핑 함수로 사용자 문구 생성 | 재시도 버튼 없음(서버 오류 분류기가 해당 코드 범위에 없음) (`PayoutsPage.tsx:106`) | 구현됨 (`PayoutsPage.tsx:96-109, 175`) |
| 인증 필요/세션 만료(401) | `markPaid.isError`且에러가 401 (`PayoutsPage.tsx:175`) | `MarkPaidActionError` → `classifyForbidden` null → `routeApiError`(`:98`)에서 세션 만료 코드 검사(`errorRouting.ts:128-129`) → `{kind:"auth_required"}` → `ErrorMessage`(`:101-107`)에 errorCode/message/traceId 전달. 세션 만료 문구 | 재시도 버튼 없음(`auth_required`는 서버 오류 분류기 대상 아님) (`PayoutsPage.tsx:106`) | 구현됨 (`PayoutsPage.tsx:96-109, 175`) |
| 권한 없음/MFA 필요(403) | `markPaid.isError`且에러의 `statusCode === 403` (`PayoutsPage.tsx:175`) | `MarkPaidActionError` → `classifyForbidden`(`:97`)(`forbidden.ts:31-41`)가 `mfa_required`/`tenant_mismatch`/`policy`/`forbidden` 중 하나 반환 → `ForbiddenNotice`(`:97`)(`ForbiddenNotice.tsx:31-50`)가 apiError.ts(`shared-types/src/apiError.ts:106`)의 매핑 함수로 문구 표시. `mfa_required`일 때 `onStepUp` 버튼(`ForbiddenNotice.tsx:42-46`), `policy`일 때 `DenialReasons`(`ForbiddenNotice.tsx:41`) | `mfa_required`일 때 `onStepUp` 버튼(현재 `PayoutsPage.tsx:175`에서 `onStepUp` 전달 안 함 → 버튼 숨김) | 구현됨 (`PayoutsPage.tsx:96-109, 175`, `ForbiddenNotice.tsx:31-50`) |
| 충돌/상태 불일치(409) | `markPaid.isError`且에러의 `statusCode === 409` (`PayoutsPage.tsx:175`) | `MarkPaidActionError` → `classifyForbidden` null → `routeApiError`(`:98`)에서 상태 충돌 분류기(`stateConflict.ts:33`) → `{kind:"invalid_transition"}` 또는 `{kind:"refetch_retry"}`(STATE_CONCURRENCY_CONFLICT) → `ErrorMessage`(`:101-107`)에 errorCode/message/traceId 전달 | `routeApiError`가 `backoff_retry`일 때 `retryAfterSec` 설정(`:105`). 그러나 서버 오류 분류기(`:99`)가 409를 대상 밖(5xx만)으로 판단하므로 `onRetry` 미설정(`:106`) — 재시도 버튼 없음 | 구현됨 (`PayoutsPage.tsx:96-109, 175`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J5-PayoutsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | `markPaid.isError`且에러의 statusCode 423且errorCode "AUTH_ACCOUNT_LOCKED" (`PayoutsPage.tsx:175`) 또는 errorCode "RATE_LIMIT_EXCEEDED" (`PayoutsPage.tsx:175`) | 423: `MarkPaidActionError` → `classifyForbidden` null → `routeApiError`(`:98`)에서 `deriveLockout`(`accountLockout.ts:41-50`)가 statusCode=423且errorCode="AUTH_ACCOUNT_LOCKED" 검출 → `{kind:"account_locked", retryAfterSec}` → `ErrorMessage`(`:101-107`)에 errorCode=message 전달. `retryAfterSec` 미전달(`routed.kind !== "backoff_retry"`) → 카운트다운 없음. 429: `routeApiError`에서 `classifyRetry`(`retryable.ts:45-50`)가 `RATE_LIMIT_EXCEEDED` 검출 → `{kind:"backoff", afterSec}` → `routeApiError`가 `{kind:"backoff_retry", afterSec}` 반환 → `ErrorMessage`에 `retryAfterSec` 전달(`:105`). ErrorMessage가 `classifyRetry`로 `kind==="backoff"` 검출 → 카운트다운 후 재시도 버튼 활성화 | 423: 재시도 버튼 없음(`account_locked`는 `backoff_retry` 아님). 서버가 준 `retryAfterSec`을 `accountLockout.ts:31-36`이 `DEFAULT_ACCOUNT_LOCKOUT_SEC=60`으로 클램프. 429: `ErrorMessage`에 `retryAfterSec`은 전달되나 `onRetry`는 미전달(`classifyServerError`가 429 대상 밖 → not_applicable → `:106`) — 버튼 표시 안 됨. 실제 백오프 재시도는 전역 `useRetryableAction` 훅(`useRetryableAction.ts:126-138`)이 `RateLimitNotice` 배너로 처리 | 구현됨 (`PayoutsPage.tsx:96-109, 175`, `errorRouting.ts:119-121, 125-126`, `accountLockout.ts:41-50`, `retryable.ts:27-31, 45-50`, `useRetryableAction.ts:126-138`) |
| 서버 오류(5xx) | `markPaid.isError`且에러의 statusCode 500且errorCode "INTERNAL_ERROR" 또는 statusCode 502且errorCode "EXCHANGE_FATAL" 또는 statusCode 503且errorCode "EXCHANGE_UNAVAILABLE"/"DEPENDENCY_NOT_READY" (`PayoutsPage.tsx:175`) | 500/502(fatal): `MarkPaidActionError` → `classifyServerError`(`serverError.ts:47-49`)가 `{kind:"fatal", traceId}` → `routeApiError`(`:98`)가 `{kind:"server_fatal", traceId}` → `ErrorMessage`(`:101-107`)에 errorCode/message/traceId 전달. traceId 노출(`ErrorMessage.tsx:60`). 503(retryable): `classifyServerError`가 `{kind:"retryable", afterSec}` → `routeApiError`가 `{kind:"backoff_retry", afterSec}` → `ErrorMessage`에 `retryAfterSec`(`:105`) + `onRetry`(`:106`, `serverError.kind==="retryable"`) 전달. 카운트다운 후 재시도 버튼 | 500/502: 재시도 버튼 없음(fatal). traceId 노출(`ErrorMessage.tsx:60`). 503: 재시도 버튼 있음(`onRetry` 설정됨, `PayoutsPage.tsx:106`). 백오프 시간: 서버 제공 `retryAfterSec` 또는 기본 없음 | 구현됨 (`PayoutsPage.tsx:96-109, 175`, `serverError.ts:39-52`, `errorRouting.ts:115-117`) |
| 네트워크 단절 | `query.isError`且에러가 fetch 실패(`PayoutsPage.tsx:227`) 또는 `markPaid.isError`且에러가 fetch 실패 (`PayoutsPage.tsx:175`) | 네트워크 에러는 statusCode/errorCode 필드 없음. `routeApiError`(`:98`)에서 `isErrorCodeLike`(`errorRouting.ts:80-82`)가 false → 모든 분류기 통과 → `{kind:"unknown"}`. `ErrorMessage`(`:101-107`)에 error.message 전달. traceId 없음. 재시도 버튼 없음(`classifyRetry`가 "none") | `CursorListSection`(`PayoutsPage.tsx:216-217`)에서 `routed.kind`가 "unknown" → `canRetry=false` → 재시도 버튼 없음. `MarkPaidActionError`(`PayoutsPage.tsx:106`)에서 `classifyServerError`가 "not_applicable" → `onRetry` 미설정. 수동 refetch/retry 불가 — 사용자가 화면 새로고침 또는 네트워크 복구 후 수동 리프레시 | 구현됨 (`PayoutsPage.tsx:216-234, 96-109`, `errorRouting.ts:84-86, 132`) |
| 부분 실패(일부 데이터만 실패) | `CursorListSection` 두 개(홀드:`PayoutsPage.tsx:274-282`, 정산 배치:`PayoutsPage.tsx:283-291`) 중 하나만 `query.isError` | 실패한 섹션만 `ErrorMessage`(`PayoutsPage.tsx:227-234`) 표시. 성공한 섹션은 정상 렌더(`HoldCard` 또는 `PayoutBatchCard`). 두 섹션은 각각 독립 `useQuery`(`PayoutsPage.tsx:207-210`)·독립 `fetchPage` 함수·독립 커서 상태 | 사용자가 성공한 섹션은 확인하고 실패한 섹션은 새로고침 시도. 재시도 버튼은 네트워크/서버 오류 분류에 따라 위 행들과 동일 | 구현됨 (`PayoutsPage.tsx:207-210, 227-234, 274-291`) — 구조적 보장(각 섹션 독립 쿼리) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-PayoutsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/wallet/PayoutsPage.tsx:164`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/wallet/PayoutsPage.tsx:245`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/wallet/PayoutsPage.tsx:247`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-PayoutsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-PayoutsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `Alert` (`frontend/apps/web/src/routes/wallet/PayoutsPage.tsx:60`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/wallet/PayoutsPage.tsx:97`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/wallet/PayoutsPage.tsx:97`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/wallet/PayoutsPage.tsx:98`) | TODO | TODO | TODO |
| `classifyServerError` (`frontend/apps/web/src/routes/wallet/PayoutsPage.tsx:99`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/wallet/PayoutsPage.tsx:101`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-PayoutsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-PayoutsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-PayoutsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
