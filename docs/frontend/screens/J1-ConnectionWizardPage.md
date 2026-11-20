# J1-ConnectionWizardPage — `/onboarding/connect`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ConnectionWizardPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/connect` / `frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-ConnectionWizardPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더). 근거 `PERSONAS_AND_JOURNEYS.md:40` — 시작·온보딩(J1) 주 페르소나는 P1만 있음. |
| 목적(한 문장) | 공급자 코드와 계정 키로 공급자 계정을 연결하고, 읽기 전용 권한(READ_BALANCE/READ_POSITION/READ_ACTIVITY) 중 선택해 연결 요청을 보내는 3단계 마법사 (`ConnectionWizardPage.tsx:12-14,96-198`). 성공 시 "연결 요청을 보냈습니다. 설정 > 연결에서 상태를 확인하세요." (`:87`). |
| 진입 조건 | `router.tsx:91-97` — `/onboarding/connect`는 `protect()`(`router.tsx:70-72`, `ProtectedRoute`로 로그인·MFA·적합성평가 필수)와 `FeatureFlagGate flag="onboarding_connection_wizard"`로 감겨 있음. `FeatureFlagGate` 구현 확인: 플래그 꺼짐 시 `EmptyState` + `common.featureDisabled` 표시 (`FeatureFlagGate.tsx:16-17`). `navItems.ts:14`에 메뉴 링크 있으나 메뉴 노출이 플래그에 묶였는지는 미확인. |
| 사용자 준비물 | 공급자 코드(자유 입력, `ConnectionWizardPage.tsx:99-100`, 목록 선택 없음), 공급자가 발급한 계정 키(opaque_account_ref, `ConnectionWizardPage.tsx:102-110`, password 타입 입력), 요청할 권한 1개 이상(`READ_BALANCE`/`READ_POSITION`/`READ_ACTIVITY`, `:12, :56`). 키 발급 절차와 MFA 필요 여부는 미확인. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-ConnectionWizardPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useBeginConnection` | `frontend/packages/shared-hooks/src/useConnections.ts:20` | mutation(`useMutation` `:22`); `onSuccess`에서 `qc.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY })`(`:24`) → `["connections"]` 키 무효화(GET 재조회); 1회 조회 아님·폴링 없음(폴링/만료 설정 옵션 전역 정의 없음 `useConnections.ts` 전역 0건) | mutation 시 `onError` → `setSubmitError(err)`(`ConnectionWizardPage.tsx:68`); review 단계(`step==="review"` `:168`)에서 `submitError` truthy → `<WizardActionError error={submitError} />` 렌더(`:188`); `WizardActionError`(`:19-30`) — `classifyForbidden(error)` 참이면 `<ForbiddenNotice error={error} />`(`:20`), 아니면 `<ErrorMessage>`(`:23-28`, `errorCode`/`message`/`traceId` 전달, `retryAfterSec`는 `routed.kind==="backoff_retry"`일 때만 `:27`); 화면 상태 `succeeded`는 false 유지, 재시도는 연결하기 버튼(`:193`) 클릭; 미확인: `ForbiddenNotice`/`ErrorMessage` 내부 렌더 내용, `apiClient.beginConnection` 던지는 오류 형태, QueryClient mutation 재시도 기본값 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-ConnectionWizardPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `beginConnection.isPending`이 true — review 단계에서 `handleSubmit()` 호출 후 mutation 제출 중 (`ConnectionWizardPage.tsx:60-70`, `:193`) | review 단계의 `연결하기` 버튼(`connectWizard.steps.review.submit`, `:194`)에 `loading` prop이 걸려 버튼 텍스트가 비활성화되고 로딩 인디케이터 표시. 이 화면에는 목록 조회 로딩 문구 키가 없음(페이지 진입 시 데이터 없음). | 제출 취소 버튼 없음. 로딩 완료(성공/실패)까지 대기. | 구현됨(`:193` Button에 `loading={beginConnection.isPending}` 전달). Button이 loading prop을 어떻게 렌더하는지는 ui-web 내부 미확인. |
| 빈 상태 | 초기 입력 상태 — `providerCode=""`, `opaqueAccountRef=""`, `capabilityProfile=[]`, `readonlyConfirmed=false` (`:45-48`). 권한 체크 0개이므로 `providerStepValid=false` (`:56`) | provider 단계: 빈 `공급자 코드` 입력 필드(`connectWizard.steps.provider.providerLabel`, `:99`), 빈 `계정 키` 입력 필드(`connectWizard.steps.provider.keyLabel`/`keyPlaceholder`, `:102-107`), 권한 체크박스 3개 모두 미선택(`:117-124`), `다음` 버튼(`connectWizard.steps.provider.next`, `:132`)이 `disabled={!providerStepValid}` (`:131`)로 비활성화 | 권한 1개 이상 선택 후 `다음` 클릭 → permissions 단계로 진행. | 구현됨(provider 단계 입력 필드 `:96-133`, `providerStepValid` 조건 `:56`, `next` 버튼 `disabled` `:131`). |
| 정상 | provider → permissions(`readonlyConfirmed` 체크) → review → 제출 성공 (`onSuccess` → `setSucceeded(true)`, `:67`). `succeeded`가 true일 때 (`:85`) | `connectWizard.steps.review.success` 문구("연결 요청을 보냈습니다. 설정 > 연결에서 상태를 확인하세요.", `:87`)이 `Alert tone="success"` (`:86`)로 표시. 하단에 `connectWizard.steps.review.goToConnections` 버튼("연결 목록으로 이동", `:90`) | `연결 목록으로 이동` 버튼 클릭 → `/settings/connections` 이동 (`:88`, `Link to="/settings/connections"`). | 구현됨(성공 시 `Alert` + `Link` 버튼 `:85-93`, `onSuccess` 핸들러 `:67`). |
| 낡은 데이터(신선도 초과) | 이 화면은 기존 연결 데이터를 조회하지 않는다. mutation만 존재 (`useConnections.ts:20-25`). 신선도 설정(staleTime, isStale, refetch)이 이 페이지와 훅에 없음. | 없음 — 화면이 기존 연결 상태를 읽어오지 않으므로 신선도 초과 개념이 적용되지 않음. | N/A | 없음(조회 훅 없음, mutation 전용 `:20-25`). 성공 후 `qc.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY })`(`useConnections.ts:24`)가 연결 목록 쪽 신선도를 맞추는 유일한 장치. |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-ConnectionWizardPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `routeApiError`가 `field_errors`/`idempotency_missing_header`/`idempotency_new_key` 반환 — 400 응답 (`errorRouting.ts:111-113`) | `ErrorMessage` 렌더(`ConnectionWizardPage.tsx:21-28`). `fieldErrors` 있으면 배너 숨김(`ErrorMessage.tsx:55`), 없으면 `<Alert tone="danger">` + `getApiErrorMessage(errorCode, message)` 문구(`:36-37`) + traceId(`:60`, `legacy.errorMessage.t1`) + 재시도 버튼(`:61-68`, `legacy.errorMessage.t2` countdown, `t3` retry). `classifyRetry`가 "backoff"일 때 retryAfterSec 카운트다운(`:41-53`), "refetch"일 때 즉시 활성화. | `field_errors`일 경우 필드별 오류 표시(이미 표시됨). 재시도 가능 시 `연결하기` 버튼 재클릭(`:193`). `backoff_retry`일 때 countdown 종료 후 재클릭. | 구현됨(공통 라우터 위임, 경로: `ConnectionWizardPage.tsx:21-28`, `errorRouting.ts:111-113`, `ErrorMessage.tsx:36-39`) |
| 인증 필요/세션 만료(401) | `isSessionExpiredErrorCode` 참 → `routeApiError`가 `{kind: "auth_required"}` 반환 — 401 응답 (`errorRouting.ts:128-130`) | `ErrorMessage` 렌더(`ConnectionWizardPage.tsx:21-28`). `errorCode`로 `getApiErrorMessage` 호출(`:36`). traceId 표시(`:60`). `classifyRetry`가 "none" → `canRetry=false`(`:39`), 재시도 버튼 미표시. | `연결하기` 버튼 재클릭(`:193`). 재시도 버튼 없음(세션 만료시 로그인 유도). 성공 상태 미설정(`succeeded` 유지 false, `:67`). | 구현됨(공통 라우터 위임, 경로: `errorRouting.ts:128-130`, `ConnectionWizardPage.tsx:21-28`, `ErrorMessage.tsx:36-39`) |
| 권한 없음/MFA 필요(403) | `classifyForbidden` 참 — 403 응답 (`forbidden.ts:31-41`). 네 갈래: `mfa_required`(`errorCode==="AUTH_MFA_REQUIRED"`, `:35`), `tenant_mismatch`(`:36`), `policy`(`errorCode` `POLICY_`/`RISK_` 접두사, `:37-38`), `forbidden`(나머지 403, `:40`) | `ForbiddenNotice` 렌더(`ConnectionWizardPage.tsx:20`). `<Alert tone="danger">` + `getApiErrorMessage` 문구(`ForbiddenNotice.tsx:36`). `policy`일 때 `DenialReasons` 컴포넌트(`:41`). `mfa_required`일 때 `onStepUp` 버튼 + `legacy.forbiddenNotice.t1`(`:42-47`). | `mfa_required`일 때 `onStepUp` 버튼 클릭(현재 `ConnectionWizardPage.tsx:20`에서 `onStepUp` prop 전달 안 함 — 미구현). 나머지: 재시도 불가(`연결하기` 버튼 재클릭, `:193`). | 구현됨(공통 라우터 위임, 경로: `ConnectionWizardPage.tsx:20`, `forbidden.ts:31-41`, `ForbiddenNotice.tsx:31-49`). 단 `ForbiddenNotice.onStepUp` prop 전달 없음(`ConnectionWizardPage.tsx:20`) |
| 충돌/상태 불일치(409) | `classifyStateConflict` 참 또는 특정 errorCode → `refetch_retry`/`invalid_transition`/`data_coverage_missing`/`wallet_balance_drift` (`errorRouting.ts:104-113`) | `ErrorMessage` 렌더(`ConnectionWizardPage.tsx:21-28`). `errorCode`/`message`/`traceId` 전달(`:24-26`). `refetch_retry`일 때 `classifyRetry`가 "refetch" → `canRetry=true`(`ErrorMessage.tsx:38-39`), 즉시 재시도 버튼 활성화. `backoff_retry`일 때 countdown. | `연결하기` 버튼 재클릭(`:193`). `data_coverage_missing`/`wallet_balance_drift`는 재시도 없음(수집 후 재요청). 성공 상태 미설정(`succeeded` 유지 false, `:67`). | 구현됨(공통 라우터 위임, 경로: `ConnectionWizardPage.tsx:21-28`, `errorRouting.ts:104-113`, `ErrorMessage.tsx:36-39`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-ConnectionWizardPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 423: `AUTH_ACCOUNT_LOCKED` — `deriveLockout()`(`accountLockout.ts:41-50`) → `account_locked` 라우트(`errorRouting.ts:125-126`). 429: `RATE_LIMIT_EXCEEDED` — `classifyRetry()`("backoff", `retryable.ts:45-49`) → `backoff_retry` 라우트(`errorRouting.ts:119-121`) | `ErrorMessage` 렌더(`ConnectionWizardPage.tsx:21-28`). `errorCode`로 `getApiErrorMessage` 호출(`ErrorMessage.tsx:36`). `classifyRetry`가 "backoff" → `canRetry=true`, `remainingSec` 카운트다운(`:50-53`). 재시도 버튼(`:66`, `legacy.errorMessage.t3`)은 `remainingSec>0` 시 `disabled`. 전역 `RateLimitNotice`(`main.tsx:29`)도 429일 때 화면 상단에 고정 배너 표시(`RateLimitNotice.tsx:44-60`, `remainingSec` 카운트다운 + "다시 시도" 버튼) | `연결하기` 버튼 재클릭(`:193`). `backoff_retry`일 때 countdown 종료 후 재클릭 또는 `RateLimitNotice`의 "다시 시도" 버튼 클릭. 423은 `retryAfterSec`(기본 60초, `accountLockout.ts:14`) 후 재시도 | 구현됨(공통 라우터 위임, 경로: `errorRouting.ts:119-126`, `ConnectionWizardPage.tsx:21-28`, `ErrorMessage.tsx:50-53,66`, `RateLimitNotice.tsx:44-60`, `accountLockout.ts:14,41-50`) |
| 서버 오류(5xx) | 503: `EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY` — `classifyServerError()`("retryable", `serverError.ts:42-44`) → `backoff_retry` 라우트(`errorRouting.ts:116`). 500/502: `INTERNAL_ERROR`/`EXCHANGE_FATAL` — `classifyServerError()`("fatal", `:47-48`) → `server_fatal` 라우트(`errorRouting.ts:117`) | `ErrorMessage` 렌더(`ConnectionWizardPage.tsx:21-28`). retryable(503): `classifyRetry` "backoff" → 카운트다운 + 재시도 버튼(`ErrorMessage.tsx:50-53,66`). fatal(500/502): `canRetry=false`(`:39`), traceId 표시(`:60`, `legacy.errorMessage.t1`), 재시도 버튼 미표시(`:61`) | retryable(503): countdown 종료 후 `연결하기` 버튼 재클릭(`:193`). fatal(500/502): 재시도 불가(동일 오류 반복). 성공 상태 미설정(`succeeded` 유지 false, `:67`) | 구현됨(공통 라우터 위임, 경로: `errorRouting.ts:116-117`, `serverError.ts:42-48`, `ConnectionWizardPage.tsx:21-28`, `ErrorMessage.tsx:39,50-53,60,66`) |
| 네트워크 단절 | `apiClient.beginConnection()` 호출 중 네트워크 오류(연결 실패, 타임아웃) — React Query mutation onError 콜백(`ConnectionWizardPage.tsx:68`)으로 err 전달. err는 ApiError가 아닌 표준 JavaScript Error일 수 있음(fetch 실패) | WizardActionError 렌더(`ConnectionWizardPage.tsx:188`). classifyForbidden(error) → false(`:20`). routeApiError(error) → errorCode 없음 → unknown 라우트(`errorRouting.ts:132`). ErrorMessage 렌더(`:23-28`). errorCode undefined → getApiErrorMessage(undefined, message)(`ErrorMessage.tsx:36`). classifyRetry(undefined) → "none"(`retryable.ts:41`) → canRetry=false. traceId 없음. 재시도 버튼 미표시 | 연결하기 버튼 재클릭(`:193`). 자동 재시도 없음 | 구현됨(`ConnectionWizardPage.tsx:188,20,23-28,193`, `errorRouting.ts:132`, `ErrorMessage.tsx:36,39`, `retryable.ts:41`). 단 네트워크 오류 시 getApiErrorMessage가 어떤 문구를 반환하는지는 i18n 매핑 미확인 |
| 부분 실패(일부 데이터만 실패) | `useBeginConnection` mutation은 단일 API 호출(`useConnections.ts:23`) — 부분 실패 개념 없음. `beginConnection` 응답은 연결 요청 수락/거절의 이분 상태만 반환 | 해당 없음. `beginConnection`은 원자적 mutation이며 일부만 성공하는 사례가 없음 | 해당 없음 | 해당 없음(`useBeginConnection`은 단일 mutation, 성공/실패 이분 경로만, 경로: `useConnections.ts:22-25`) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-ConnectionWizardPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`:89`) — "연결 목록으로 이동" | `succeeded === true` (`:85`) — `handleSubmit()` 제출 후 `onSuccess` (`:67`)가 `setSucceeded(true)`를 호출한 후 | `succeeded` 상태가 true이므로 `Alert tone="success"` (`:86`)와 "연결 요청을 보냈습니다…" 문구(`:87`)가 이미 표시됨. 버튼은 `Link` 컴포넌트(`:88`)로 `/settings/connections` 페이지로 이동 | `Link` 컴포넌트는 동기 탐색이므로 실제 실패 경로 없음. `/settings/connections` 라우트의 정의 위치는 미확인 | `/settings/connections` (`Link` 컴포넌트 사용, 라우트 정의 미확인) |
| `<Button>` (`:131`) — "다음" (provider 단계) | `providerStepValid === true` (`:56`) — `providerCode.trim().length > 0`, `opaqueAccountRef.trim().length > 0`, `capabilityProfile.length > 0` (권한 1개 이상 선택) | `step`이 `"permissions"`로 변경 (`:133` — `setStep("permissions")`) | 입력 검증 실패 시 버튼이 `disabled={!providerStepValid}` (`:131`)로 비활성화되어 클릭 불가. 사용자 피드백 없음(비활성화 상태만) | permissions 단계 (`:134-166`) |
| `<Button>` (`:159`) — "이전" (permissions 단계) | `step === "permissions"` (`:134`), `readonlyConfirmed` 체크박스 상태와 무관 | `step`이 `"provider"`로 변경 (`:159` — `onClick={() => setStep("provider")}`) | 없음 (순수 상태 전이, side-effect 없음) | provider 단계 (`:96-133`) |
| `<Button>` (`:162`) — "다음" (permissions 단계) | `step === "permissions"` (`:134`), `readonlyConfirmed === true` (`:151` 체크박스 확인) | `step`이 `"review"`로 변경 (`:162` — `onClick={() => setStep("review")}`) | 체크박스가 미확인 시 버튼이 `disabled={!readonlyConfirmed}` (`:162`)로 비활성화되어 클릭 불가. 사용자 피드백 없음(비활성화 상태만) | review 단계 (`:167-198`) |
| `<Button>` (`:190`) — "이전" (review 단계) | `step === "review"` (`:167`), `succeeded === false` (`:85` 조건 바깥) | `step`이 `"permissions"`로 변경 (`:190` — `onClick={() => setStep("permissions")}`) | 없음 (순수 상태 전이, side-effect 없음) | permissions 단계 (`:134-166`) |
| `<Button>` (`:193`) — "연결하기" (review 단계 제출) | `step === "review"` (`:167`), `beginConnection.isPending === false` (`:193` — `loading={beginConnection.isPending}`으로 비활성화 방지), `submitError === null` 또는 이전 오류 상태 | `handleSubmit()` (`:58-71`) 호출 → `beginConnection.mutate()` (`:60`) 실행 → `onSuccess` (`:67`) → `setSucceeded(true)` → 성공 Alert 표시 (`:85-93`) | `onError` (`:68`) → `setSubmitError(err)` (`:68`) → review 단계에서 `submitError` truthy (`:188`) → `<WizardActionError error={submitError} />` 렌더 (`:188`). `WizardActionError` (`:19-30`) — `classifyForbidden(error)` 참이면 `<ForbiddenNotice>` (`:20`), 아니면 `<ErrorMessage>` (`:23-28`) | 상태 유지 (review 단계, `:167-198`). `succeeded`는 false 유지, 재시도는 동일 버튼 클릭 |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-ConnectionWizardPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음 — 이 화면은 공급자 계정 연결 요청(onboarding) 전용이며, 주문·실행 생성·LIVE 전환·위임장 변경·출금 등 금전·위험 행동이 없음. 선택 가능한 권한은 `READ_BALANCE`/`READ_POSITION`/`READ_ACTIVITY` 세 가지 읽기 전용 스코프뿐(`ConnectionWizardPage.tsx:12`). i18n `connectWizard.steps.permissions.readonlyNotice`가 명시적으로 "주문·출금 권한은 요청되지 않습니다"(`:141`)고 안내. `useBeginConnection` mutation(`useConnections.ts:20-25`)은 연결 요청 생성 API(`apiClient.beginConnection`) 호출만 수행하고, 주문/실행/출금 등 금전 동작을 포함하지 않음. |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-ConnectionWizardPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:20`, `shared-types/src/forbidden.ts:31`) | 판별 함수 — 직접 문구 없음. `shared-types/src/forbidden.ts:31`에서 403 응답의 `errorCode`를 네 갈래(`mfa_required`/`tenant_mismatch`/`policy`/`forbidden`)로 분류. 분류 결과에 따라 `ForbiddenNotice` 또는 `routeApiError` 경로로 분기. | — | 아니오 |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:20`, `frontend/apps/web/src/components/ForbiddenNotice.tsx:31`, `shared-types/src/forbidden.ts:33`, `shared-types/src/apiError.ts:106`) | `getApiErrorMessage(errorCode, message)` — `shared-types/src/apiError.ts:106`. 403 오류 코드별 고정 메시지(EXACT_MESSAGES/PREFIX_MESSAGES). `policy` 갈래: `DenialReasons` 컴포넌트에서 상세 사유 표시(`ForbiddenNotice.tsx:41`). `mfa_required` 갈래: `legacy.forbiddenNotice.t1` ("Step-up authentication" / "step-up 인증") 버튼(`:45`). 나머지 갈래: 재시도 버튼 없음(반복 결과). | `policy`: 사유 목록 확인 후 공급자/테넌트 설정 변경 필요. `mfa_required`: "step-up 인증" 버튼 클릭 → MFA 추가 인증으로 리디렉션(`onStepUp` 콜백, `:42`). `tenant_mismatch`/`forbidden`: 설정 변경 후 재시도. | 아니오 — `ForbiddenNotice`는 traceId 필드를 받지 않음 |
| `routeApiError` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:21`, `shared-types/src/errorRouting.ts:88`) | 라우팅 함수 — 직접 문구 없음. `shared-types/src/errorRouting.ts:88`에서 idempotency/`field_errors`/mfa_invalid/`forbidden`/data_coverage/wallet_drift 등 `RoutedApiError.kind`로 분류. `backoff_retry`일 때 `afterSec` 포함. | 분류 결과에 따라 `ErrorMessage` 컴포넌트에서 메시지 렌더. `backoff_retry`: 카운트다운 후 재시도 버튼 활성화. `field_errors`: 개별 필드 오류 배너가 담당. | 아니오 — `routeApiError` 자체는 traceId를 직접 전달하지 않음 |
| `ErrorMessage` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:23`, `frontend/apps/web/src/components/ErrorMessage.tsx:27`, `shared-types/src/errorRouting.ts:38`, `shared-types/src/apiError.ts:106`) | `getApiErrorMessage(errorCode, message)` — `shared-types/src/apiError.ts:106`. errorCode 매핑 실패 시 `"요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요."` (`DEFAULT_API_ERROR_MESSAGE`). traceId 표시: `legacy.errorMessage.t1` ("Support code: {{traceId}}" / "지원코드: {{traceId}}", `ErrorMessage.tsx:60`). 재시도 카운트다운: `legacy.errorMessage.t2` ("Retry available in {{remainingSec}}s" / "{{remainingSec}}초 후 재시도 가능", `:64`). 재시도 버튼: `legacy.errorMessage.t3` ("Retry" / "다시 시도", `:66`). | `backoff_retry`(예: RATE_LIMIT_EXCEEDED): 카운트다운 후 재시도 버튼 활성화. `refetch`(예: STATE_CONCURRENCY_CONFLICT): 즉시 재시도 가능. `field_errors`: `hasMappedFieldErrors` 시 배너 자체를 숨김(중복 방지, `:55`). 그 외: 즉시 재시도 가능. | 예 — `traceId` prop으로 받아 `<p className="mt-1 text-xs text-fg-muted">`에 표시(`:60`). `ApiError.traceId`가 있을 때만 노출 |
| `Alert` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:86`) | `connectWizard.steps.review.success` ("Connection request sent. Check its status under Settings > Connections." / "연결 요청을 보냈습니다. 설정 > 연결에서 상태를 확인하세요."). `tone="success"` (`:86`). | 하단 `Link to="/settings/connections"` + `connectWizard.steps.review.goToConnections` ("Go to connections" / "연결 목록으로 이동") 버튼 클릭 → `/settings/connections` 이동. | 아니오 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-ConnectionWizardPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-ConnectionWizardPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-ConnectionWizardPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
