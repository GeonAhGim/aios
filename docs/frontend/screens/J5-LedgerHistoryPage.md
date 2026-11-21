# J5-LedgerHistoryPage — `/wallet/ledger`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/LedgerHistoryPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/wallet/ledger` / `frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx`
- 여정 소속: J5


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J5-LedgerHistoryPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더) — 원장 내역으로 자신의 모든 거래·배분·수수료 이력을 조회. P11(감사자)도 읽기 전용으로 접근 가능(PERSONAS_AND_JOURNEYS.md §2). |
| 목적(한 문장) | 원장(ledger)의 모든 엔트리를 커서 기반 페이지네이션으로 조회하고 이전/다음으로 탐색한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:142` — `/wallet/ledger` 경로, `protect()` HOC로 인증 요구. |
| 사용자 준비물 | 로그인된 세션(protect HOC 필요). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J5-LedgerHistoryPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useCursorPage` | `frontend/apps/web/src/hooks/useCursorPage.ts:25` | 훅 자체는 상태 계산만 하고 네트워크 요청이 없다(25-59행). `next()`/`prev()` 호출 시 `rerender()`로 리렌더링(30·35·41·46행). 폴링 없음. 호출처(`LedgerHistoryPage.tsx:48`)에서 직전 성공 응답의 `meta.page`(45-60행)를 주입받아 cursor를 관리. | 훅 자체는 에러 처리가 없고 `hasNext`/`hasPrev`만 반환(54행). 실제 에러 경로는 호출처에서 `query.isError`(73행)로 분기하여 `ErrorMessage`(74-80행) 표시. |
| `useQuery` | `@tanstack/react-query` (외부 패키지) | `queryKey: ["ledgerHistoryPage", cursorPage.cursor]`, `queryFn: () => fetchPage(cursorPage.cursor)`(50-53행). 폴링 설정 없음 → 폴링 없음. 1회 조회 후 이전/다음 버튼(87·89행) 클릭 시 cursor 변경 → 새 queryKey로 재조회. 수동 새로고침은 `ErrorMessage.onRetry`(79행)의 `query.refetch()`만 있음. | 기본 `fetchPage`(28-29행)는 항상 reject하며 메시지 "원장 내역 조회 API가 아직 제공되지 않습니다." 오류는 `query.isError`(73행)에서 `ErrorMessage`(74-80행)로 표시, 목록(82행)과 페이지 버튼(85행) 숨김. 재시도 가능 여부: `routeApiError`(62행) 결과의 `kind`가 `refetch_retry` 또는 `backoff_retry`일 때 `onRetry` 활성화(79행). `routeApiError` 내부 결정 로직은 미확인. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J5-LedgerHistoryPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `query.isLoading`이 true — `LedgerHistoryPage.tsx:82`에서 `LedgerEntryList`에 `isLoading={true}` 전달 | `LedgerEntryList`가 `<LoadingState />` 렌더(회색 로딩 인디케이터) | 사용자 상호작용 비활성화(로딩 중에는 버튼도 `disabled` 상태) | 구현됨 — `frontend/apps/web/src/components/LedgerEntryList.tsx:115-116` |
| 빈 상태 | `query.data`가 있고 `entries.length === 0`, `query.isError` false — `LedgerHistoryPage.tsx:82`의 entries prop이 빈 배열 | `<EmptyState>` 컴포넌트 표시, 문구 키 `legacy.ledgerEntryList.t10` | "이전" 버튼(`cursorPage.hasPrev`일 때 활성화, `LedgerHistoryPage.tsx:87-88`), "다음" 버튼(`cursorPage.hasNext`일 때 활성화, `LedgerHistoryPage.tsx:89-90`) | 구현됨 — `frontend/apps/web/src/components/LedgerEntryList.tsx:119-120` |
| 정상 | `query.data`가 있고 `entries`가 비어 있지 않으며 `query.isError` false | `<PageHeader>` 제목 `legacy.ledgerHistoryPage.title1`("원장 내역", `LedgerHistoryPage.tsx:69`), `<DataFreshness>`로 `as_of` 기준 시각·경과 시간 표시(70), `<LedgerEntryList>`가 각 엔트리를 `<JournalEntryCard>`로 렌더(82), 하단에 페이지네이션 버튼(85-92) | "이전" 버튼 → `cursorPage.prev` 호출 → cursor 변경 → `useQuery`가 새 `queryKey`로 재조회(87-88), "다음" 버튼 → `cursorPage.next` 동일 흐름(89-90) | 구현됨 — `frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:68-92`, `frontend/apps/web/src/hooks/useCursorPage.ts:32-42` |
| 낡은 데이터(신선도 초과) | `DataFreshness` 내부 `deriveFreshness`가 `isStale: true` 반환 — `ageSec >= staleAfterSec`일 때(`frontend/packages/api-client/src/envelope.ts:127`), 기본 `staleAfterSec=300`(`frontend/apps/web/src/components/DataFreshness.tsx:20`) | `DataFreshness`가 `legacy.dataFreshness.t2`("N분 전") 텍스트와 `legacy.dataFreshness.t3`("낡음") 경고 배지 렌더(`frontend/apps/web/src/components/DataFreshness.tsx:32-37`) | 명시적 새로고침 버튼 없음. "이전"/"다음" 페이지네이션 시 새 queryKey로 자동 재조회. 에러 표시 컴포넌트의 재시도 핸들러는 에러 상태에서만 표시되므로 신선도 초과 시에는 동작하지 않음 | 구현됨 — `frontend/apps/web/src/components/DataFreshness.tsx:34-36`, `frontend/packages/api-client/src/envelope.ts:127` |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J5-LedgerHistoryPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `query.isError` true, `routeApiError` 결과의 `kind`가 `field_errors` — `LedgerHistoryPage.tsx:62`에서 `routeApiError(query.error)` 호출 | `ErrorMessage` 컴포넌트 렌더(`LedgerHistoryPage.tsx:74-80`), 필드별 오류는 `fieldErrors` prop으로 전달되지 않음(본 화면에 입력 필드 없음). `getApiErrorMessage`(`@aios/shared-types`)가 `errorCode`를 한국어 메시지로 변환. `traceId` 노출(`legacy.errorMessage.t1` 키, `ErrorMessage.tsx:60`). | `routeApiError.kind`가 `refetch_retry` 또는 `backoff_retry`일 때만 `onRetry` 활성화(`LedgerHistoryPage.tsx:63-64`) — 입력 오류(400) 갈래는 일반적으로 재시도 없음. `field_errors` 갈래는 `ErrorMessage` 내부에서 `fieldErrors` prop이 없으므로 배너만 표시. | 구현됨 — `LedgerHistoryPage.tsx:62-64,74-80`, `errorRouting.ts:94-96`, `ErrorMessage.tsx:36-37` |
| 인증 필요/세션 만료(401) | `query.isError` true, `routeApiError` 결과의 `kind`가 `auth_required` — `LedgerHistoryPage.tsx:62`에서 `routeApiError(query.error)` 호출, `isSessionExpiredErrorCode`가 true 반환 | `ErrorMessage` 컴포넌트 렌더(`LedgerHistoryPage.tsx:74-80`), `getApiErrorMessage`가 세션 만료 메시지 반환. `traceId` 노출(`legacy.errorMessage.t1` 키). | `auth_required` 갈래는 재시도 없음(`refetch_retry`/`backoff_retry` 아님). 재로그인 유도 필요하지만 화면 내 별도 버튼 없음. | 구현됨 — `LedgerHistoryPage.tsx:62-64,74-80`, `errorRouting.ts:128-130`, `ErrorMessage.tsx:36-37` |
| 권한 없음/MFA 필요(403) | `query.isError` true, `routeApiError` 결과의 `kind`가 `mfa_required`, `tenant_mismatch`, `policy_denied`, 또는 `forbidden` — `LedgerHistoryPage.tsx:62`에서 `routeApiError(query.error)` 호출, `classifyForbidden`가 true 반환 | `ErrorMessage` 컴포넌트 렌더(`LedgerHistoryPage.tsx:74-80`). `policy_denied`/`forbidden` 갈래는 `reasonCodes`를 포함하지만 `ErrorMessage`가 이를 표시하는지는 미확인. `traceId` 노출(`legacy.errorMessage.t1` 키). | 권한 오류는 재시도로 해결되지 않으므로 재시도 버튼 비활성화. `mfa_required`는 MFA 재요청 필요하지만 화면 내 별도 MFA 플로우 없음. | 구현됨 — `LedgerHistoryPage.tsx:62-64,74-80`, `errorRouting.ts:98-102`, `ErrorMessage.tsx:36-37` |
| 충돌/상태 불일치(409) | `query.isError` true, `routeApiError` 결과의 `kind`가 `idempotency_new_key`, `idempotency_missing_header`, `refetch_retry`, `invalid_transition`, `data_coverage_missing`, 또는 `wallet_balance_drift` — `LedgerHistoryPage.tsx:62`에서 `routeApiError(query.error)` 호출, `classifyIdempotencyFailure` 또는 `classifyStateConflict`가 true 반환 | `ErrorMessage` 컴포넌트 렌더(`LedgerHistoryPage.tsx:74-80`). `refetch_retry` 갈래는 재시도 가능(`backoff_retry`일 때 `retryAfterSec` 카운트다운, `ErrorMessage.tsx:49-53`). `idempotency_new_key`/`idempotency_missing_header`는 재시도 금지(멱등키 문제). | `refetch_retry`일 때만 `onRetry` 활성화(`LedgerHistoryPage.tsx:63-64`). 나머지는 재시도 불가. | 구현됨 — `LedgerHistoryPage.tsx:62-64,74-80`, `errorRouting.ts:111-113,104-109`, `ErrorMessage.tsx:36-37` |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J5-LedgerHistoryPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 423: `query.isError` true, `routeApiError` 결과의 `kind`가 `account_locked` — `accountLockout.ts:41-50`에서 `statusCode===423 && errorCode==="AUTH_ACCOUNT_LOCKED"`일 때 반환. 429: `query.isError` true, `routeApiError` 결과의 `kind`가 `backoff_retry` — `retryable.ts:27-31`의 `BACKOFF_ERROR_CODES`("RATE_LIMIT_EXCEEDED", "EXCHANGE_UNAVAILABLE", "DEPENDENCY_NOT_READY") 중 하나일 때 `classifyRetry`가 `backoff`를 반환하고 `errorRouting.ts:124`에서 `backoff_retry`로 래핑. | 둘 다 `ErrorMessage` 컴포넌트 렌더(`LedgerHistoryPage.tsx:74-80`). 429는 `backoff_retry`이므로 `retryAfterSec`이 전달되어 `ErrorMessage.tsx:49-53`에서 초 단위 카운트다운 표시. 423은 `account_locked`이므로 `retryAfterSec`이 `deriveLockout`에서 계산되어(Clamp: 기본 60초, `accountLockout.ts:31-36`) `ErrorMessage`에 전달. 둘 다 `ErrorMessage.t3`("다시 시도") 버튼 표시. | 429: `backoff_retry` 갈래이므로 `LedgerHistoryPage.tsx:63`에서 `canRetry=true`, `ErrorMessage` 내부 카운트다운이 0이 되면 재시도 버튼 활성화. 423: `account_locked`은 `refetch_retry`/`backoff_retry`가 아니므로 `canRetry=false`(`LedgerHistoryPage.tsx:63`). `ErrorMessage` 내부 `classifyRetry`도 `kind==="none"`이므로 `canRetry=false`(`ErrorMessage.tsx:38-39`) — 버튼 자체가 렌더되지 않음. | 구현됨 — `LedgerHistoryPage.tsx:62-64,74-80`, `accountLockout.ts:41-50`, `retryable.ts:27-31,40-53`, `errorRouting.ts:124-125`, `ErrorMessage.tsx:38-39,49-53` |
| 서버 오류(5xx) | `query.isError` true. 503(EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY): `classifyServerError`가 `retryable` 반환 → `backoff_retry`. 502(EXCHANGE_FATAL)/500(INTERNAL_ERROR): `classifyServerError`가 `fatal` 반환 → `server_fatal`. — `serverError.ts:25-30`, `errorRouting.ts:121-122`. | `ErrorMessage` 컴포넌트 렌더(`LedgerHistoryPage.tsx:74-80`). 503은 `backoff_retry`로 `retryAfterSec` 카운트다운 표시. 502/500은 `server_fatal`로 `traceId` 노출(`ErrorMessage.tsx:60`). | 503: `backoff_retry`이므로 `canRetry=true`, 백오프 카운트다운 후 재시도 버튼 활성화. 502/500: `server_fatal`은 재시도 불가(`refetch_retry`/`backoff_retry` 아님) — `canRetry=false`, 재시도 버튼 없음. | 구현됨 — `LedgerHistoryPage.tsx:62-64,74-80`, `serverError.ts:39-52`, `errorRouting.ts:121-122`, `ErrorMessage.tsx:38-39` |
| 네트워크 단절 | `query.isError` true, `query.error`가 fetch 실패 시 던지는 `Error` 서브 클래스(`LedgerHistoryPage.tsx:75`에서 `instanceof ApiError` 체크가 false) — `routeApiError(query.error)`가 모든 분류기를 통과해 `{ kind: "unknown" }` 반환(`errorRouting.ts:133`). `query.error instanceof ApiError`가 false이므로 `errorCode`/`traceId`는 `undefined`. | `ErrorMessage` 컴포넌트 렌더(`LedgerHistoryPage.tsx:74-80`). `errorCode`/`traceId`가 `undefined`이므로 `getApiErrorMessage`가 `message`만 사용(`shared-types/apiError.ts:1`). `traceId` 블록(`ErrorMessage.tsx:60`) 렌더되지 않음. | `unknown` 갈래이므로 `canRetry=false`(`LedgerHistoryPage.tsx:63`). `ErrorMessage` 내부 `classifyRetry`도 `kind==="none"` — 재시도 버튼 없음. | 구현됨 — `LedgerHistoryPage.tsx:62-64,75-76`, `errorRouting.ts:133`, `ErrorMessage.tsx:38-39,60`, `apiError.ts:1` |
| 부분 실패(일부 데이터만 실패) | 이 화면은 단일 `useQuery` 요청으로 전체 페이지를 가져온다(`LedgerHistoryPage.tsx:50-53`). 부분 데이터 실패를 구분하는 메커니즘이 없음. | 해당 없음 — 전체 페이지가 실패하면 `ErrorMessage` 표시, 성공하면 `entries` 전체 표시. | 해당 없음 | 없음(단일 페이지 조회 구조이므로 부분 실패 발생 경로 없음) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-LedgerHistoryPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:87`) | `cursorPage.hasPrev`이 true — `useCursorPage.ts:53`에서 `navigator.getState().hasPrev` 기반. 버튼은 `disabled={!cursorPage.hasPrev}`(87행)로 비활성화 방지. | `cursorPage.prev` 호출 → `useCursorPage.ts:38-42`에서 `navigator.prev()` 실행 후 `rerender()`. `cursorPage.cursor` 변경 → `useQuery`(50-53행)가 새 `queryKey`로 자동 재조회 → `fetchPage` 호출 → `query.data` 갱신 → 목록 이전 페이지 표시. `committed` 상태도 58-60행에서 동기화. | 버튼이 `disabled`이므로 클릭 이벤트 자체가 발생하지 않음(React가 DOM 레벨에서 차단). `hasPrev`가 true인 경우에도 `navigator.prev()`는 내부 상태 이동만 하므로 실패 경로 없음. | 동일 화면(`/wallet/ledger`) — 이전 페이지 데이터로 갱신된 목록 표시. 라우트 이동 없음. |
| `<Button>` (`frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:89`) | `cursorPage.hasNext`이 true — `useCursorPage.ts:54`에서 `meta != null && meta.next_cursor != null` 기반. 직전 조회 응답의 `data.meta.page.next_cursor`가 있을 때만 true. 버튼은 `disabled={!cursorPage.hasNext}`(89행)로 비활성화 방지. | `cursorPage.next` 호출 → `useCursorPage.ts:32-36`에서 `meta.next_cursor` 확인 후 `navigator.next(meta)` 실행 → `rerender()`. `cursorPage.cursor` 변경 → `useQuery`(50-53행)가 새 `queryKey`로 자동 재조회 → `fetchPage` 호출 → `query.data` 갱신 → 목록 다음 페이지 표시. `committed` 상태도 58-60행에서 동기화. | 버튼이 `disabled`이므로 클릭 이벤트 자체가 발생하지 않음. `hasNext`가 true이더라도 `meta.next_cursor`가 null인 경우(53행 조건)에는 `next()`가 즉시 리턴(no-op). 실패 시 에러 표시 없음, 단순히 이전 상태로 유지. | 동일 화면(`/wallet/ledger`) — 다음 페이지 데이터로 갱신된 목록 표시. 라우트 이동 없음. |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-LedgerHistoryPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-LedgerHistoryPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:62`) | routeApiError는 내부 분류기이므로 직접 사용자 문구를 생산하지 않음. `RoutedApiError.kind`로 16종 갈분(refetch_retry, backoff_retry, server_fatal, auth_required, field_errors, not_found, unknown 등) — 분류기 정의: `frontend/packages/shared-types/src/errorRouting.ts:88-133` | 분류 결과에 따라 `canRetry`(`LedgerHistoryPage.tsx:63`)가 true이면 `ErrorMessage`가 재시도 버튼 표시. false이면 버튼 없음(사용자는 페이지 이탈 또는 새로고침). `backoff_retry`일 때 `retryAfterSec`으로 백오프 초 표시. `server_fatal`일 때 `traceId`가 `routed.traceId`에 담기나 LedgerHistoryPage는 이를 ErrorMessage에 전달하지 않음(74-80행에서 `query.error` 원문의 traceId만 전달). | routeApiError 자체는 trace_id를 노출하지 않음. `server_fatal` 갈래에서 `routed.traceId`에 담지만 LedgerHistoryPage는 ErrorMessage에 이 값을 전달하지 않음(`LedgerHistoryPage.tsx:77`에서 `query.error instanceof ApiError ? query.error.traceId : undefined`만 전달). |
| `ErrorMessage` (`frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:74`) | `getApiErrorMessage(errorCode, message)` — `errorCode`가 있으면 `EXACT_MESSAGES`에서 1:1 매핑(한국어), 없으면 `PREFIX_MESSAGES` 접두 매칭, 그마저 없으면 `DEFAULT_API_ERROR_MESSAGE`("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.") — 매핑 정의: `frontend/packages/shared-types/src/apiError.ts:55-84,89-100`.<br><br>i18n 키: `legacy.errorMessage.t1` (traceId 표시), `legacy.errorMessage.t2` (카운트다운), `legacy.errorMessage.t3` (재시도 버튼) — `frontend/apps/web/src/components/ErrorMessage.tsx:60,64,67` | `classifyRetry`가 "none"이 아니면 재시도 버튼 표시(`ErrorMessage.tsx:38-39`). `backoff`(예: RATE_LIMIT_EXCEEDED)는 retryAfterSec 카운트다운 후 버튼 활성화. `refetch`(예: STATE_CONCURRENCY_CONFLICT)는 즉시 활성화. 버튼 클릭 시 `onRetry` 호출(호출부에서 `query.refetch()`). 재시도 불가 오류(401, 404, field_errors 등)는 버튼 없음. fieldErrors가 있으면 배너 전체를 생략(`ErrorMessage.tsx:55`). | traceId 노출함 — `query.error instanceof ApiError ? query.error.traceId : undefined`(77행)로 ApiError에서 traceId를 받아 `ErrorMessage`에 전달. ErrorMessage는 `traceId &&` 조건으로 truthy일 때 `{t("legacy.errorMessage.t1", { traceId: traceId })}`로 `<p>` 태그에 표시(`ErrorMessage.tsx:60`). traceId는 muted small 텍스트로 표시되어 노출 수준이 낮음. |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-LedgerHistoryPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-LedgerHistoryPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-LedgerHistoryPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
