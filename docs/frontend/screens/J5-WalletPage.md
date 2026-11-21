# J5-WalletPage — `/wallet`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/WalletPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/wallet` / `frontend/apps/web/src/routes/wallet/WalletPage.tsx`
- 여정 소속: J5


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J5-WalletPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:22` 표 P1 행("J1~J6 전체 [확인]"). 운영자 측 충전 승인(`/admin/wallet-topups`)은 P8 몫(`router.tsx:161`). |
| 목적(한 문장) | 보유 크레딧을 확인하고 충전 금액을 신청하면, 입금 후 관리자 확인으로 크레딧이 반영된다. (`WalletPage.tsx:48-51` 잔액 표시, `:58-84` 신청 폼) |
| 진입 조건 | 1) 라우트 가드: `router.tsx:141`이 `protect(<WalletPage />)`로 `/wallet`를 감싼다. `protect`는 `router.tsx:70-72`에서 `ProtectedRoute`로 감김. 2) 플래그 조건: 없음. 3) 토큰 없으면 `/login?next=...`로 리다이렉트: `ProtectedRoute.tsx:20-21`. 4) MFA 미완료 시 `/onboarding/mfa-setup`로 강제: `ProtectedRoute.tsx:32-34`. 5) 적합성평가 미완료 시 `/onboarding/risk-assessment`로 강제: `ProtectedRoute.tsx:35-41`. |
| 사용자 준비물 | 1) 로그인 세션(토큰 보유). 2) MFA 설정 완료(`ProtectedRoute.tsx:32`). 3) 적합성평가 완료(`ProtectedRoute.tsx:35`). 4) 충전 금액: 정수, 최소 1(`WalletPage.tsx:62`), 기본값 30000(`WalletPage.tsx:16`). 1크레딧 = 1원(`WalletPage.tsx:58`). 5) 실제 계좌 입금: 문구 `WalletPage.tsx:69`("신청 후 실제 계좌로 입금하면 관리자 확인 즉시 크레딧이 반영됩니다"). 입금 계좌 정보 출처: 미확인. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J5-WalletPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useWalletBalance` | `frontend/packages/shared-hooks/src/useWallet.ts:5` | `useQuery`(`useWallet.ts:6-9`): 1회 조회 후 staleTime 기본값(5분)까지 재페치 없음. `refetchOnWindowFocus: true` 기본 동작으로 창 포커스 시 재조회. API: `GET wallet.balance`(frontend/packages/api-client/src/clients/portfolio.ts:35) | `useQuery`의 `isLoading` 플래그로 `Stat` 컴포넌트에서 "…" 표시(`WalletPage.tsx:51`). 네트워크/5xx 실패 시 기존 캐시 데이터 유지(React Query 기본). 401 시 `ProtectedRoute`가 `/login` 리다이렉트(`router.tsx:141`). `INTEGRITY_WALLET_BALANCE_DRIFT`(409)는 `routeApiError`가 `{kind:"wallet_balance_drift"}`로 분류(frontend/packages/shared-types/src/errorRouting.ts:107) — 현재 화면에서는 `error` 상태로 전달되지 않음(본인 훅의 실패가 아님). |
| `useRequestTopup` | `frontend/packages/shared-hooks/src/useWallet.ts:12` | `useMutation`(`useWallet.ts:14-18`): 변경 요청(POST). API 경로 `wallet.topupRequests`(frontend/packages/api-client/src/clients/portfolio.ts:39). 성공 시 `onSuccess`로 `qc.invalidateQueries({queryKey:["walletBalance"]})`(`useWallet.ts:17`) — 낙관적 업데이트 대신 서버 확인 후 쿼리 무효화. 호출부는 `useIdempotentSubmit`이 `Idempotency-Key` 헤더 관리. API: `POST wallet.topupRequests`, 바디 `{amount}`, idempotencyKey 필수 헤더. | `mutateAsync`가 throw하므로 `WalletPage.tsx:28-31`의 catch에서 `DuplicateSubmitError`는 silently ignore, 그 외는 `ApiError` 또는 `Error`로 `setError`(`WalletPage.tsx:30`). `ErrorMessage` 컴포넌트(`WalletPage.tsx:71-77`)가 `errorCode`/`message`/`traceId`/`retryAfterSec`를 받아 표시. `routeApiError` 분류 결과에 따라 `canRetry`(429 backoff) 시 재시도 버튼 활성화(`WalletPage.tsx:42-43`). `INTEGRITY_IDEMPOTENCY_CONFLICT`(409)는 `useIdempotentSubmit`이 `classifyIdempotencyFailure`로 "new_key" 판정 후 키 폐기+재제출 금지(frontend/packages/shared-types/src/idempotencyFailure.ts:35, frontend/apps/web/src/hooks/useIdempotentSubmit.ts:58). |
| `useIdempotentSubmit` | `frontend/apps/web/src/hooks/useIdempotentSubmit.ts:30` | 변경 요청 래퍼: 1회성 토큰 수명주기 관리(`useIdempotentSubmit.ts:31-32`). `requestKey="wallet.topup"` 고정. 성공 시 `discardKey`(`useIdempotentSubmit.ts:43`), 4xx 실패 시 `isNonRetryableFailure` 판정으로 `discardKey`(`useIdempotentSubmit.ts:58-59`). in-flight 가드로 버튼 연타 방지(`useIdempotentSubmit.ts:36-38`). 재시도 가능(5xx/네트워크)은 같은 키 유지. | `DuplicateSubmitError` throw(`useIdempotentSubmit.ts:37`) — 호출부(`WalletPage.tsx:29`)에서 `return`으로 silent ignore. `isNonRetryableFailure`(4xx) 또는 `classifyIdempotencyFailure`가 "none" 아님 → 키 폐기 후 `throw err`(`useIdempotentSubmit.ts:58-61`). `missing_header`(400)는 `console.warn`(`useIdempotentSubmit.ts:53-56`) — 자동 재제출 없음. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J5-WalletPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩(잔액 조회 중) | `useWalletBalance().isLoading`이 true (`WalletPage.tsx:13` 구조분해, `:51` 조건) | 라벨 `legacy.walletPage.label2` / "보유 크레딧"(`:50`), 값 `"…"`(`:51`). "…"은 사실 시트 키 목록에 없음(시트 밖 문자). | 신청 폼은 그대로 렌더됨(`:54-85`). 금액 입력·제출 가능. | 미확인(분기는 `:51`에 있으나 "…" 문구가 사실 시트에 없음). |
| 로딩(제출 중) | `requestTopup.isPending`이 true (`WalletPage.tsx:83` loading prop) 또는 in-flight 가드(`useIdempotentSubmit.ts:36-38`) | 버튼 `legacy.walletPage.t6` / "충전 신청"(`:84`)이 spinner 표시(Button.tsx:47-49). disabled(`Button.tsx:39`). | 버튼 연타는 DuplicateSubmitError로 조용히 무시(`WalletPage.tsx:29`, `useIdempotentSubmit.ts:37`). | 구현됨(`:83`, `:29`, `useIdempotentSubmit.ts:36-39`). |
| 빈 상태(잔액 0/null) | `balance?.balance`가 nullish(`WalletPage.tsx:51`, 폴백 `"0"`) | 라벨 "보유 크레딧"(`:50`), 값 `"0 크레딧"`. ` 크레딧` 접미사는 하드코딩(`:51`), 키 없음. | 폼 입력·제출 가능. | 구현됨(표시 자체는 `:51`). 빈 상태 안내 문구: 사실 시트에 없음 → 없음. 목록 없음 → 해당 없음. 주의: `useWalletBalance`의 `error`를 구조분해하지 않으므로(`:13`) 조회 실패도 "0 크레딧"으로 보인다. 빈 상태와 실패 구분 불가. |
| 정상(잔액 로드 완료) | `isLoading === false`이고 `data` 존재 | 제목 `legacy.walletPage.title1` / "지갑"(`:48`). 라벨 `label2`(`:50`), 값 `"{balance} 크레딧"`(`:51`). 금액 필드 라벨 `label3`(`:58`), 기본값 `"30000"`(`:16`), 최소 1(`:62`). 안내 `t4`(`:69`). 버튼 `t6`(`:84`). | 금액 입력 → 제출(`:34-37`). | 구현됨(위 줄 인용). |
| 정상 추가(제출 성공 직후) | `submit` 성공 후 `setSubmitted`(`WalletPage.tsx:27`) | 성공 알림 `legacy.walletPage.t5` / "충전 요청 #{{id}} 접수됨 ({{amount}} 크레딧) — 관리자 확인 대기 중"(`:79-82`). | 다시 신청 가능. 잔액 재조회: `useWallet.ts:17`의 `invalidateQueries`. | 구현됨(`:27`, `:79-82`, `useWallet.ts:17`). |
| 낡은 데이터(신선도 초과) | 캐시된 값이 staleTime을 넘었으나 `refetchOnWindowFocus: true` 기본 동작으로 창 포커스 시 재조회(`useWallet.ts:6-9`) | 캐시된 값 그대로 표시(`:51`). 낡음 표시 문구: 사실 시트에 없음. | 창 포커스 시 자동 재조회(React Query 기본). 제출 성공 후 무효화 재조회(`useWallet.ts:17`). | 낡음 표시: 없음. staleTime: `useWallet.ts`에 설정 없음, `main.tsx:15`도 `new QueryClient()` 기본값. data 블록(27줄)의 "staleTime 기본값(5분)" 주장은 TanStack 기본값(0)과 다름 — data 블록 오류 의심. |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J5-WalletPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `<Input type="number" min="1" required>` — 브라우저 유효성 검사 (`WalletPage.tsx:59-66`). 서버 400 응답 시 `routeApiError`가 `{kind:"field_errors"}` 반환(`errorRouting.ts:87-88`) | 브라우저 기본 오류 툴팁 (예: "숫자를 입력하세요", "1 이상의 값을 입력하세요"). 서버 400 시 `ErrorMessage` 배너(`WalletPage.tsx:71-77`) — `fieldErrors` 추출 안 함(`ErrorMessage.tsx:55`에서 `hasMappedFieldErrors` 체크 없음). | 필드에 올바른 값 입력 후 재제출. 서버 400 시 재시도 버튼 없음 (`classifyRetry`에서 `kind:"none"`). | 구현됨 (브라우저 네이티브). 서버 400 응답 시 `ErrorMessage` 배너만 표시, 필드별 오류 안내 없음. |
| 인증 필요/세션 만료(401) | 토큰 만료·부재 — `isSessionExpiredErrorCode`(`errorRouting.ts:128-129`)가 `auth_required` 반환 | — (`ProtectedRoute`가 `/login?next=...`로 리다이렉트) — WalletPage 렌더링 안 됨 | `/login`에서 재로그인 후 원 경로 복귀 | 없음 (`router.tsx:141`의 `protect(<WalletPage />)` → `ProtectedRoute.tsx:20-21`이 401을 가로챔). `WalletPage.tsx:11-31`에서 401을 처리하지 않음. |
| 권한 없음/MFA 필요(403) | `routeApiError`가 `{kind:"mfa_required"|"tenant_mismatch"|"policy_denied"|"forbidden"}` 반환 (`errorRouting.ts:98-102`) | `ErrorMessage` 배너 (`WalletPage.tsx:71-77`) — `getApiErrorMessage`가 errorCode→메시지 매핑 (`ErrorMessage.tsx:36`) | 재시도 버튼 없음 (`canRetry=false`, 403은 `classifyRetry`에서 `kind:"none"`) | 구현됨. `ErrorMessage`가 에러 바너 표시. 403 하위 분류(mfa_required 등)에 따른 별도 UI(예: MFA 설정 페이지 링크)는 없음. |
| 충돌/상태 불일치(409) | `INTEGRITY_IDEMPOTENCY_CONFLICT` — `classifyIdempotencyFailure`가 `"new_key"`(`idempotencyFailure.ts:35`). `useIdempotentSubmit`이 키 폐기 후 throw(`useIdempotentSubmit.ts:58-59`). `DATA_COVERAGE_MISSING` — `{kind:"data_coverage_missing"}`(`errorRouting.ts:104-105`) | `ErrorMessage` 배너(`WalletPage.tsx:71-77`). `routeApiError`가 `{kind:"idempotency_new_key"}` 또는 `{kind:"data_coverage_missing"}`(`errorRouting.ts:84,104-105`). 재시도 버튼 없음 (`canRetry=false`) | 새 Idempotency-Key 필요 — 사용자가 금액 수정 후 재제출. DATA_COVERAGE_MISSING은 재시도 없음(수집 후 재요청) | 구현됨 (키 폐기+에러 표시). "새 키 발급" 안내 문구 없음. DATA_COVERAGE_MISSING 안내는 `getApiErrorMessage`가 errorCode 매핑으로 제공(`ErrorMessage.tsx:36`). |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J5-WalletPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | `routeApiError(error)`가 `{kind:"backoff_retry"}` 반환 (`errorRouting.ts:121`, `WalletPage.tsx:43`). 423(잠김)은 `deriveLockout`(`errorRouting.ts:125-126`)이 `{kind:"account_locked", retryAfterSec}` 반환 | `ErrorMessage` 배너(`WalletPage.tsx:71-77`)가 `retryAfterSec` 값을 받아 초 단위 카운트다운 표시(`ErrorMessage.tsx:49-53`). 버튼은 `remainingSec > 0` 시 비활성화(`ErrorMessage.tsx:66`) | `ErrorMessage` 내 재시도 버튼(`ErrorMessage.tsx:66-67`) 클릭 시 `attemptTopup()` 재호출(`WalletPage.tsx:76`). `canRetry`는 `routed?.kind === "backoff_retry"`(`WalletPage.tsx:43`) 판정. 423(account_locked)은 재시도 불가(`classifyRetry` → `kind:"none"`) | 구현됨. 429(`RATE_LIMIT_EXCEEDED`)는 `BACKOFF_ERROR_CODES`(retryable.ts:27)에 등록되어 `classifyRetry`(retryable.ts:40)가 `kind:"backoff"` 반환. `ErrorMessage.tsx:38-40`에서 판별, 카운트다운 후 재시도 버튼 활성화. 423은 `deriveLockout`(errorRouting.ts:125)가 `account_locked` 분류 — 재시도 불가 |
| 서버 오류(5xx) | `routeApiError(error)`가 `{kind:"server_fatal", traceId:?}` 또는 `{kind:"backoff_retry"}` 반환 (`errorRouting.ts:116-117`). 502/503/500 중 `BACKOFF_ERROR_CODES`(retryable.ts:27)에 등록된 코드만 재시도 가능 | `ErrorMessage`(ErrorMessage.tsx:36)가 `getApiErrorMessage`(shared-types/src/apiError.ts:106)로 메시지 변환. `traceId` 있으면 `legacy.errorMessage.t1`(ErrorMessage.tsx:60)로 노출. `server_fatal`은 재시도 불가(`classifyRetry` → `kind:"none"`) | `server_fatal`은 재시도 버튼 미노출(`ErrorMessage.tsx:39: canRetry=false`). `backoff_retry`(502/503 중 재시도 가능 코드)는 429와 동일 카운트다운 재시도 | 구현됨. `errorRouting.ts:115-117`에서 `classifyServerError` 결과에 따라 `server_fatal`/`backoff_retry` 분류. `classifyServerError`(serverError.ts)가 502/503/500 중 재시도 가능 코드 집합 정의 |
| 네트워크 단절 | `requestTopup.mutateAsync`가 네트워크 연결 실패 시 throw (`useWallet.ts:16`, `WalletPage.tsx:25`) | `err instanceof ApiError`가 false이므로 `new Error(t("legacy.walletPage.t7"))`로 래핑(`WalletPage.tsx:30`). `ErrorMessage`가 일반 메시지 표시 | `routeApiError`는 `ApiError`가 아닌 `Error`를 받으면 `{kind:"unknown"}` 반환(`errorRouting.ts:86`). 재시도 불가(`canRetry=false`). 같은 `idempotencyKey` 유지(재시도 가능 가정, `useIdempotentSubmit.ts:46`) | 구현됨. 네트워크 오류는 `ApiError`가 아니므로 `t("legacy.walletPage.t7")`("충전 요청에 실패했습니다.") 표시. `useIdempotentSubmit`이 같은 키 유지하여 재시도 허용(5xx/네트워크는 재시도 가능 가정) |
| 부분 실패(일부 데이터만 실패) | `useWalletBalance`는 성공했으나 `useRequestTopup`이 실패(`WalletPage.tsx:13-14` 독립 훅). 또는 그 반대 | 잔액 `Stat`은 정상 표시(`:51`). 폼 아래 `ErrorMessage`가 오류 표시(`:71-77`). `submitted` 상태 `null`(`:27`) | 버튼은 `requestTopup.isPending`이 false면 재제출 가능(`:83`). `setError`로 오류 상태 유지(`:30`). 잔액 조회 실패는 `Stat`에서 "…" 표시(`:51`) | 구현됨. `useQuery`/`useMutation`은 독립적(`useWallet.ts:5-18`). 잔액 조회 실패도 `WalletPage.tsx:13`에서 `error` 구조분해하지 않으므로("0 크레딧" 또는 "…"으로 보인다 — st-a 블록 39줄 참고). 톱업 실패만 `ErrorMessage` 표시 |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-WalletPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/wallet/WalletPage.tsx:83`) — "충전 신청" (i18n: `legacy.walletPage.t6`) | 1) `<form>`이 `onSubmit={handleSubmit}`(`frontend/apps/web/src/routes/wallet/WalletPage.tsx:55`)으로 감싼 상태. 2) `<Input>`(`:59-66`)이 `required min="1" type="number"`로 브라우저 검증 통과. 3) `amount` 상태(`:16`)가 빈 문자열이 아님. 4) `useIdempotentSubmit`이 `inFlightRef.current === false`(`frontend/apps/web/src/hooks/useIdempotentSubmit.ts:36`) — 버튼 연타 방지 가드 통과. | 1) `handleSubmit`(`:34-37`)이 `attemptTopup`(`:20-32`) 호출. 2) `submit(idempotencyKey)`(`:24`)가 `requestTopup.mutateAsync({body:{amount}, idempotencyKey})`(`:25`)로 `POST wallet.topupRequests` 전송. 3) `mutateAsync` 성공 시 `result.id`/`result.requestedAmount`로 `setSubmitted`(`:27`). 4) `useRequestTopup`의 `onSuccess`(`frontend/packages/shared-hooks/src/useWallet.ts:17`)가 `qc.invalidateQueries(["walletBalance"])` 호출 — 잔액 자동 갱신. 5) `Alert(tone="success")`(`:79-81`)가 `t("legacy.walletPage.t5", {id, amount})` 표시. | 1) `catch`(`:28`)에서 `DuplicateSubmitError`(`frontend/apps/web/src/hooks/useIdempotentSubmit.ts:37`)면 `return`(`:29`) — silently ignore. 2) 그 외: `setError(err)`(`:30`). `ApiError`면 원본 유지, 그 외면 `t("legacy.walletPage.t7")`("충전 요청에 실패했습니다.") 생성. 3) `routeApiError(error)`(`:42`)로 분류 — `routed.kind === "backoff_retry"`(`:43`) 시 `canRetry=true`(`:43`). 4) `ErrorMessage`(`:71-77`)가 `errorCode`/`message`/`traceId`/`retryAfterSec` 표시. 5) `canRetry`면 `onRetry={() => attemptTopup()}`(`:76`) 활성화 — 재시도 버튼. | 동일 화면 `/wallet` 유지. 화면 이동(navigate/router.push) 호출 없음. 성공 시 Alert(`frontend/packages/ui-web/src/Feedback.tsx:4`) 표시, 실패 시 ErrorMessage 표시 — 모두 같은 화면 내 상태 변화. |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-WalletPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-WalletPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`frontend/apps/web/src/routes/wallet/WalletPage.tsx:42`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/wallet/WalletPage.tsx:71`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/wallet/WalletPage.tsx:80`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-WalletPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-WalletPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-WalletPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
