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
| 잠김/속도 제한(423/429) | (429) 등록·해지 실패 시 `ExchangeCredentialErrors.tsx:39,46`(등록)·`:57,64`(해지)의 `routeApiError`가 `RATE_LIMIT_EXCEEDED`를 `backoff_retry`로 분류(`retryable.ts:27-31`)하고 `retryAfterSec`을 `ErrorMessage`로 전달. (423) `AUTH_ACCOUNT_LOCKED`는 `deriveLockout`이 처리하나(`errorRouting.ts:25`, `accountLockout.ts:41`) 이 화면의 오류 컴포넌트는 별도 423 분기가 없음 | 429: `ErrorMessage`(`ErrorMessage.tsx:58-70`)가 고정 문구 "요청이 너무 많습니다. 잠시 후 다시 시도해주세요."(`apiError.ts:78`)를 표시. `retryAfterSec`이 있으면 `legacy.errorMessage.t2` 카운트다운 문구(`ErrorMessage.tsx:63-65`). 423: 화면 고유 문구 없음 — `AUTH_ACCOUNT_LOCKED` 문구(`apiError.ts:64`)는 `ErrorMessage` 경로의 `getApiErrorMessage`가 반환할 수 있으나 이 화면에서의 실제 도달은 미확인 | 429: 등록 폼은 `onRetry`가 `serverError.kind === "retryable"`일 때만 전달되므로(`ExchangeCredentialErrors.tsx:47,65`) `RATE_LIMIT_EXCEEDED`에는 `onRetry`가 없어 "다시 시도" 버튼이 나타나지 않음(`ErrorMessage.tsx:61`의 `canRetry && onRetry` 조건). 사용자는 직접 폼을 다시 제출. 423: 없음 | 429: 문구·카운트다운 계산은 구현됨(`ExchangeCredentialErrors.tsx:46,64`), 단 재시도 버튼은 미연결(`:47,65`). 423: 전용 처리 없음 |
| 서버 오류(5xx) | 등록/해지 `mutateAsync`가 `ApiError` 던짐(`ExchangeManagementPage.tsx:100,56`). `EXCHANGE_UNAVAILABLE`·`DEPENDENCY_NOT_READY`(503)는 `retryable`, `EXCHANGE_FATAL`(502)·`INTERNAL_ERROR`(500)는 `fatal`(`serverError.ts:25-30,39-51`) | `ErrorMessage` 배너(`ErrorMessage.tsx:58`): `error_code`에 매핑된 고정 문구, 코드 없으면 `redactSecret` 처리된 메시지(`ExchangeCredentialErrors.tsx:44,62`). `traceId`가 있으면 `legacy.errorMessage.t1` 줄 표시(`ErrorMessage.tsx:60`). 등록 실패 시 secret/passphrase 입력은 비워지고 안내 배너 표시(`ExchangeManagementPage.tsx:97-99`) | 503 계열만 "다시 시도" 버튼(`legacy.errorMessage.t3`, `ErrorMessage.tsx:66-67`): 등록은 `onRetry`→`submitRegistration`(`ExchangeManagementPage.tsx:166`), 해지는 `handleRetryRevoke`(`:67-72`). 500/502는 재시도 버튼 없음. 단 비워진 secret을 다시 입력해야 등록 재제출 가능 | 구현됨(`ExchangeCredentialErrors.tsx:36-50,53-67`). GET은 api-client에서 503에 한해 1초·2초 자동 재시도 2회(`httpErrors.ts:52,62-63,70-71`); 등록(POST)/해지(DELETE)는 자동 재시도 없음(`httpErrors.ts:71`) |
| 네트워크 단절 | `fetch`가 throw(`http.ts:46`에서 catch 없음)하면 `ApiError`가 아닌 오류가 전파 | 등록: `Error(t("legacy.exchangeManagementPage.t2"))` → "등록에 실패했습니다."(`ExchangeManagementPage.tsx:100`), `ErrorMessage`가 `errorCode` 없이 `redactSecret(message)` 표시(`ExchangeCredentialErrors.tsx:43-44`). 해지: 하드코딩 `Error("해지에 실패했습니다.")`(`ExchangeManagementPage.tsx:56`, i18n 키 없음). 목록·잔고 조회 실패: 화면에 표시하는 코드 없음(`ExchangeManagementPage.tsx:21,38`에서 `error` 미사용). 별도 오프라인 배너: 미확인(이 화면 파일에는 없음) | 등록 실패 후 secret 재입력·재제출. `onRetry`는 `ApiError` 아님이라 `classifyServerError`가 `not_applicable`을 반환해(`serverError.ts:40`) 전달되지 않음. 해지도 동일 | 부분 구현: 등록·해지 실패 문구는 있으나 네트워크 전용 안내·재시도 없음. 조회 실패는 없음 |
| 부분 실패(일부 데이터만 실패) | 자격 증명 목록(`useExchangeCredentials`)은 성공하고 잔고(`useExchangeBalance`, `ExchangeManagementPage.tsx:38`)나 포지션(`ExchangePositionsCard.tsx:39`)만 실패 | 포지션: `PositionsQueryError`(`ExchangePositionsCard.tsx:46-51`, `PositionsQueryError.tsx:19-37`) — 404는 `NotFoundState`, 403은 `ForbiddenNotice`, 그 외는 `ErrorMessage`(재시도 버튼은 `refetch_retry`/`backoff_retry`일 때만, `:27`). 잔고: `error`를 읽지 않아(`ExchangeManagementPage.tsx:38`) 실패 시 `balances`가 `undefined`로 잔고 영역이 `CredentialListCard.tsx:88`의 조건 거짓으로 아예 표시되지 않음 — 오류 안내 없음. 목록 조회 자체 실패: `credentials`가 `undefined`라 `EmptyState`(`CredentialListCard.tsx:78-79`)가 표시되어 "없음"과 구분되지 않음 | 포지션: 재시도 버튼(`ExchangePositionsCard.tsx:50`). 잔고·목록: 거래소 재선택(`onSelectExchange`, `ExchangeManagementPage.tsx:145`)이나 해지/등록 성공 시 무효화로 재조회(`useExchanges.ts:16,24`)에 의존. 해지 실패는 목록은 유지하고 해당 행 아래에 `RevokeCredentialError`만 표시(`CredentialListCard.tsx:82-86`) | 포지션: 구현됨. 잔고: 없음(오류 미표시). 목록 조회 실패: 없음(빈 상태로 오인) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-ExchangeManagementPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| TODO | | | |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-ExchangeManagementPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-ExchangeManagementPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| TODO | | |
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
