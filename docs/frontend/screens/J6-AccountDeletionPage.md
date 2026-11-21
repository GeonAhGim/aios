# J6-AccountDeletionPage — `/settings/account`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/AccountDeletionPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/settings/account` / `frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-AccountDeletionPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 — 개인 트레이더(투자자): `docs/frontend/PERSONAS_AND_JOURNEYS.md` §2. P1의 핵심 과업은 "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독"이며, J6 설정·승인·감사 여정은 P1(+P2)이 담당한다. |
| 목적(한 문장) | 인증된 사용자가 자신의 계정 탈퇴를 요청하고, 위기 상황에 대비해 비상 출금 목적지 화이트리스트를 사전 등록한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:150` — `/settings/account` 경로. `protect()` 함수(`router.tsx:70-72`)가 `<ProtectedRoute>`로 래핑하여 인증된 사용자만 접근 가능하게 한다. |
| 사용자 준비물 | 계정 비밀번호 — 탈퇴 요청 시 `label13` 필드(비밀번호 확인, `AccountDeletionPage.tsx:165`)에 입력해야 하며, WRONG_PASSWORD 응답(400) 시 요청이 거부된다. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-AccountDeletionPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useWhitelistEntries` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:24` | 1회 조회 — `useQuery({ queryKey: ["whitelistEntries"], queryFn: () => apiClient.listWhitelistEntries() })`. 자동 갱신 없음, 기본 staleTime이므로 마운트 시 한 번만 GET `account.whitelist` (`frontend/packages/api-client/src/clients/account.ts:50-52`)을 날린다. `whitelistLoading`(`AccountDeletionPage.tsx:41`)으로 로딩 상태 표시. | `queryFn` 실패 시 React Query가 에러 상태 설정. 화면에서는 `whitelistLoading`이 false가 된 후 `whitelist && whitelist.length > 0` 체크가 false가 되어 `<EmptyState>` 경로로 떨어진다(`AccountDeletionPage.tsx:98-110`). 명시적 에러 UI는 없음 — `AccountSecurityError` 컴포넌트에서 처리하지 않음. |
| `useRegisterWhitelistEntry` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:31` | 변경 요청(mutations) — `mutateAsync({ exchange, destinationAddress, label?, password })` 호출 시 POST `account.whitelist`(`frontend/packages/api-client/src/clients/account.ts:54-56`). 성공 시 `onSuccess` 후크가 `{ queryKey: ["whitelistEntries"] }`를 무효화하여 `useWhitelistEntries`가 자동으로 리페치(`useAccountSettings.ts:35`). | `handleWhitelistSubmit`(`AccountDeletionPage.tsx:56-72`)에서 `mutateAsync` catch → `setWlError(err)`(`AccountDeletionPage.tsx:70`). `{wlError !== null && <AccountSecurityError error={wlError} />}`(`AccountDeletionPage.tsx:140`)가 에러 표시. `AccountSecurityError`는 `classifyBadRequest`→`BadRequestNotice`, `classifyForbidden`→`ForbiddenNotice`, 그 외→`ErrorMessage` 경로로 라우팅(`AccountDeletionPage.tsx:25-37`). |
| `useRequestAccountDeletion` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:39` | 변경 요청(mutations) — `mutateAsync({ password })` 호출 시 POST `account.deletion`(`frontend/packages/api-client/src/clients/account.ts:58-60`). `onSuccess` 후크 없음. 서버 응답 `DeletionResponse.deletionEffectiveAt`를 `setDeletionResult`로 성공 시각 표시(`AccountDeletionPage.tsx:79-81`). | `handleDeleteSubmit`(`AccountDeletionPage.tsx:74-85`)에서 `mutateAsync` catch → `setDeleteError(err)`(`AccountDeletionPage.tsx:83`). `{deleteError !== null && <AccountSecurityError error={deleteError} />}`(`AccountDeletionPage.tsx:174`)가 에러 표시. 동일하게 `AccountSecurityError` taxonomy로 라우팅. |
| `useLogout` | `frontend/packages/shared-hooks/src/useAuth.ts:49` | 변경 요청(mutations) — `async () => { await client.logout(); qc.clear(); }`. `client.logout()`은 `createLogoutClient`(`@aios/api-client`)가 생성한 함수로, 서버 `POST /auth/logout` 후 로컬 스토어 정리(`useAuthStore.getState().logout()`)(`useAuth.ts:51-59`). `qc.clear()`로 모든 쿼리 캐시 삭제. | 호출부(`AccountDeletionPage.tsx:154-156`)에서 `await logout()` 후 `navigate("/login")`. try/catch 없음 — 에러가 상단 전파되어 React error boundary가 처리하거나 브라우저가 에러 표시. 명시적 복구 UI는 없음. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-AccountDeletionPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `whitelistLoading === true` (`useWhitelistEntries` 훅이 `isLoading: true` 반환) | `<LoadingState />` 컴포넌트 표시 (`AccountDeletionPage.tsx:96-97`) | 없음 — 폼도 렌더링되나 로딩 중 데이터 없음 | `구현됨` (`AccountDeletionPage.tsx:96-97`) |
| 빈 상태 | `whitelistLoading === false`이고 `whitelist && whitelist.length > 0`이 `false` (`AccountDeletionPage.tsx:98`) | `EmptyState` 컴포넌트에 `legacy.accountDeletionPage.t4` ("등록된 목적지가 없습니다.") 표시 (`AccountDeletionPage.tsx:106-110`) | whitelist 등록 폼에 거래소/주소/비밀번호 입력 후 `legacy.accountDeletionPage.t9` ("목적지 등록") 버튼 클릭 | `구현됨` (`AccountDeletionPage.tsx:106-110`) |
| 정상 | `whitelistLoading === false`이고 `whitelist.length > 0` (`AccountDeletionPage.tsx:98`) | 등록 목적지 목록 (`<ul>` — `AccountDeletionPage.tsx:99-105`), whitelist 등록 폼 (`:111-143`), 회원 탈퇴 카드 (`:146-179`) | 1) whitelist 등록 폼 제출 → `handleWhitelistSubmit` (`:56-72`) 2) 탈퇴 요청 폼에 비밀번호 입력 후 `legacy.accountDeletionPage.t14` ("탈퇴 요청") 클릭 → `handleDeleteSubmit` (`:74-85`) 3) 성공 시 `legacy.accountDeletionPage.t16` ("탈퇴가 예약됐습니다. {{val}}에 확정됩니다.") + `legacy.accountDeletionPage.t11` ("로그아웃") 버튼 (`:148-160`) | `구현됨` (`AccountDeletionPage.tsx:87-179`) |
| 낡은 데이터(신선도 초과) | `useWhitelistEntries`(`shared-hooks/src/useAccountSettings.ts:24-29`)에 명시적 갱신 옵션 없음 — React Query 기본 설정이라 조회 직후 stale. refetchOnWindowFocus 기본값 여부 전역 `QueryClient` 설정(`main.tsx:15`) 확인 필요 | 명시적 "낡은 데이터" 경고 UI 없음 — 마지막 캐시 데이터 표시 | 페이지 재방문 시 마운트 재시작으로 refetch, 수동 새로고침 버튼 없음 | `없음` — stale 상태 시각화 없음, 명시적 갱신 옵션이 이 훅에 없음 (`shared-hooks/src/useAccountSettings.ts:24-29`) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-AccountDeletionPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `classifyBadRequest`(`AccountDeletionPage.tsx:26`)가 true — `statusCode===400` (`shared-types/badRequest.ts:34-42`) | `BadRequestNotice`(`AccountDeletionPage.tsx:26`) — `getApiErrorMessage`(`apiError.ts:106`) | kind별 안내: field→인라인 필드 오류, disclosure_retired→재조회 버튼, mfa_invalid→코드 재입력 | `구현됨` (`AccountDeletionPage.tsx:26`, `shared-types/badRequest.ts:34`) |
| 인증 필요/세션 만료(401) | `classifyBadRequest`→null, `classifyForbidden`→null, `routeApiError`(`errorRouting.ts:88`)→`isSessionExpiredErrorCode`(`apiError.ts:124`)→`{kind:"auth_required"}` | `ErrorMessage`(`AccountDeletionPage.tsx:30`) — `legacy.errorMessage.t1({traceId})`. 전용 401 컴포넌트 없음 | 로그인 페이지 리다이렉트 UI 없음 — `ErrorMessage`만 표시 | `구현됨` (fallthrough, `AccountDeletionPage.tsx:28-36`, `errorRouting.ts:128-129`) |
| 권한 없음/MFA 필요(403) | `classifyForbidden`(`AccountDeletionPage.tsx:27`)가 true — `statusCode===403` (`shared-types/forbidden.ts:31-41`) | `ForbiddenNotice`(`AccountDeletionPage.tsx:27`) — `getApiErrorMessage`(`apiError.ts:106`) | `policy`: DenialReasons 표시, `mfa_required`: onStepUp 버튼(현재 미전달), 나머지: 재시도 버튼 없음 | `구현됨` (`AccountDeletionPage.tsx:27`, `shared-types/forbidden.ts:31`) |
| 충돌/상태 불일치(409) | `classifyBadRequest`→null, `classifyForbidden`→null, `routeApiError`(`errorRouting.ts:88`)→`classifyStateConflict` 또는 `INTEGRITY_WALLET_BALANCE_DRIFT`/`DATA_COVERAGE_MISSING` 체크 | `ErrorMessage`(`AccountDeletionPage.tsx:30`) — traceId 노출(`AccountDeletionPage.tsx:33`). 전용 409 컴포넌트 없음 | 재시도 없음(수집 후 재요청) — `ErrorMessage`만 표시 | `구현됨` (fallthrough, `AccountDeletionPage.tsx:28-36`, `errorRouting.ts:111-113`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-AccountDeletionPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 서버가 423 Locked 또는 429 Too Many Response 반환 | `AccountSecurityError` → `routeApiError`(`AccountDeletionPage.tsx:28`)가 `RoutedApiError`로 분류. `ErrorMessage`(`AccountDeletionPage.tsx:30`) 표시. 429일 때 `classifyRetry`(`shared-types/src/retryable.ts:40`)가 backoff_retry 판별 → `retryAfterSec` 카운트다운. 423 Locked는 일반 오류로 처리(구분 없음). | 429: 재시도 버튼(`legacy.errorMessage.t3`) 표시. 423: 재시도 버튼 없음(잠금 해제 대기). | `구현됨(429 부분만)` — 429는 backoff_retry 경로로 카운트다운과 재시도 버튼이 동작(`shared-types/src/retryable.ts:40`, `AccountDeletionPage.tsx:34`). 423 Locked는 별도 분류 없이 일반 ErrorMessage로 떨어짐. |
| 서버 오류(5xx) | POST `account.whitelist` 또는 POST `account.deletion`이 5xx 반환 | `AccountSecurityError`(`AccountDeletionPage.tsx:25`) → `classifyBadRequest` false, `classifyForbidden` false → `routeApiError`(`AccountDeletionPage.tsx:28`) → `ErrorMessage`(`AccountDeletionPage.tsx:30`). `Alert tone="danger"`(`AccountDeletionPage.tsx:35`). | 재시도 버튼 — `classifyRetry`가 5xx를 refetch 가능 시 `legacy.errorMessage.t3` 버튼 표시(`shared-types/src/retryable.ts:40`). | `구현됨` — 5xx는 `ErrorMessage` + `Alert tone="danger"`로 표시, 재시도 가능 시 버튼 노출. |
| 네트워크 단절 | API 호출 중 연결 끊김 — HTTP 응답 없음 | `mutateAsync` catch(`AccountDeletionPage.tsx:69`, `AccountDeletionPage.tsx:82`)에서 `err instanceof ApiError` false → `new Error(t("legacy.accountDeletionPage.t15"))` 또는 `new Error(t("legacy.accountDeletionPage.t17"))`. `AccountSecurityError` → `classifyBadRequest` false, `classifyForbidden` false, `routeApiError` — `error instanceof ApiError` false → `errorCode`/`traceId` undefined. `ErrorMessage`에 generic message만 전달(`AccountDeletionPage.tsx:30-34`). | 재시도 버튼 — `classifyRetry`가 Error를 backoff_retry로 분류하면 재시도 버튼 표시. | `구현됨(약한 형태)` — 네트워크 오류는 generic 메시지("등록에 실패했습니다." / "탈퇴 요청에 실패했습니다.")로 표시. traceId/errorCode 없음. 재시도 여부는 `classifyRetry` 판단에 의존. |
| 부분 실패(일부 데이터만 실패) | 화이트리스트 등록은 성공했으나 탈퇴 요청이 실패하거나 그 반대 | Whitelist 등록 성공 시 `whitelistEntries` 쿼리 캐시가 무효화되고(`useAccountSettings.ts:35`) 새 데이터로 리페치됨. 탈퇴 요청 실패 시 `deleteError` 설정, 폼 유지. 두 요청은 독립적 — 트랜잭션 없음. | whitelist 등록 실패: `wlError` 설정, 폼 유지, 재시도 가능. 탈퇴 실패: `deleteError` 설정, 폼 유지, 재시도 가능. | `구현됨` — 두 mutation이 독립적으로 처리됨(`AccountDeletionPage.tsx:56-85`). 한쪽 실패가 다른 쪽 데이터에 영향 없음. |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-AccountDeletionPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button type="submit">` ("목적지 등록", `AccountDeletionPage.tsx:141-142`) — `handleWhitelistSubmit`(`AccountDeletionPage.tsx:56-72`) 호출 | 1) whitelist 로딩 완료(`whitelistLoading===false`, `AccountDeletionPage.tsx:96-97`) 2) exchange/destinationAddress/password 필드 입력 (label은 선택) | 1) `registerWhitelist.mutateAsync()`(`AccountDeletionPage.tsx:60-65`)가 POST `account.whitelist` 전송 2) 성공 시 `onSuccess` 훅(`shared-hooks/src/useAccountSettings.ts:35`)이 `{queryKey:["whitelistEntries"]}` 무효화 → `useWhitelistEntries` 자동 리페치 3) 폼 필드 초기화(`AccountDeletionPage.tsx:66-68`) | `mutateAsync` catch(`AccountDeletionPage.tsx:69`) → `setWlError(err)`(`AccountDeletionPage.tsx:70`) → `<AccountSecurityError error={wlError} />`(`AccountDeletionPage.tsx:140`)가 `BadRequestNotice`/`ForbiddenNotice`/`ErrorMessage`로 라우팅 (`AccountDeletionPage.tsx:25-37`) | 같은 화면 (whitelist 목록 업데이트) |
| `<Button type="button">` ("로그아웃", `AccountDeletionPage.tsx:151-159`) — inline `onClick`(`AccountDeletionPage.tsx:154-157`) | 1) 탈퇴 성공 후 (`deletionResult !== null`, `AccountDeletionPage.tsx:148`) 2) `deletionResult`에 예약 시각 문자열 포함 (`AccountDeletionPage.tsx:79-81`) | 1) `logout()`(`AccountDeletionPage.tsx:155`) → `client.logout()`(`shared-hooks/src/useAuth.ts:51-59`)으로 서버 `POST /auth/logout` 호출 + 로컬 스토어 정리 + `qc.clear()`로 쿼리 캐시 삭제 2) `navigate("/login")`(`AccountDeletionPage.tsx:156`) | try/catch 없음 — `logout()`에서 던지는 에러가 React error boundary 처리 또는 브라우저 에러 표시 | `/login` 페이지 (`router.tsx:150` 경로 없음, `react-router-dom` navigate) |
| `<Button type="submit" variant="danger">` ("탈퇴 요청", `AccountDeletionPage.tsx:175-176`) — `handleDeleteSubmit`(`AccountDeletionPage.tsx:74-85`) 호출 | 1) 탈퇴 요청 전 (`deletionResult === null`, `AccountDeletionPage.tsx:161`) 2) `deletePassword` 필드에 비밀번호 입력 (`AccountDeletionPage.tsx:165-173`) 3) `required` 속성으로 브라우저 검증 | 1) `requestDeletion.mutateAsync({password: deletePassword})`(`AccountDeletionPage.tsx:78`)가 POST `account.deletion` 전송 2) 서버 응답 `result.deletionEffectiveAt`를 `new Date().toLocaleString()`으로 포맷 → `setDeletionResult`(`AccountDeletionPage.tsx:79-81`) 3) `<Alert tone="success">`(`AccountDeletionPage.tsx:150`) + "로그아웃" 버튼(`AccountDeletionPage.tsx:151-159`) 표시 4) 폼이 비활성화됨 | `mutateAsync` catch(`AccountDeletionPage.tsx:82`) → `setDeleteError(err)`(`AccountDeletionPage.tsx:83`) → `<AccountSecurityError error={deleteError} />`(`AccountDeletionPage.tsx:174`)가 `BadRequestNotice`/`ForbiddenNotice`/`ErrorMessage`로 라우팅 (`AccountDeletionPage.tsx:25-37`) | 같은 화면 (탈퇴 성공 시 성공 메시지 + 로그아웃 버튼 표시, `AccountDeletionPage.tsx:148-160`) |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-AccountDeletionPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-AccountDeletionPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:26`) | — (판단 함수, 문구 없음) | 400 응답을 분류(`shared-types/badRequest.ts:34`). true 반환 시 `BadRequestNotice` 표시 | 없음 |
| `BadRequestNotice` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:26`) | `getApiErrorMessage(errorCode, message)` — `shared-types/apiError.ts` EXACT_MESSAGES 매핑 | `field`: 인라인 필드 오류 중복 방지로 배너 숨김(`shared-types/badRequest.ts:38`). `disclosure_retired`: '최신 revision 재조회' 버튼(`shared-types/badRequest.ts:40`). `mfa_invalid`: '코드 재입력' 버튼(`shared-types/badRequest.ts:41`). 나머지: 재시도 버튼 없음. `Alert tone="danger"` | 노출 안 함 |
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:27`) | — (판단 함수, 문구 없음) | 403 응답을 분류(`shared-types/forbidden.ts:31`). true 반환 시 `ForbiddenNotice` 표시 | 없음 |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:27`) | `getApiErrorMessage(errorCode, message)` — `shared-types/apiError.ts` EXACT_MESSAGES 매핑 | `policy`: `DenialReasons`(`frontend/apps/web/src/components/DenialReasons.tsx`) 하위 컴포넌트 표시. `mfa_required`: `onStepUp` 콜백 버튼(현재 호출부에서 전달 안 함, `ForbiddenNotice.tsx:42-47`). 나머지: 재시도 버튼 없음. `Alert tone="danger"` | 노출 안 함 |
| `routeApiError` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:28`) | — (판단 함수, 문구 없음) | 400/403 외 ApiError를 `RoutedApiError`(`shared-types/errorRouting.ts:88`)로 분류. kind="backoff_retry"면 `afterSec` 반환 | `ErrorMessage`에서 traceId 받음 |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:30`) | `getApiErrorMessage(errorCode, message)` — `shared-types/apiError.ts` EXACT_MESSAGES 매핑. traceId 노출 시 `legacy.errorMessage.t1({traceId})` | `classifyRetry`(`shared-types/src/retryable.ts:40`)로 backoff/refetch 판별. backoff일 때 retryAfterSec 카운트다운. 재시도 가능 시 `legacy.errorMessage.t3` 버튼. `Alert tone="danger"` | `legacy.errorMessage.t1`에 traceId 삽입 노출(`AccountDeletionPage.tsx:33`) |
| `Alert` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:150`) | `deletionResult` 문자열 — `legacy.accountDeletionPage.t16({val})` (`AccountDeletionPage.tsx:80`) | 탈퇴 성공 시 `tone="success"` 알럿. 탈퇴 예약 시각 표시. 로그아웃 버튼(`legacy.accountDeletionPage.t11`) 아래 배치 | 노출 안 함 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-AccountDeletionPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-AccountDeletionPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-AccountDeletionPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
