# J6-ConnectionsPage — `/settings/connections`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ConnectionsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/settings/connections` / `frontend/apps/web/src/routes/settings/ConnectionsPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-ConnectionsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더) — `docs/frontend/PERSONAS_AND_JOURNEYS.md` §2: "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독". J1~J6 전체가 P1 중심. [확인] |
| 목적(한 문장) | 외부 제공자(거래소/증권사 등)에 계정 연동을 생성·확인·동기화·해지하고, 연동 상태와 스냅샷 신선도를 관리한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:153` — `/settings/connections` 경로는 `protect(<ConnectionsPage />)` 래퍼로 `ProtectedRoute`를 거쳐 인증된 사용자만 접근. [확인] |
| 사용자 준비물 | 제공자 코드(providerCode, 예: "toss"), 연동 대상 참조값(opaqueAccountRef), 요청할 권한 범위(CAPABILITY_OPTIONS: READ_BALANCE, READ_POSITION, READ_ACTIVITY 중 선택). [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-ConnectionsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useConnections` | `frontend/packages/shared-hooks/src/useConnections.ts:13` | `useQuery`로 마운트 시 1회 조회. 폴링 interval 설정 없음. 화면 '새로고침' 버튼(`ConnectionsPage.tsx:210-211`) + 4개 mutation 성공 시 queryKey 무효화로 GET 재조회. 전역 staleTime 기본값은 미확인. | `ConnectionsPage.tsx:180-181`에서 isError면 목록 대신 에러 컴포넌트 표시. 새로고침 버튼 숨김(`:209`). 403은 ForbiddenNotice 경로, 그 외는 ErrorMessage(errorCode/message/traceId/retryAfterSec 전달). 네트워크 단절/401/429/5xx 처리 근거는 이 범위에 없음 → 미확인. |
| `useBeginConnection` | `frontend/packages/shared-hooks/src/useConnections.ts:20` | 변경 요청(mutations, POST 성격). 성공 시 onSuccess 콜백(`:24`)에서 CONNECTIONS_QUERY_KEY 무효화 → useConnections가 GET 재조회. 페이지는 성공 시 폼 필드 초기화(`ConnectionsPage.tsx:133-137`). | 실패 시 onError 콜백(`:138-141`)에서 setCreateError + setFromError 호출. 폼 하단 에러 컴포넌트(`:272`) 표시. 필드별 오류는 fieldErrors 맵으로 ConnectionsPage.tsx:219(providerCode), `:230`(opaqueAccountRef), `:260`(requested_capability_profile)에 text-danger 클래스로 표시. |
| `useConfirmConnection` | `frontend/packages/shared-hooks/src/useConnections.ts:28` | 변경 요청(mutations). 성공 시 onSuccess 콜백(`:32`)에서 CONNECTIONS_QUERY_KEY 무효화 → 목록 GET 재조회. 행 버튼은 loading 속성(`ConnectionsPage.tsx:73`)으로 진행 상태 표시. | onError 콜백(`:150`)에서 setRowError로 행 오류 저장. 해당 행 아래에 에러 컴포넌트(`:198-202`)로 표시. 403/그 외 구분은 에러 컴포넌트(`:25-36`)에서 처리. |
| `useSyncConnection` | `frontend/packages/shared-hooks/src/useConnections.ts:36` | 변경 요청(mutations). 성공 시 onSuccess 콜백(`:40`)에서 CONNECTIONS_QUERY_KEY 무효화 → 목록 GET 재조회. 응답 스냅샷은 React Query 캐시에 넣지 않고 snapshots 로컬 상태(`ConnectionsPage.tsx:159`)에 저장 — 무효화로 갱신되지 않고 페이지 언마운트 시 사라짐. 행 버튼은 loading 속성(`:75`)으로 진행 상태 표시. | onError 콜백(`:160`)에서 setRowError로 행 오류 저장. 해당 행 아래에 에러 컴포넌트(`:198-202`)로 표시. |
| `useRevokeConnection` | `frontend/packages/shared-hooks/src/useConnections.ts:44` | 변경 요청(mutations). 성공 시 onSuccess 콜백(`:48`)에서 CONNECTIONS_QUERY_KEY 무효화 → 목록 GET 재조회. 행 버튼은 loading 속성(`:77`)으로 진행 상태 표시. | onError 콜백(`:169`)에서 setRowError로 행 오류 저장. 해당 행 아래에 에러 컴포넌트(`:198-202`)로 표시. |
| `useFieldErrors` | `frontend/apps/web/src/hooks/useFieldErrors.ts:15` | 로컬 `useState` 기반 폼 상태, 네트워크 호출 없음. 갱신 = 생성 실패 시 `setFromError`(`ConnectionsPage.tsx:140`), 입력 변경 시 `clearField`(`:224`, `:235`, `:253`). `setFromError(null)`(`:129`)는 폼 재제출 시 이전 오류 초기화. | 매핑은 `shared-types`의 `extractFieldErrors`(`useFieldErrors.ts:2,19`, 주석 `:11`에서 VALIDATION_INVALID_FIELD)가 담당. 매핑 세부는 `fieldErrors.ts` 미확인. `ConnectionsPage.tsx:129`의 `setFromError(null)` 동작은 미확인. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-ConnectionsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `isLoading` (`useConnections`의 `useQuery` isLoading, `useConnections.ts:14`) | `<LoadingState />` 컴포넌트 표시 (`ConnectionsPage.tsx:183`) | 없음 (로딩 중 버튼 비활성화) | 구현됨 (`ConnectionsPage.tsx:182-183`) |
| 빈 상태 | `!isLoading && !isError && data.connections.length === 0` (`ConnectionsPage.tsx:184,206-208`) | `<EmptyState>`에 `legacy.connectionsPage.t9` ("등록된 계정 연동이 없습니다.", `ConnectionsPage.tsx:207`) | "새로고침" 버튼 (`:210-211`, `refetch()`) / "새 연동 생성" 버튼 (`:213-216`, 폼으로 스크롤) | 구현됨 (`ConnectionsPage.tsx:206-217`) |
| 정상 | `!isLoading && !isError && data.connections.length > 0` (`ConnectionsPage.tsx:184`) | 각 연결 행에 `<ConnectionRow>` 표시: 계정 레이블, `StatusBadge` (`:63-64`), 범위 검증 스패ан (`:65`), 제공자 정보 (`:68`), 스냅샷 섹션 (`:81-93`). 행별 Confirm/Sync/Revoke 버튼 (`:73-78`). 상단 "새로고침" 버튼 (`:210-211`) | 행별 Confirm (`:146-153`), Sync (`:155-163`), Revoke (`:165-172`) / 페이지 새로고침 (`:210-211`, `refetch()`) | 구현됨 (`ConnectionsPage.tsx:184-205`) |
| 낡은 데이터(신선도 초과) | `useConnections`에 staleTime/refetchInterval 설정 없음 (`useConnections.ts:14-17`). 스냅샷 freshness (`snapshot.freshness`, `:84`)는 표시되지만 임계값 비교 로직 없음 | — | — | 없음 (`useConnections.ts:14-17`에 staleTime 없음, `ConnectionsPage.tsx:84`에서 freshness 표시만 하고 임계값 초과 감지 없음) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-ConnectionsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `beginConnection.mutate`의 `onError` (`ConnectionsPage.tsx:138-141`) — 서버가 400을 반환하고 `errorCode`가 `VALIDATION_*` 형식 | 필드별 오류 문구가 `fieldErrors` 맵으로 `text-danger` 클래스 표시 (`:219`: provider_code, `:230`: opaque_account_ref, `:260`: requested_capability_profile). `setFromError`(`:140`)가 `useFieldErrors`에 매핑. | 필드 수정 후 폼 재제출 (`:125-144 handleCreate`) | 구현됨 (`:138-141`, `:219`, `:230`, `:260`, `:224`, `:235`, `:253`) |
| 인증 필요/세션 만료(401) | `classifyForbidden`(`:26`) null → `routeApiError`(`:27`)에서 `isSessionExpiredErrorCode`(`errorRouting.ts:128-129`)가 true → `auth_required` kind | `ErrorMessage`(`:29-34`)가 `getApiErrorMessage`로 오류 문구 표시. trace_id(`:32`) 노출. 401 전용 리다이렉트/로그아웃 없음. | 새로고침 (`ErrorMessage`의 `canRetry` 로직, `ErrorMessage.tsx:61-62`) — but `onRetry` prop이 `ConnectionActionError`(`ConnectionsPage.tsx:25`)에서 전달되지 않음 → 재시도 버튼 없음. 미확인: `getApiErrorMessage`가 `AUTH_*` 코드의 문구를 어떻게 매핑하는지 | 부분 구현 (`:26-34`, `ErrorMessage.tsx:27-34`로 표시되나 401 전용 처리 없음, `onRetry` 미배선) |
| 권한 없음/MFA 필요(403) | `classifyForbidden`(`:26`)이 403에서 true → `ForbiddenKind` 반환 (`forbidden.ts:31-41`) | `<ForbiddenNotice>`(`:27`) 표시. kind별: `policy`→`DenialReasons`, `mfa_required`→`onStepUp` 버튼(`ForbiddenNotice.tsx:42-47`), 그 외→일반 문구(`:40`) | MFA required 시 `onStepUp`으로 단계 상승 — but `ConnectionActionError`(`ConnectionsPage.tsx:25`)가 `onStepUp` prop을 전달하지 않음 → 미배선 | 구현됨 (`:26-27`, `ForbiddenNotice.tsx:31-50`), onStepUp 미배선 (`ConnectionsPage.tsx:25`에 prop 누락) |
| 충돌/상태 불일치(409) | `classifyForbidden`(`:26`) null → `routeApiError`(`:27`)에서 `classifyStateConflict`(`errorRouting.ts:111-113`) — `statusCode === 409` | `ErrorMessage`(`:29-34`) 표시. `refetch_retry`일 때 `retryAfterSec`(`:33`) 전달. 409 전용 문구 없음. | `refetch_retry`일 때 `ErrorMessage` retry 로직 (`ErrorMessage.tsx:38-40`, `:61-62`). but `onRetry` 미배선. `invalid_transition`일 때 재시도 불가. | 부분 구현 (`:26-34`, `ErrorMessage`로 표시되나 409 전용 처리 없음, `onRetry` 미배선) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-ConnectionsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | `shared-hooks/src/useConnections.ts:14`의 GET 또는 mutation 중 서버가 `RATE_LIMIT_EXCEEDED`(429) 반환 (`retryable.ts:27-31`, `errorRouting.ts:121` → `backoff_retry`). `retryAfterSec` 포함 가능. | `ConnectionActionError`(`ConnectionsPage.tsx:25-36`) → `classifyForbidden`(`:26`) null → `routeApiError`(`:27`)가 `backoff_retry` 반환 → `ErrorMessage`(`:29-34`)가 `retryAfterSec`(`:33`) 전달. `ErrorMessage.tsx`에서 `classifyRetry`(`:38`) → `backoff` → 카운트다운(`:49-53`) 후 `remainingSec` 0이 되면 재시도 버튼 활성화(`:66`). | 재시도 버튼 클릭 — but `ConnectionActionError`(`:25-36`)가 ErrorMessage의 `onRetry` prop(`ErrorMessage.tsx:20`)을 전달하지 않음 → 버튼 비활성화 상태만 표시. `retryAfterSec` 카운트다운 후 활성화. | 부분 구현 (`:26-34`, `ErrorMessage.tsx:49-66`에 카운트다운·재시도 로직 있으나 `onRetry` 미배선). 423(LOCKED) 코드 미확인 — `BACKOFF_ERROR_CODES`에 없음(`retryable.ts:27-31`). |
| 서버 오류(5xx) | `shared-hooks/src/useConnections.ts:14`의 GET 또는 mutation 중 서버가 500/502/503 반환. `EXCHANGE_UNAVAILABLE`(503)/`DEPENDENCY_NOT_READY`(503)는 `BACKOFF_ERROR_CODES`(`retryable.ts:29-30`) → `backoff_retry`. 그 외 5xx는 `classifyServerError`(`errorRouting.ts:115-117`)에서 `retryable`/`fatal` 판정. | `retryable`(503 등): `ErrorMessage`에 `backoff_retry` → 429와 동일 카운트다운 경로. `fatal`(500 등): `ErrorMessage`에 `server_fatal` → `traceId`(`:32`) 노출, `retryAfterSec` 없음 → 재시도 불가. `classifyForbidden`(`:26`) null → `routeApiError`(`:27`) → `classifyServerError`(`:115-117`). | 재시도 불가(fatal) 또는 `retryAfterSec` 후 재시도 가능(backoff_retry). — but ErrorMessage의 `onRetry`(`ErrorMessage.tsx:20`)가 ConnectionActionError에서 전달되지 않음. | 부분 구현 (`:26-34`, `ErrorMessage.tsx:38-66`). `server_fatal`은 trace_id만 보이고 재시도 버튼 안 보임(`canRetry` false). `backoff_retry`는 카운트다운 있으나 `onRetry` 누락. |
| 네트워크 단절 | `shared-hooks/src/useConnections.ts:14`의 GET 또는 mutation 중 fetch/XHR 실패 → TypeError. `error`는 `ApiError` 아님 (`ConnectionsPage.tsx:25-36`). React Query가 `isError=true`, `error=TypeError` 설정. | `classifyForbidden`(`:26`) null(TypeError는 object이나 statusCode 없음). `routeApiError`(`:27`) → 모든 조건 실패 → `{ kind: "unknown" }`(132). `ErrorMessage`(`:29-34`)가 `error instanceof ApiError` false → errorCode/traceId undefined, message도 undefined. `getApiErrorMessage`(`apiError.ts:117`) → `DEFAULT_API_ERROR_MESSAGE`. | 없음(재시도 버튼 없음, ErrorMessage의 `onRetry`(`ErrorMessage.tsx:20`) 미배선). 사용자는 generic error 문구만 보고 '새로고침' 버튼(`:210-211`)으로 수동 재시도. | 구현됨(`:26-34`, generic 메시지 표시). 재시도 경로 없음(`onRetry` 누락, `:210-211`의 새로고침 버튼이 유일한 수동 복구 수단). |
| 부분 실패(일부 데이터만 실패) | 목록 조회(`shared-hooks/src/useConnections.ts:14`)는 전체 성공/실패. 행별 mutation(confirm/sync/revoke) 중 하나만 실패 — 다른 행은 정상. `rowError`(`ConnectionsPage.tsx:116`)가 실패한 행 ID로만 저장. | 성공한 행: `<ConnectionRow>` 정상 표시. 실패한 행: `rowError?.connectionId === connection.id`(`:198`) → `<ConnectionActionError>`(`:200`) 표시. 폼 생성 실패(`:272`)도 동일 `ConnectionActionError`. 다른 행 영향 없음. | 실패한 행의 버튼 재시도 — but ErrorMessage의 `onRetry`(`ErrorMessage.tsx:20`) prop이 `ConnectionActionError`에 전달되지 않음. 성공한 행은 계속 사용 가능. "새로고침" 버튼(`:210-211`)으로 전체 목록 갱신 가능. | 구현됨 (`:116`, `:146-172`, `:198-202`, `:272`). 행별 격리 정상. 재시도 버튼 누락(`ConnectionActionError`에 `onRetry` prop 없음). |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-ConnectionsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/settings/ConnectionsPage.tsx:73`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/settings/ConnectionsPage.tsx:75`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/settings/ConnectionsPage.tsx:77`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/settings/ConnectionsPage.tsx:210`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/settings/ConnectionsPage.tsx:264`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-ConnectionsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-ConnectionsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/ConnectionsPage.tsx:26`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/ConnectionsPage.tsx:26`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/settings/ConnectionsPage.tsx:27`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/ConnectionsPage.tsx:29`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-ConnectionsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-ConnectionsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-ConnectionsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
