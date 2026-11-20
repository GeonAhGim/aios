# J1-ExchangeManagementPage — `/exchanges`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ExchangeManagementPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/exchanges` / `frontend/apps/web/src/routes/exchanges/ExchangeManagementPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-ExchangeManagementPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:22`(P1 = 개인 트레이더(투자자), 핵심 과업: 전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독). 거래소 연동은 J1("시작·온보딩")의 선행 단계로 P1 전용 [확인] |
| 목적(한 문장) | 거래소 API 인증 정보를 등록·해지하고 연결된 거래소의 포트폴리오 현황을 확인하여 모의/실제 거래를 위한 데이터 경로를 확보한다 |
| 진입 조건 | `frontend/apps/web/src/router.tsx:115` — `/exchanges` 라우트. `protect()`(`router.tsx:70` → `ProtectedRoute`, `ProtectedRoute.tsx:9`) 감싸짐: (1) 인증 토큰 필요(`ProtectedRoute.tsx:20`), (2) `me` 클레임 존재(`ProtectedRoute.tsx:29`), (3) MFA 미활성 시 `/onboarding/mfa-setup`로 리다이렉트(`ProtectedRoute.tsx:32-34`), (4) MFA 활성화且 위험 프로필 미작성 시 `/onboarding/risk-assessment`로 리다이렉트(`ProtectedRoute.tsx:35-41`). 이 조건을 모두 통과한 인증 사용자만 접근 가능 [확인]. 기능 플래그 없음 [확인] |
| 사용자 준비물 | 로그인(토큰 확보), MFA 설정(또는 `/onboarding/mfa-setup` 완료), 위험 프로필 작성(MFA 활성화 시 필요, `ProtectedRoute.tsx:35-41`), 거래소 API 키/시크릿(또는 패스프레이즈 — 거래소별 상이, Bitget은 패스프레이즈 필요 `ExchangeManagementPage.tsx:82`) [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-ExchangeManagementPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useExchangeCredentials` | `frontend/packages/shared-hooks/src/useExchanges.ts:5` | `useQuery` — 1회 조회(폴링 옵션 미설정). 수동 `refetch()` 호출: 등록/해지 `onSuccess` 시 `qc.invalidateQueries({ queryKey: ["exchangeCredentials"] })`로 자동 refetch(`useExchanges.ts:16,24`), 화면에서 수동 refetch 버튼 없음 [확인] | `ApiError` 발생 시 react-query의 기본 동작: `error` 상태에 쌓이지만 화면에 표시하는 코드 없음(`ExchangeManagementPage.tsx:21`에서 `error` 변수는 등록 전용 [확인]) |
| `useRegisterExchangeCredential` | `frontend/packages/shared-hooks/src/useExchanges.ts:12` | `useMutation` — `mutateAsync()` 호출 시 POST `/api/v1/exchange-credentials` 1회 전송. `postIdempotent` 자동 생성: 키 미전송 시 16~128자 Idempotency-Key 자동 생성(`frontend/packages/api-client/src/clients/exchange.ts:54`, `frontend/packages/api-client/src/clients/exchange.test.ts:50`). `onSuccess` 시 `exchangeCredentials` 무효화(`useExchanges.ts:16`) [확인] | `mutateAsync`가 `ApiError` 던짐. 화면(`ExchangeManagementPage.tsx:78-84`)에서 `registerWithRetry()` 호출 후 `catch` 블록(`:96-101`)이 `ApiError`면 그대로, 아니면 `t("legacy.exchangeManagementPage.t2")`("등록에 실패했습니다.")로 오류 메시지 생성. `setFromError(err)`로 필드별 오류 추출(`useFieldErrors.ts:19` → `extractFieldErrors`) [확인] |
| `useRevokeExchangeCredential` | `frontend/packages/shared-hooks/src/useExchanges.ts:20` | `useMutation` — `mutateAsync(exchange)` 호출 시 DELETE `/api/v1/exchange-credentials/:exchange` 1회 전송(`frontend/packages/api-client/src/clients/exchange.ts:61-63`). `onSuccess` 시 `exchangeCredentials` 무효화(`useExchanges.ts:24`) [확인] | `mutateAsync`가 `ApiError` 던짐. 화면(`ExchangeManagementPage.tsx:50-60`)에서 `performRevoke` → `revokeWithRetry` 호출 후 `catch`가 `revokeError` 상태에 `{exchange, error}` 저장. `ApiError`면 그대로, 아니면 "해지에 실패했습니다." [확인] |
| `useExchangeBalance` | `frontend/packages/shared-hooks/src/useExchanges.ts:28` | `useQuery` — `enabled: !!exchange`(`useExchanges.ts:32`). `selectedExchange` 상태(`ExchangeManagementPage.tsx:37`)가 truthy일 때만 GET `/api/v1/exchange-credentials/:exchange/balance` 조회. 폴링 옵션 미설정 — 1회 조회 [확인] | `ApiError` 발생 시 react-query 기본 동작. `ExchangeManagementPage.tsx:38`에서 `data: balances`만 destrucure — `error`/`isLoading` 미사용. `CredentialListCard`로 전달되지 않음 [확인] |
| `useFieldErrors` | `frontend/apps/web/src/hooks/useFieldErrors.ts:15` | 로컬 상태 훅 — 외부 API 호출 없음. `setFromError(err)`가 `extractFieldErrors(err)` 호출하여 `Record<string, string>` 세팅(`useFieldErrors.ts:19`). `clearField(field)`가 해당 필드 오류만 삭제(`useFieldErrors.ts:22-28`) [확인] | `extractFieldErrors`가 필드 오류가 없는 오류 타입이면 빈 객체 `{}` 반환. `setFromError(null)` 호출 시 상태 변경 없음(React state setter에 null 전달, 기존 값 유지) [확인] |
| `useConflictRetry` | `frontend/apps/web/src/hooks/useConflictRetry.ts:18` | `mutate` 함수 래퍼. 409 중 `classifyStateConflict(err) === "refetch_retry"`일 때만 `refetch()` 1회 후 `mutate` 재시도 정확히 1회(`useConflictRetry.ts:27-29`). 그 외 409(idempotency, invalid_transition)·400·5xx는 즉시 재던짐. 무한 재시도 금지 [확인] | 재시도 후에도 실패하면 원본 `ApiError`가 `catch` 블록으로 전파. 호출부(`ExchangeManagementPage.tsx:53-57, 96-101`)가 `error` 상태에 적재하여 사용자에게 안내 [확인] |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-ExchangeManagementPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `useExchangeCredentials`가 `isLoading: true` (`useExchanges.ts:5-9`의 `useQuery` 기본 로딩, `ExchangeManagementPage.tsx:21`에서 `isLoading` destructured) | `CredentialListCard.tsx:33-34` — `<LoadingState />` 표시. i18n 키 없음(LoadingState 컴포넌트 내부 고정 문구) | 없음(로딩 완료 대기) | 구현됨(`CredentialListCard.tsx:33-34`) |
| 빈 상태 | `credentials`가 빈 배열(`CredentialListCard.tsx:35`의 `credentials.length > 0` 조건 거짓) | `CredentialListCard.tsx:79` — `<EmptyState>{t("legacy.credentialListCard.t5")}</EmptyState>` | 없음(등록 폼으로 이동) | 사실 시트 누락 — `legacy.credentialListCard.t5`는 facts.md에 없음. `legacy.credentialListCard.*` 키 전체가 시트에 등재되지 않음(생성기가 자식 컴포넌트 파일을 훑지 않음). 코드상 구현은 있으나 사실 시트 미등재 |
| 정상 | `credentials.length > 0`(`CredentialListCard.tsx:35`의 `length > 0` 조건 참) | `CredentialListCard.tsx:36-76` — 거래소 목록 행(거래소명, 활성/비활성 배지, 연결일, withdrawalPermissionWarning, LIVE_BLOCKED_NOTICE). 페이지 제목 `legacy.exchangeManagementPage.title1` "거래소 연동"(`ExchangeManagementPage.tsx:135`) | 해지 버튼(`CredentialListCard.tsx:65-72`, `t("legacy.credentialListCard.t4")`), 거래소 선택(`onSelectExchange`, `ExchangeManagementPage.tsx:145` → `setSelectedExchange`), 등록 폼(`RegisterCredentialForm`, `ExchangeManagementPage.tsx:152-167`) | 구현됨(목록 행: `CredentialListCard.tsx:36-76`, 제목: `ExchangeManagementPage.tsx:135`, 해지: `CredentialListCard.tsx:65-72`, 선택: `ExchangeManagementPage.tsx:145`) |
| 낡은 데이터(신선도 초과) | `useExchanges.ts:5-10`에 staleTime, refetchInterval, gcTime 옵션이 없음(기본값 staleTime=0, gcTime=300000). 수동 새로고침 UI 없음(CredentialListCard에 리프레시 버튼 없음) | 신선도 표시(마지막 갱신 시각 등) 없음. 검색 결과: `exchanges/` 전체에 stale, updatedAt, 마지막, 갱신 키워드가 없음(`ExchangePositionsCard.test.tsx:49`의 updatedAt은 테스트 데이터 필드일 뿐) | 등록 성공 시 `useExchanges.ts:16` — `qc.invalidateQueries({ queryKey: ["exchangeCredentials"] })`로 자동 refetch. 해지 성공 시 `useExchanges.ts:24` — 동일 무효화. 수동 새로고침 버튼 없음 | 구현됨(자동 무효화 경로: `useExchanges.ts:16,24`). 낡은 데이터 신선도 표시: 없음(`exchanges/` 전체 검색 결과 없음) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-ExchangeManagementPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `ExchangeManagementPage.tsx:78` — `register.mutateAsync`가 `ApiError`(400) 던짐. `setFromError(err)`가 `extractFieldErrors(err)` 호출하여 필드별 오류 추출(`ExchangeManagementPage.tsx:101`, `useFieldErrors.ts:19`). `RegisterCredentialError`(`ExchangeManagementPage.tsx:100`)가 `ExchangeCredentialErrors.tsx:37`에서 `classifyBadRequest`로 분류 | 필드별 오류(`Field.error` prop: `RegisterCredentialForm.tsx:48`(exchange), `:57`(api_key), `:65`(api_secret), `:74`(api_passphrase)) + 전체 오류(`RegisterCredentialForm.tsx:89`). `BadRequestNotice`는 알려진 error_code일 때 고정 문구, 아니면 `ErrorMessage`에 `redactSecret` 처리된 메시지 | 필드 오류 수정 후 재제출(`ExchangeManagementPage.tsx:165`), `onRetry`로 재시도(`:166`) | 구현됨(`RegisterCredentialForm.tsx:48,57,65,74,89`, `ExchangeManagementPage.tsx:78,100-101`, `useFieldErrors.ts:19`) |
| 인증 필요/세션 만료(401) | 이 페이지는 `router.tsx:70`의 `ProtectedRoute` 감싸짐: 토큰 필요(`router.tsx:20`), `me` 클레임 필요(`router.tsx:29`). 401 응답 시 페이지에 도달하지 못함 | 401 관련 문구 없음. 페이지 내에서 401 처리 코드 없음. ExchangeManagementPage.tsx 전체 검색: 401, unauthorized 키워드 없음 | 없음(ProtectedRoute가 리다이렉트) | 없음(근거: `router.tsx:70`의 `ProtectedRoute`가 인증 가드, 페이지 내 401 처리 없음) |
| 권한 없음/MFA 필요(403) | 서버가 403 응답. `RegisterCredentialError`(`ExchangeCredentialErrors.tsx:36`)와 `RevokeCredentialError`(`ExchangeCredentialErrors.tsx:54`)가 `classifyForbidden(error)` 체크 | `ForbiddenNotice` 컴포넌트 렌더링(`ExchangeCredentialErrors.tsx:36,54`). 고정 문구(EXACT_MESSAGES) | 권한 획득 후 재시도 | 구현됨(`ExchangeCredentialErrors.tsx:36,54`) |
| 충돌/상태 불일치(409) | `useConflictRetry.ts:18` — `useConflictRetry`가 409 수신. `classifyStateConflict(err) === "retryable"`일 때 refetch 후 1회 재시도(`ExchangeManagementPage.tsx:45`(해지), `:76`(등록)) | 409 오류 메시지 없음. `useConflictRetry`가 투명하게 재시도. 재시도 실패 시 후속 오류(5xx 등)만 등록/해지 오류 컴포넌트로 전달 | 없음(자동 재시도). 재시도 실패 시 후속 오류 처리 | 구현됨(`useConflictRetry.ts:18`, `ExchangeManagementPage.tsx:45,76`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-ExchangeManagementPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 서버가 423 Locked 또는 429 Too Many Requests 응답. `classifyRetry(err)`이 `RATE_LIMIT_EXCEEDED` errorCode를 `kind: "backoff"`로 분류(`shared-types/src/retryable.ts:27-31,45-50`) | `ErrorMessage` 컴포넌트가 `retryAfterSec` 초 카운트다운 표시(`ErrorMessage.tsx:50-53`). 재시도 버튼은 카운트다운 완료 시 활성화(`ErrorMessage.tsx:66`) | 카운트다운 완료 후 "다시 시도" 버튼 클릭. 실제 재시도는 호출부(`ExchangeCredentialErrors.tsx:47`, `ExchangeCredentialErrors.tsx:65`)의 `onRetry`가 담당 | 구현됨. 등록/해지 모두 `ErrorMessage`가 `retryAfterSec`을 받아 backoffRetry UI 표시. 단, `useConflictRetry`(`useConflictRetry.ts:18`)는 409만 처리하므로 429는 `useConflictRetry` 재시도 대신 `ErrorMessage`의 backoffRetry만 동작(적합). 423 Locked은 errorCode가 `RATE_LIMIT_EXCEEDED`가 아니므로 `classifyRetry` → `kind: "none"`, 재시도 버튼 없음. 코드: `shared-types/src/retryable.ts:27-31,45-50`, `ExchangeCredentialErrors.tsx:46-47`, `ErrorMessage.tsx:38-42,50-53` |
| 서버 오류(5xx) | 서버가 500/502/503 응답. `classifyServerError`가 errorCode로 분류(`shared-types/src/serverError.ts:39-52`) | `EXCHANGE_UNAVAILABLE`(503), `DEPENDENCY_NOT_READY`(503): `kind: "retryable"` → 재시도 버튼 표시. `EXCHANGE_FATAL`(502), `INTERNAL_ERROR`(500): `kind: "fatal"` → 재시도 버튼 없음. 모든 5xx는 `ErrorMessage`가 `errorCode`에 따른 한국어 메시지로 변환(`shared-types/src/apiError.ts:108`) | retryable(503): "다시 시도" 버튼으로 등록/해지 재시도. fatal(502/500): 메시지만 표시, 재시도 불가 | 구현됨. `classifyServerError`(`shared-types/src/serverError.ts:39-52`)가 4종류 분류. `ExchangeCredentialErrors.tsx:40,47`, `ExchangeCredentialErrors.tsx:58,65`에서 `serverError.kind === "retryable"`일 때만 `onRetry` 전달. 코드: `shared-types/src/serverError.ts:25-30`, `shared-types/src/retryable.ts:27-31` |
| 네트워크 단절 | API 호출 중 네트워크 연결 손실. react-query가 `error` 상태로 전환 | 등록: `catch`(`ExchangeManagementPage.tsx:96-101`)가 `ApiError`가 아니면 `new Error(t("legacy.exchangeManagementPage.t2"))`("등록에 실패했습니다.") 생성. 해지: `catch`(`ExchangeManagementPage.tsx:53-57`)가 `new Error("해지에 실패했습니다.")` 생성. 둘 다 `ErrorMessage` 컴포넌트가 `getApiErrorMessage`의 fallbackMessage 경로로 일반 오류 메시지 표시(`shared-types/src/apiError.ts:108-117`) | "다시 시도" 버튼: `classifyRetry`가 `errorCode` 없으면 `kind: "none"` → 재시도 버튼 없음(`ErrorMessage.tsx:39`). 그러나 `ExchangeManagementPage.tsx:166`에서 `onRetry={() => void submitRegistration()}`가 폼에 제공되므로, 사용자가 폼 하단에서 수동 재시도 가능 | 구현됨(제한적). 네트워크 실패 시 오류 메시지는 표시되나, `classifyRetry`가 `errorCode` 없으므로 `ErrorMessage`에 재시도 버튼 없음(`ErrorMessage.tsx:39`). 수동 재시도는 `ExchangeManagementPage.tsx:166`의 `onRetry`로 가능. 해지 측(`handleRetryRevoke`, `ExchangeManagementPage.tsx:67-72`)도 재시도 버튼 제공 |
| 부분 실패(일부 데이터만 실패) | `useExchangeCredentials`는 성공했으나 `useExchangeBalance` 또는 `useExchangePositions`가 실패 | `useExchangeBalance`: `ExchangeManagementPage.tsx:38`에서 `data: balances`만 destructured. `error`/`isLoading` 미사용. `CredentialListCard`로 `balances`만 전달, error 처리 없음 → balance 조회 실패 시 화면에 아무 표시 없음(갭). `useExchangePositions`: `ExchangePositionsCard.tsx:46-51`에서 `query.isError` 갈래가 있고 `PositionsQueryError` 컴포넌트로 에러 표시 + 재시도 버튼 제공 | balance 실패: 현재 재시도 수단 없음(갭). positions 실패: `PositionsQueryError`의 재시도 버튼으로 `query.refetch()` | 구현됨(partial). positions 에러는 `ExchangePositionsCard.tsx:46-51`에서 `PositionsQueryError`로 표시·재시도 가능. balance 에러는 `useExchangeBalance`의 `error`가 화면에 표시되지 않음(`ExchangeManagementPage.tsx:38`: `data`만 destructured). 코드: `shared-hooks/src/useExchanges.ts:28-33`, `ExchangeManagementPage.tsx:38`, `ExchangePositionsCard.tsx:39,46-51` |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-ExchangeManagementPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| 거래소 선택 (잔고 조회 버튼) | 등록 목록에 행이 있을 때(`CredentialListCard.tsx:35-76`의 `credentials.length > 0`) | `onClick` → `onSelectExchange(c.exchange)` → `setSelectedExchange`(`CredentialListCard.tsx:62-64`, `ExchangeManagementPage.tsx:145`). `useExchangeBalance`가 `selectedExchange` truthy로 전환되면 GET `/api/v1/exchange-credentials/:exchange/balance` 조회 시작(`ExchangeManagementPage.tsx:38`, `shared-hooks/src/useExchanges.ts:28-32`) | `useExchangeBalance`의 `error`가 화면에 표시되지 않음(`ExchangeManagementPage.tsx:38`에서 `data: balances`만 구조분해). 잔고 조회 실패 시 사용자에게 피드백 없음 | 같은 페이지 |
| 해지 버튼 | 행이 존재하고 `revokingExchange !== c.exchange`이며 `!scope.isLive`일 때(`CredentialListCard.tsx:69`의 `disabled` 조건: live 거래소는 해지 버튼 비활성화, 중복 클릭 방지) | `onClick` → `onRevoke(c.exchange)` → `handleRevoke`(`CredentialListCard.tsx:70-72`, `ExchangeManagementPage.tsx:62-65`) → `setRevokeError(null)` 후 `performRevoke` → `revokeWithRetry(exchange)` → DELETE `/api/v1/exchange-credentials/:exchange` → `onSuccess` 시 `exchangeCredentials` 무효화(`shared-hooks/src/useExchanges.ts:24`) → 목록 갱신(`useExchangeCredentials.refetch()`) | `ApiError`면 `err` 그대로, 아니면 "해지에 실패했습니다."(`ExchangeManagementPage.tsx:56`)를 `{exchange, error}`로 `revokeError`에 저장 → `RevokeCredentialError` 컴포넌트에서 표시(`CredentialListCard.tsx:82-86`, `ExchangeCredentialErrors.tsx:53-68`) | 같은 페이지 |
| 해지 재시도 | `revokeError`가 있고 `classifyServerError(revokeError.error).kind === "retryable"`일 때(`ExchangeCredentialErrors.tsx:53-68`의 `onRetry` 갈래: 5xx 재시도 가능 서버 오류일 때만 버튼 노출) | `onRetryRevoke` → `handleRetryRevoke`(`CredentialListCard.tsx:84`, `ExchangeManagementPage.tsx:67-72`) → `setRevokeError(null)` 후 `performRevoke` 재호출 — 성공/실패는 해지 행과 동일 | 동일 | 같은 페이지 |
| 등록 폼 입력 변경 (거래소·API 키·시크릿·패스프레이즈) | 폼 표시 상태(`RegisterCredentialForm.tsx:44-94`) | `handleExchangeChange`(`ExchangeManagementPage.tsx:110-113`): `setExchange(value)`, `clearField("exchange")`. `handleApiKeyChange`(`:115-118`): `setApiKey(value)`, `clearField("api_key")`. `handleApiSecretChange`(`:120-124`): `setApiSecret(value)`, `setSecretsCleared(false)`, `clearField("api_secret")`. `handleApiPassphraseChange`(`:126-130`): `setApiPassphrase(value)`, `setSecretsCleared(false)`, `clearField("api_passphrase")`. 각 `clearField`는 해당 필드 오류만 삭제(`useFieldErrors.ts:22-28`) | 해당 없음(입력 자체는 실패하지 않음). `secretsCleared`가 true였다면 해당 필드 입력 시 false로 전환 | 같은 페이지 |
| 등록 제출 | 폼 입력이 채워진 상태(HTML5 `required` 검증: API Key, API Secret 필수, Bitget일 때 API Passphrase 필수 — `RegisterCredentialForm.tsx:60,68,77`). `exchange`는 SELECT에서 기본값 "bitget" 선택됨(`RegisterCredentialForm.tsx:7,49-55`) | `handleSubmit`(`ExchangeManagementPage.tsx:105-108`) → `submitRegistration`(`:87-103`) → `registerWithRetry()` → POST `/api/v1/exchange-credentials`. Bitget일 때만 `apiPassphrase` 전송(`:82`). 성공: 키·시크릿·패스프레이즈 비움(`:93-95`), 목록 무효화(`shared-hooks/src/useExchanges.ts:16`). 실패: 시크릿·패스프레이즈 비움 + `secretsCleared` true(`:97-99`), 400이면 `setFromError(err)`로 필드별 오류(`useFieldErrors.ts:19`, `extractFieldErrors`), 그 외 "등록에 실패했습니다."(`:100`, i18n `legacy.exchangeManagementPage.t2`) | 같은 페이지 | 같은 페이지 |
| 등록 재시도 | 등록 오류가 재시도 가능(5xx)일 때 — `RegisterCredentialError`에서 `classifyServerError(error).kind === "retryable"`이면 `onRetry` 버튼 노출(`ExchangeCredentialErrors.tsx:29-47`, `ExchangeManagementPage.tsx:166`) | `onRetry={() => void submitRegistration()}`(`ExchangeManagementPage.tsx:166`, `RegisterCredentialForm.tsx:89`). 성공/실패는 6번(등록 제출)과 동일 | 같은 페이지 |
| 포지션 재시도 | `query.isError`이고 `exchange`가 선택된 상태(`ExchangePositionsCard.tsx:46-51`) | `onRetry={() => query.refetch()}`(`ExchangePositionsCard.tsx:50`). 성공: 포지션 재조회(`shared-hooks/src/useExchanges.ts:36-41`). 실패: `PositionsQueryError` 유지 | 같은 페이지 |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-ExchangeManagementPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음 — 이 화면은 거래소 API 자격증명(키/시크릿/패스프레이즈)의 등록·해지·조회만 처리한다(`ExchangeManagementPage.tsx:21-23`: `useExchangeCredentials`/`useRegisterExchangeCredential`/`useRevokeExchangeCredential`). MONEY_ACTIONS.md A1~A12에 명시된 행동(주문 제출, 전략 시작/종료/일시정지, 모의→실전 전환, 출금/충전, 킬스위치, 승인 설정 변경, 리밸런싱) 중 어느 것도 이 화면에서 직접 수행하지 않는다. 자격증명 등록은 거래 가능 경로를 확보하는 선행 단계이지만, 그 자체가 주문/실행/출금을 유발하지 않으며, 해지도 기존 포지션이나 잔고에 영향을 주지 않는 읽기 전용 작업이다(`exchange_credentials.py:67-76`: DELETE는 활성 플래그만 false로 설정). |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-ExchangeManagementPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| 등록 실패 (submitRegistration catch) | `legacy.exchangeManagementPage.t2` → "등록에 실패했습니다." (`ExchangeManagementPage.tsx:100`) | `RegisterCredentialError` 컴포넌트(`RegisterCredentialForm.tsx:89`)가 taxonomy 분류: (1) 403 → `ForbiddenNotice` (`errorMessage.t1` trace_id) (2) 400 → `BadRequestNotice` (`getApiErrorMessage(errorCode, message)` 매핑) (3) 5xx/429 → `ErrorMessage` + "다시 시도" 버튼 (`errorMessage.t2` 카운트다운, `t3`) (`ExchangeCredentialErrors.tsx:27-50`) | 예 — `error instanceof ApiError ? error.traceId : null` (`ExchangeCredentialErrors.tsx:45`) |
| 등록 필드 오류 (submitRegistration → setFromError) | `useFieldErrors`가 `extractFieldErrors`로 필드별 오류 추출 (`ExchangeManagementPage.tsx:101`, `useFieldErrors.ts:19`) | 각 Field 컴포넌트의 `error` prop에 표시 (`RegisterCredentialForm.tsx:48,57,65,74`) — 인라인 필드 오류 배너. `fieldErrors`가 비어 있으면 `RegisterCredentialError`의 `ErrorMessage`도 병렬 노출 | 예 — ApiError.traceId가 ErrorMessage로 전달 (`ExchangeCredentialErrors.tsx:45`) |
| 해지 실패 (performRevoke catch) | `err instanceof ApiError ? err : new Error("해지에 실패했습니다.")` (`ExchangeManagementPage.tsx:56`) — ApiError면 `redactSecret(error.message)`, 아니면 고정 문구 | `RevokeCredentialError` 컴포넌트(`CredentialListCard.tsx:84`)가 taxonomy 분류: (1) 403 → `ForbiddenNotice` (2) 400 → `BadRequestNotice` (3) 5xx/429 → `ErrorMessage` + 재시도 버튼 (`ExchangeCredentialErrors.tsx:53-67`) | 예 — `error instanceof ApiError ? error.traceId : null` (`ExchangeCredentialErrors.tsx:63`) |
| 해지 재시도 버튼 (handleRetryRevoke) | — (재시도 버튼 클릭 시 재호출) | `handleRetryRevoke()`(`ExchangeManagementPage.tsx:67-72`)가 `revokeError` 상태를 null로 초기화 후 `performRevoke` 재호출 | 동일 경로 |
| 포지션 조회 실패 (ExchangePositionsCard) | `exchangePositions.notFoundTitle` → "이 거래소의 자격증명이 없습니다." (`catalog.ko.ts:36`) — NotFoundState title | `PositionsQueryError` 컴포넌트 재사용 (`ExchangePositionsCard.tsx:47-50`): (1) 404 RESOURCE_NOT_FOUND → `NotFoundState` (재시도 버튼 없음) (2) 403 → `ForbiddenNotice` (3) 5xx/429 → `ErrorMessage` + 재시도 (`ExchangePositionsCard.tsx:50`) | 예 — `error instanceof ApiError ? error.traceId : null` (`PositionsQueryError.tsx:32`) |
| 등록 폼 secret 노출 방지 | — (보안 동작) | F-5(task-10642): 등록 실패 시 `apiSecret`/`apiPassphrase`가 빈 문자열로 초기화됨 (`ExchangeManagementPage.tsx:97-98`). `secretsCleared` 상태가 true가 되면 `RegisterCredentialForm.tsx:83-86`에서 `t("legacy.registerCredentialForm.secretsClearedNotice")` 배너 노출 | 해당 없음 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-ExchangeManagementPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-ExchangeManagementPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-ExchangeManagementPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
