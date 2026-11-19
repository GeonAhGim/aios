# J1-LoginPage — `/login`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/LoginPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/login` / `frontend/apps/web/src/routes/auth/LoginPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-LoginPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — `PERSONAS_AND_JOURNEYS.md` §2 P1, §3 여정 J1 "시작·온보딩" [확인] |
| 목적(한 문장) | 이메일·비밀번호(+MFA)로 인증해 AIOS 세션을 시작하고, `?next`로 저장된 원경로 또는 `/dashboard`로 이동 [확인] |
| 진입 조건 | 비로그인 상태 — `router.tsx:86` `/login` 라우트는 `protect()`/`FeatureFlagGate` 없이 직접 렌더, `ProtectedRoute.tsx:20~21` 토큰 없으면 `/login?next=...`로 리다이렉트 [확인] |
| 사용자 준비물 | 계정(이메일+비밀번호 설정 완료), MFA 설정(첫 로그인 시 `ProtectedRoute.tsx:32~33` 이후 온보딩 강제) [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-LoginPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useLogin` | `frontend/packages/shared-hooks/src/useAuth.ts:32` | `useMutation` 기반 변경 요청 — 사용자가 제출할 때만 `mutateAsync`로 1회 실행(:43). 자동 폴링(refetchInterval), staleTime 모두 없음(:35~41). 훅에 `retry` 옵션이 명시되지 않아 자동 재시도는 미확인 | 호출 쪽은 `frontend/apps/web/src/routes/auth/LoginPage.tsx:45`, 오류 표시는 `frontend/apps/web/src/components/ErrorMessage.tsx:1`을 본다. `attemptLogin`의 `catch`(:45~48): `ApiError`면 그대로 `setError`(:46), 아니면 `t("legacy.loginPage.t9")` 문구의 `Error`로 감싸 저장. `deriveLockout(err).retryAfterSec`를 `lockoutRemainingSec`에 넣음(:47). 값 > 0이면 입력창·버튼이 `disabled={locked}`(:73, :84, :94, :111)로 비활성화되고 카운트다운 안내(:107~110)가 뜬다. `ErrorMessage`(:98~106)가 `errorCode`, `message`, `traceId`를 표시하고, 429(`backoff_retry`)일 때만 `retryAfterSec`/`onRetry` 재시도 버튼 활성화(:103~104). 성공 시 `navigate(sanitizeNextPath(...))`(:44)로 `?next` 쿼리 또는 `/dashboard`로 이동. 실패 시 `navigate` 호출 없음. `routeApiError`(60)가 오류 분류를 담당. `ApiError` 생성 경로, HTTP 오류 매핑, `deriveLockout` 판정 기준 — 미확인 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-LoginPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `login.isPending === true` — `mutateAsync`가 실행 중(`useAuth.ts:35~41` mutation) | Submit 버튼에 `loading` 스피너 표시(`LoginPage.tsx:111`), 폼 입력창은 `locked`가 아니면 활성화 상태 유지 | 버튼 클릭 무시 — React Query mutation 중 중복 제출 방지 | 구현됨 (`LoginPage.tsx:111` — `loading={login.isPending}`) |
| 빈 상태 | 초기 마운트 — 모든 `useState("")`(`LoginPage.tsx:21~23`) | 빈 이메일/비밀번호/MFA 입력창, 제목 "AIOS 로그인"(`:64`), 회원가입 링크(`:114~116`) | 입력 후 제출 | 구현됨 (초기 상태 `:21~23`, 모든 라벨/버튼 렌더링 `:64~117`) |
| 정상 | 로딩 없음, 에러 없음, 잠김 없음 — `locked === false`, `error === null`, `login.isPending === false` | 입력 가능한 이메일(`:66`)/비밀번호(`:77`)/MFA(`:88`) 필드, "로그인" 버튼 활성화(`:111~112`), "계정이 없으신가요? 회원가입" 링크(`:113~117`) | 이메일·비밀번호·MFA(선택) 입력 후 로그인 버튼 클릭 | 구현됨 (`handleSubmit` `:51~55`, `attemptLogin` `:40~49`, 전체 폼 `:64~117`) |
| 낡은 데이터(신선도 초과) | `useLogin`은 `useMutation` 기반(`useAuth.ts:35`), refetchInterval/staleTime 옵션 없음(`:35~41`) | 없음 — 신선도 메커니즘 없음 | 없음 | 없음 (mutation 기반 훅이므로 stale 데이터 개념 적용 대상 아님) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-LoginPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 브라우저 기본 검증: `email`(`LoginPage.tsx:70`), `password`(`LoginPage.tsx:81`)에 `required` 속성 → 빈 값 제출 시 브라우저가 제출을 막음, 화면 코드의 고유 처리 없음. TOTP(`LoginPage.tsx:88~97`)는 `required` 없이 `hint="설정한 경우만 입력"`(하드코딩)만 있음. 서버 400 응답 중 `AUTH_MFA_INVALID`(errorCode)는 `routeApiError`(`shared-types/errorRouting.ts:98`)에서 `mfa_invalid`로 분류됨 | `routeApiError`(`LoginPage.tsx:60`) → `ErrorMessage`(`LoginPage.tsx:98~106`) 경로. `AUTH_MFA_INVALID`일 때 `ErrorMessage.tsx:36`의 `getApiErrorMessage`가 `EXACT_MESSAGES["AUTH_MFA_INVALID"]`("인증 코드가 올바르지 않습니다. 다시 시도해주세요.")를 반환 [확인]. 그 외 400(errorCode 없음)은 `LoginPage.tsx:46`의 `legacy.loginPage.t9`("로그인에 실패했습니다.") [확인] | 제출 재시도 | 부분(`AUTH_MFA_INVALID`만 `getApiErrorMessage`로 매핑됨, 경로:`LoginPage.tsx:60`→`ErrorMessage.tsx:36`→`apiError.ts:66`. 그 외 400은 `legacy.loginPage.t9`로 감김. 브라우저 기본 검증(`LoginPage.tsx:70`,`:81`)은 화면 고유 처리 없음) |
| 인증 필요/세션 만료(401) | `routeApiError`(`shared-types/errorRouting.ts:128~130`)가 `AUTH_REQUIRED`/`AUTH_INVALID_CREDENTIALS`/`AUTH_TOKEN_EXPIRED`/`AUTH_TOKEN_INVALID`/`AUTH_SESSION_REVOKED`를 `isSessionExpiredErrorCode`로 판정, `auth_required`로 분류. 위 파일(`LoginPage.tsx:40~49`)에 401 전용 분기 없음 | 위 파일에 401 전용 분기 없음. `routeApiError`(:60) 결과와 무관하게 `ErrorMessage`(:98~106)에 `errorCode`/`message` 전달. `ErrorMessage.tsx:36`의 `getApiErrorMessage`가 `EXACT_MESSAGES` 매핑(예: `AUTH_INVALID_CREDENTIALS`→"이메일 또는 비밀번호가 올바르지 않습니다.", `AUTH_TOKEN_EXPIRED`→"세션이 만료되었습니다. 다시 로그인해주세요.")을 반환 [확인]. 세션 만료 시 실제 리다이렉트는 `ProtectedRoute.tsx:20~21`(`!token` → `/login?next=...`)에서 발생, 로그인 화면이 401을 직접 처리하지 않음 [확인] | 제출 재시도 | 부분(`ErrorMessage`가 `getApiErrorMessage`로 errorCode 매핑 표시함, 경로:`LoginPage.tsx:60`→`:98~106`→`ErrorMessage.tsx:36`→`apiError.ts:59~63`. 전용 401 분기 없음. 세션 만료 리다이렉트는 `ProtectedRoute.tsx:20~21`) |
| 권한 없음/MFA 필요(403) | `forbidden.ts:32~40`: 403 응답을 `mfa_required`(AUTH_MFA_REQUIRED), `tenant_mismatch`(AUTH_TENANT_MISMATCH), `policy`(POLICY_*/RISK_*), `forbidden`(기타 AUTHZ_*)로 분류. `routeApiError`(`errorRouting.ts:96~102`)에서 위 갈래로 전달. 위 파일(`LoginPage.tsx:40~49`)에 403 전용 분기 없음 | 위 파일에 403 전용 분기 없음. `routeApiError`(:60) 결과와 무관하게 `ErrorMessage`(:98~106)에 전달. `ErrorMessage.tsx:36`의 `getApiErrorMessage`가 errorCode 매핑(예: `AUTH_MFA_REQUIRED`→"추가 인증이 필요합니다.", `AUTHZ_FORBIDDEN`→"이 작업을 수행할 권한이 없습니다.")을 반환 [확인]. TOTP 입력 필드(`LoginPage.tsx:88~97`)는 항상 노출, 조건부 표시 없음 | 제출 재시도 | 부분(`ErrorMessage`가 errorCode 매핑 표시함, 경로:`LoginPage.tsx:60`→`:98~106`→`ErrorMessage.tsx:36`→`apiError.ts:65~68`. 403 전용 분기 없음. MFA 필요 시 조건부 UI 전환 없음) |
| 충돌/상태 불일치(409) | `errorRouting.ts:111~113`: `classifyStateConflict`가 409를 `refetch_retry`(STATE_CONCURRENCY_CONFLICT), `invalid_transition`(STATE_INVALID_TRANSITION)으로 분류. `data_coverage_missing`(DATA_COVERAGE_MISSING, errorRouting.ts:104~106), `wallet_balance_drift`(INTEGRITY_WALLET_BALANCE_DRIFT, :107~109), `idempotency_new_key`(INTEGRITY_IDEMPOTENCY_CONFLICT, :104)도 별도 갈래. 위 파일(`LoginPage.tsx:40~49`)에 409 전용 분기 없음 | 위 파일에 409 전용 분기 없음. `ErrorMessage`(:98~106)에 전달됨. `getApiErrorMessage`가 errorCode 매핑(예: `STATE_CONCURRENCY_CONFLICT`→"다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요.", `DATA_COVERAGE_MISSING`→"요청한 구간의 시장 데이터가 아직 수집되지 않았습니다(미커버 구간).")을 반환 [확인]. `routeApiError` 결과의 `kind`가 `backoff_retry`가 아니므로 재시도 버튼 비활성화 | 제출 재시도(버튼 비활성화됨) | 부분(`ErrorMessage`가 errorCode 매핑 표시함, 경로:`LoginPage.tsx:60`→`:98~106`→`ErrorMessage.tsx:36`→`apiError.ts:74~82`. 409 전용 분기 없음. `backoff_retry` 아님 → 재시도 버튼 비활성화) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-LoginPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 423 AUTH_ACCOUNT_LOCKED: `deriveLockout`(accountLockout.ts:41)이 `statusCode===423 && errorCode==="AUTH_ACCOUNT_LOCKED"`일 때 `locked=true, retryAfterSec=clampRetryAfterSec(err.retryAfterSec)` (기본 60초). `LoginPage.tsx:47`에서 `setLockoutRemainingSec`. 429 RATE_LIMIT_EXCEEDED: `routeApiError`(errorRouting.ts:119~121)가 `classifyRetry`로 `backoff_retry` 분류. | 입력창·버튼 `disabled={locked}`(:73,:84,:94,:111). `locked`일 때 `role="status"` 안내 문구(`legacy.loginPage.t5`) 표시(:107~110). 429일 때 `ErrorMessage` 재시도 버튼 활성화(`canRetry` :61, `retryAfterSec` :103). 423 잠금은 `deriveLockout`이 별도, 429는 `routeApiError`가 별도 — 겹치지 않음(`LoginPage.tsx:57~59` 주석 명시). | 제출 재시도(429 only, `ErrorMessage.tsx:66` — 카운트다운 후 활성화). 423은 카운트다운 후 자동 해제 | 구현됨 (423: `LoginPage.tsx:28,:32,:47,:107~110`, `deriveLockout` `accountLockout.ts:41`. 429: `routeApiError` `errorRouting.ts:119~121`, `ErrorMessage.tsx:66`) |
| 서버 오류(5xx) | 503 EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY: `classifyServerError`(serverError.ts:42~44)가 `retryable` → `routeApiError`(errorRouting.ts:116)가 `backoff_retry`. 502 EXCHANGE_FATAL/500 INTERNAL_ERROR: `classifyServerError`(serverError.ts:47~48)가 `fatal` → `routeApiError`(errorRouting.ts:117)가 `server_fatal`. | `ErrorMessage`(:99~106)가 `getApiErrorMessage`로 한국어 메시지 표시: 503 `EXCHANGE_UNAVAILABLE`→"거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요."(`apiError.ts:79`), `DEPENDENCY_NOT_READY`→"서비스가 준비 중입니다. 잠시 후 다시 시도해주세요."(:81). 502 `EXCHANGE_FATAL`→"거래소 자격증명을 확인해주세요."(:80), 500 `INTERNAL_ERROR`→"일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."(:83). `server_fatal`은 traceId 표시(:102). `backoff_retry`(503 retryable)일 때만 재시도 버튼 활성화. | 재시도(503 only, `backoff_retry` 갈래). 502/500(fatal)은 재시도 불가 — `canRetry`가 false | 구현됨 (503: `classifyServerError` `serverError.ts:42~44`→`retryable`, `routeApiError` `errorRouting.ts:116`→`backoff_retry`, 재시도 버튼 `ErrorMessage.tsx:66`. 502/500: `serverError.ts:47~48`→`fatal`, `errorRouting.ts:117`→`server_fatal`, 재시도 불가) |
| 네트워크 단절 | fetch 자체가 실패하면(온라인 여부, DNS 실패, CORS 등) `LoginPage.tsx:45` catch에서 `err instanceof ApiError`가 false → `new Error(t("legacy.loginPage.t9"))`으로 감김(:46). `deriveLockout(err)`는 `locked=false, retryAfterSec=0` 반환(:47). `routeApiError`는 `errorCode`가 없는 `Error` 인스턴스를 받아 `isErrorCodeLike`(:80)가 false → `unknown` 분류. | `ErrorMessage`(:99~106)가 `error.message`(즉 `legacy.loginPage.t9` "로그인에 실패했습니다.") 표시. 재시도 버튼 없음(`canRetry` false). 잠금 UI 없음(`locked` false). traceId 없음. | 수동 재제출(사용자가 폼 다시 입력 후 버튼 클릭) | 구현됨 (네트워크 실패 → 일반 `Error`로 감김 `LoginPage.tsx:46`, `deriveLockout` `accountLockout.ts:47`→`locked:false`, `routeApiError` `errorRouting.ts:132`→`unknown`, 재시도 불가) |
| 부분 실패(일부 데이터만 실패) | 로그인 훅(`useLogin`, `useAuth.ts:32`)은 단일 `mutateAsync` 호출(`LoginPage.tsx:43`). 로그인 API가 단일 요청/응답 모델이므로 "일부 데이터만 실패"하는 시나리오가 존재하지 않음. `useLogin`에 부분 실패를 분기하는 로직 없음. | 없음 — 부분 실패 개념이 적용 대상 아님 | 없음 | 없음 (로그인은 단일 요청/응답 모델, 부분 실패 행 추가 불필요) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-LoginPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button type="submit">` (`LoginPage.tsx:111`) | `locked`가 false — `locked = lockoutRemainingSec > 0`(:32). `disabled={locked}`(:111). `loading={login.isPending}`으로 제출 중 스피너+비활성(`packages/ui-web/src/Button.tsx:39` `disabled={disabled || loading}`). 브라우저 `required`(`:70`, `:81`)가 빈 이메일·비밀번호 제출을 먼저 막음. Enter 키 제출도 form `onSubmit`(`:65`)으로 같은 경로 — Enter 동작 실측은 미확인 | `handleSubmit`(`:51~55`) → `attemptLogin`(`:40~49`) → `login.mutateAsync` 성공 후 `navigate(sanitizeNextPath(searchParams.get("next")))`(:44). `sanitizeNextPath`(`:14~17`)는 `next`가 `/`로 시작하고 `//`가 아니면 그 경로, 아니면 `/dashboard` | `catch`(`:45~48`): `error` 설정, `deriveLockout(err).retryAfterSec`를 `lockoutRemainingSec`(:47). `navigate` 호출 없음 → `/login` 머무름, `ErrorMessage`(:98~106) 표시. 429면 재시도 버튼 활성(`:103~104`), 423이면 카운트다운 후 입력·버튼 잠금 해제(`:34~38`, `:73`, `:84`, `:94`, `:111`) | 성공 → `?next` 경로 또는 `/dashboard`(`router.tsx:114` `protect(<DashboardPage />)`). 실패 → 동일 `/login` |
| `<Link to="/signup">` (`LoginPage.tsx:115`, 텍스트 `legacy.loginPage.t8` `:116`) | 항상 노출(조건 없음, `:113~117`) | 클릭 시 라우터 이동 — 제출 결과와 무관 | 없음(클릭 결과 없음) | `/signup`(`router.tsx:85`) |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-LoginPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-LoginPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`frontend/apps/web/src/routes/auth/LoginPage.tsx:60`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/auth/LoginPage.tsx:99`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-LoginPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-LoginPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-LoginPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
