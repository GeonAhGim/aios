# J6-ApprovalSettingsPage — `/settings/approval`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ApprovalSettingsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/settings/approval` / `frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-ApprovalSettingsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 — 개인 트레이더(투자자). 여정 J6("설정·승인·감사")의 주체. 승인 방식(SOLO/DUAL)을 직접 설정·변경한다. [확인: PERSONAS_AND_JOURNEYS.md §3 표 — J6는 P1(+P2) 중심] |
| 목적(한 문장) | 거래 실행의 승인 방식을 SOLO(1인 승인, 강제 대기 60초) 또는 DUAL(서로 다른 두 계정 순차 서명) 중 하나로 설정하고, DUAL 모드일 때 2차 승인자 연락처를 등록한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:148` — `/settings/approval` 라우트는 `protect(<ApprovalSettingsPage />)`로 보호됨. `protect()`는 `ProtectedRoute` 컴포넌트(`frontend/apps/web/src/components/ProtectedRoute.tsx`)를 통과해야 함: (1) 로그인 세션 보유(`router.tsx:70-72`), (2) MFA 활성화(`ProtectedRoute.tsx:32-34`), (3) 위험도 평가 완료(`ProtectedRoute.tsx:35-41`). FeatureFlagGate 미적용(`router.tsx:93-111` 중 없음). |
| 사용자 준비물 | 로그인 세션, MFA 설정 완료, 위험도 평가 완료. DUAL 모드 선택 시 2차 승인자의 연락처 정보(이메일 또는 전화번호). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-ApprovalSettingsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useApprovalSettings` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:9` | 마운트 시 1회 조회(`useQuery`, `useAccountSettings.ts:10-13`). refetchInterval 없음(`useAccountSettings.ts:10-13` grep 결과 0건). 이후 재조회는 QueryClient 기본값(`staleTime: 0`, `refetchOnWindowFocus: false`)에 의존하므로 미확인. | 페이지는 `data`와 `isLoading`만 구조분해(`ApprovalSettingsPage.tsx:46`). 훅의 `error`는 화면에 렌더링되지 않음(`:97-98`에서 로딩만 처리). `settings`가 `undefined`이면 조건부 렌더링(`:128`)이 false가 되어 폼 기본값(SOLO)으로 그대로 보임. |
| `useUpdateApprovalSettings` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:16` | mutation(`useMutation`, `useAccountSettings.ts:18`). 성공 시 `onSuccess`에서 `["approvalSettings"]` 무효화(`useAccountSettings.ts:20`). HTTP 메서드는 `apiClient.updateApprovalSettings`를 열어 보기 전까지 미확인. | `attemptUpdate` catch(`ApprovalSettingsPage.tsx:62-86`) 처리. `ApiError` + 위험등급 문구(`t8`) 포함 시 `RiskWarningModal`(`:77-80`). 그 외 `ApiError`는 `setError(err)` → `UpdateSettingsError`(`:30-42`)가 400/403/그 외 분기(`:132`). `ApiError` 아님 → `t9` 메시지(`:83-85`). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-ApprovalSettingsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `isLoading`이 true인 경우 (`useApprovalSettings` 훅이 아직 데이터를 받지 않음). `ApprovalSettingsPage.tsx:46` 구조분해, `:97` 조건부 렌더링. | 헤더(`title1`)와 `TenantSwitcher`는 표시되고, 폼 영역은 `<LoadingState />`로 대체됨(`:98`). `LoadingState`는 회색 스피너 + "불러오는 중..." 문구(`frontend/packages/ui-web/src/Feedback.tsx:31-37`). 저장 버튼은 로딩 중 숨김. | 없음(데이터 수신 대기). | 구현됨(`:97-98`) |
| 빈 상태 | 로딩이 끝났지만 `settings`가 `undefined`인 경우(조회 실패 또는 빈 응답). `ApprovalSettingsPage.tsx:46`에서 `data`가 `undefined`. | 전용 빈 상태 UI나 문구 없음. `settings`가 truthy하지 않아 `:128`의 `{settings && ...}` 줄이 숨겨지고(`t6` 미표시), 폼은 SOLO 기본값으로 그대로 표시됨. | 없음(에러 리트리 또는 페이지 재방문만 가능). | 없음(기본값 노출 — `:48`의 `useState<"SOLO" \| "DUAL">("SOLO")`, `:128` 조건부 렌더링이 `undefined`일 때 폼을 그대로 둠). |
| 정상 | `isLoading`이 false이고 `settings`가 존재하는 경우. `ApprovalSettingsPage.tsx:46`. | (1) 헤더에 `title1`("승인 방식 설정") 표시(`:92`). (2) `TenantSwitcher` 표시(`:93`). (3) `canTrade`가 false일 때 `t2` 경고(`:94-96`). (4) 라디오 버튼: `t3`(SOLO, `:109`), `t4`(DUAL, `:117`). (5) DUAL 선택 시 `label5` 필드 + `Input`(`:119-127`). (6) `t6`에 `mandatoryWaitSeconds` 값 삽입(`:129-130`). (7) 저장 버튼 `t7`(`:133-139`), `canTrade`로 비활성화. (8) `useEffect(:58-60)`가 `settings.mode`를 `mode` 상태에 반영. | (1) SOLO/DUAL 라디오 선택 — `setMode`(`:104`, `:114`). (2) DUAL일 때 `label5` 입력 — `setSecondApproverContact`(`:124`). (3) 저장 버튼 클릭 — `attemptUpdate(false)`(`:135`), `canTrade`가 false면 비활성화(`:137`). (4) 위험등급 경고 시 `RiskWarningModal` — `attemptUpdate(true)`(`:148`). | 구현됨(`:92-139`) |
| 낡은 데이터(신선도 초과) | 캐시된 `settings`가 stale인 경우. 재조회 정책: `useApprovalSettings` 훅에 주기 재조회/만료 시간 옵션 없음(`frontend/packages/shared-hooks/src/useAccountSettings.ts:10-13`). `QueryClient`는 `new QueryClient()`로 기본값만 사용(`frontend/apps/web/src/main.tsx:15`). | 화면에 갱신 시각이나 신선도 표시 없음. 재조회 시 `ApprovalSettingsPage.tsx:58-60`의 `useEffect`가 `settings`를 `mode` 상태에 덮어쓰므로(`:59`), 사용자가 고른 미저장 라디오 값이 서버 값으로 리셋될 수 있음. | 윈도우 포커스 시 재조회 — 기본값 false이므로 발생 안 함. 수동 갱신 버튼 없음. | 없음(신선도 표시 없음). 갭: `:58-60`의 `useEffect`가 재조회 시 미저장 값을 덮어씀(`:59`). 재조회 정책: 기본값 즉시 stale, 포커스 시 재조회 기본 false → 발생 안 함. |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-ApprovalSettingsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `attemptUpdate` catch에서 `err instanceof ApiError`이고 `err.statusCode === 400`인 경우. `ApprovalSettingsPage.tsx:72-82`. `classifyBadRequest`가 `BadRequestKind`(`shared-types/src/badRequest.ts:34`) 반환. | `UpdateSettingsError`(`:30-42`)가 `classifyBadRequest(error)`로 판정 → `BadRequestNotice`(`:31`) 렌더링. `BadRequestNotice`는 `<Alert tone="danger">` + `<p>{getApiErrorMessage(errorCode, message)}</p>`. 400 응답의 `error_code`에 따른 한글 문구(`shared-types/src/apiError.ts:EXACT_MESSAGES` 또는 서버 `message`). field 갈래且 details.fields가 비어있을 때만 배너 표시. trace_id 미노출. | 저장 버튼 클릭(`:135`) → `attemptUpdate(false)`(`:135`) 재시도 가능. `BadRequestNotice`의 `onReload`(`BadRequestNotice.tsx:24`), `onFocusMfaCode`(`BadRequestNotice.tsx:25`)는 미전달(`:31`). | 구현됨(`:31`, `:135`) |
| 인증 필요/세션 만료(401) | `attemptUpdate` catch에서 `err instanceof ApiError`이고 `err.statusCode === 401`인 경우. `ApprovalSettingsPage.tsx:72-82`. `classifyBadRequest(401)` → null, `classifyForbidden(401)` → null. `routeApiError(401)` → `isSessionExpiredErrorCode`(`frontend/packages/shared-types/src/apiError.ts:124`) 판정 → `{kind: "auth_required"}`. | `routeApiError` 판정 후 `ErrorMessage`(`:35-40`) 렌더링. `<Alert tone="danger">` + `<p>{getApiErrorMessage(errorCode, message)}</p>`. `traceId`가 있으면 `legacy.errorMessage.t1`로 표시(`ErrorMessage.tsx:60`). 재시도 불가(kind="auth_required" → classifyRetry "none"). | 재시도 버튼 없음(`ErrorMessage`에서 `classifyRetry`가 "none"). 세션 만료이므로 재로그인 필요. | 없음(`routeApiError`는 `ErrorMessage`로만 폴백 — 전용 401 처리 없음) |
| 권한 없음/MFA 필요(403) | `attemptUpdate` catch에서 `err instanceof ApiError`이고 `err.statusCode === 403`인 경우. `ApprovalSettingsPage.tsx:72-82`. `classifyForbidden`이 `ForbiddenKind`(`shared-types/src/forbidden.ts:31`) 반환(mfa_required/tenant_mismatch/policy/forbidden). | `UpdateSettingsError`가 `classifyForbidden(error)`로 판정 → `ForbiddenNotice`(`:32`) 렌더링. `ForbiddenNotice`는 `<Alert tone="danger">` + `<p>{getApiErrorMessage(errorCode, message)}</p>`. kind="policy" → `<DenialReasons error={error}>` 표시(`ForbiddenNotice.tsx:41`). trace_id 미노출. | 재시도 버튼 없음(`ForbiddenNotice`의 `onStepUp`은 미전달(`:32`)). 정책 거부이므로 관리자에게 문의 필요. | 구현됨(`:32`) |
| 충돌/상태 불일치(409) | `attemptUpdate` catch에서 `err instanceof ApiError`이고 `err.statusCode === 409`인 경우. `ApprovalSettingsPage.tsx:72-82`. `classifyBadRequest(409)` → null, `classifyForbidden(409)` → null. `routeApiError(409)` → `classifyIdempotencyFailure`(`frontend/packages/shared-types/src/idempotencyFailure.ts:33`) 또는 `classifyStateConflict`(`frontend/packages/shared-types/src/stateConflict.ts:33`) 판정. | `routeApiError` 판정 후 `ErrorMessage`(`:35-40`) 렌더링. `<Alert tone="danger">` + `<p>{getApiErrorMessage(errorCode, message)}</p>`. `traceId`가 있으면 표시. DATA_COVERAGE_MISSING/WALLET_BALANCE_DRIFT 갈래는 재시도 불가. 그 외 409는 `classifyStateConflict` → "invalid_transition" 또는 "refetch_retry". | 재시도 불가(idempotency_conflict) 또는 refetch_retry 가능(state_conflict). `ErrorMessage`에서 `classifyRetry`(`frontend/packages/shared-types/src/retryable.ts:40`) 판정에 따라 버튼 표시. | 없음(`routeApiError`는 `ErrorMessage`로만 폴백 — 전용 409 처리 없음) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-ApprovalSettingsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 서버가 429(RATE_LIMIT_EXCEEDED) 또는 423(LOCKED) 반환. `frontend/packages/api-client/src/http.ts:83` — `buildApiError`가 `ApiError` 던짐. 429 응답에는 봉투에 `retry_after_seconds` 포함(`frontend/packages/api-client/src/envelope.ts:59-60`). | 429: ApprovalSettingsPage.tsx의 UpdateSettingsError 함수(`:30-42`)가 `routeApiError(error)`로 분류 → `ErrorMessage`(`:35-41`)가 `classifyRetry`로 "backoff" 판정. `retryAfterSec` 카운트다운 + 재시도 버튼(`ErrorMessage.tsx:49-53`). 423: `classifyRetry`가 "none" → 재시도 버튼 없이 traceId만 표시(`ErrorMessage.tsx:60`). | 429 — `ErrorMessage.tsx:66` 재시도 버튼(카운트다운 0 후 활성화). 423 — 재시도 불가(버튼 없음). | 429 구현됨(`http.ts:83`, `envelope.ts:60`, `ErrorMessage.tsx:41-53`). 423 구현됨(`ErrorMessage.tsx:39` "none" 분기). |
| 서버 오류(5xx) | 서버가 5xx 상태 코드 반환. `http.ts:83` — `buildApiError`가 `ApiError` 던짐. `frontend/packages/api-client/src/http.ts:79-83` — 5xx 응답도 `buildApiError`로 `ApiError` 던짐. | ApprovalSettingsPage.tsx의 UpdateSettingsError 함수(`:30-42`)가 `routeApiError(error)`로 분류 → `ErrorMessage`(`:35-41`)가 `classifyRetry`로 "server_fatal" 판정. `<Alert tone="danger">` + `<p>{text}</p>` + traceId(`ErrorMessage.tsx:58-60`). 재시도 버튼 없음(`classifyRetry` "none" 분기). | 없음(재시도 불가 — 서버 재발생 전까지 의미 있는 재시도 없음). | 구현됨(`http.ts:83`, `ErrorMessage.tsx:38-39` "server_fatal" → "none"). |
| 네트워크 단절 | fetch 또는 네트워크 연결 실패. `http.ts:92` — `handleRequestFailure`가 예외 캐치. `ApprovalSettingsPage.tsx:71-85` — `catch`에서 `err instanceof ApiError`가 false → `new Error(t("legacy.approvalSettingsPage.t9"))`로 감싸서 `setError`. | `ErrorMessage`(`:132`)가 `<Alert tone="danger">` + `<p>{text}</p>` 렌더링. 문구는 `legacy.approvalSettingsPage.t9`("설정 변경에 실패했습니다.")(`:84`). traceId 없음(`:38` undefined). 재시도 버튼 없음(`classifyRetry` "none"). | 없음(재시도 버튼 없음). | 구현됨(`ApprovalSettingsPage.tsx:83-85`, `ErrorMessage.tsx:38-39`). |
| 부분 실패(일부 데이터만 실패) | `useApprovalSettings`(GET)는 성공했으나 `useUpdateApprovalSettings`(mutation)이 실패한 경우, 또는 그 반대. `ApprovalSettingsPage.tsx:46` — `useApprovalSettings` 훅의 `error`는 구조분해 없이 무시됨(`:97-98`에서 `isLoading`만 확인). | GET 실패 시: `settings`가 `undefined` → `:128` `{settings && ...}` 숨김. 폼은 SOLO 기본값(`:48`)으로 표시. 전용 에러 UI 없음. mutation 실패 시: `UpdateSettingsError`(`:132`)가 해당 오류 범주(400/403/그 외)로 분기. | GET 실패 — 페이지 재방문 또는 수동 refetch. mutation 실패 — `UpdateSettingsError` 경로로 분기. | GET 실패 미구현(에러 무시, 기본값 노출 — `:46` 구조분해에서 `error` 누락). mutation 실패 구현됨(`:71-85`, `:132`). |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-ApprovalSettingsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:133`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-ApprovalSettingsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-ApprovalSettingsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:4`, `frontend/packages/shared-types/src/badRequest.ts:34`, `frontend/apps/web/src/components/BadRequestNotice.tsx:40`) | `getApiErrorMessage()` 반환값(한글) — `shared-types/src/apiError.ts:EXACT_MESSAGES` 또는 서버 `message` | `BadRequestKind`가 "field"(details.fields 비어 있을 때) → 배너만 표시, 버튼 없음. "disclosure_retired" → `onReload` 버튼(미전달). "mfa_invalid" → `onFocusMfaCode` 버튼(미전달). 그 외 → 버튼 없음. | 없음(`BadRequestNotice`가 traceId 필드를 받지 않음) |
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:5`, `frontend/packages/shared-types/src/forbidden.ts:31`, `frontend/apps/web/src/components/ForbiddenNotice.tsx:31`) | `getApiErrorMessage()` 반환값(한글) — `shared-types/src/apiError.ts:EXACT_MESSAGES` 또는 서버 `message` | `ForbiddenKind`가 "policy" → `DenialReasons` 컴포넌트 표시, 버튼 없음. "mfa_required" → `onStepUp` 버튼(미전달). 그 외 → 버튼 없음. | 없음(`ForbiddenNotice`가 traceId 필드를 받지 않음) |
| `routeApiError` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:7`) | `ErrorMessage` 컴포넌트 내부에서 `getApiErrorMessage()` 호출 → 한글 문구 | `RoutedApiError.kind`에 따라 다름: "backoff_retry" → retryAfterSec 카운트다운 후 재시도 버튼 활성화. "refetch_retry" → 즉시 재시도 버튼 활성화. "server_fatal" → traceId만 표시, 재시도 불가. "unknown" → 일반 오류 배너. 그 외 → 재시도 버튼 없음. | `ErrorMessage` 컴포넌트가 `traceId`를 받아 `legacy.errorMessage.t1` 키로 표시(`ErrorMessage.tsx:60`) |
| `BadRequestNotice` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:31`, `frontend/apps/web/src/components/BadRequestNotice.tsx:40`) | `getApiErrorMessage(errorCode, message)` 반환값(한글). `shared-types/src/apiError.ts:getApiErrorMessage` | `<Alert tone="danger">` + `<p>{text}</p>`. kind="field"且 details.fields가 비어있을 때만 배너 표시(보통은 useFieldErrors가 폼 인라인 표시). kind="disclosure_retired" → 새로고침 버튼(미전달). kind="mfa_invalid" → MFA 코드 재입력 버튼(미전달). | 없음 |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:32`, `frontend/apps/web/src/components/ForbiddenNotice.tsx:31`) | `getApiErrorMessage(extractErrorCode(error), extractMessage(error))` 반환값(한글) | `<Alert tone="danger">` + `<p>{text}</p>`. kind="policy" → `<DenialReasons error={error}>` 표시(거부 사유 목록). kind="mfa_required" → step-up 버튼(미전달). 그 외 → 버튼 없음. | 없음 |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:35`) | `getApiErrorMessage(errorCode, message)` 반환값(한글). `shared-types/src/apiError.ts:getApiErrorMessage` | `<Alert tone="danger">` + `<p>{text}</p>`. `traceId`가 있으면 `legacy.errorMessage.t1` 키로 표시. `classifyRetry`가 "none"이 아니면 재시도 버튼(백오프일 때 retryAfterSec 카운트다운, refetch일 때 즉시 활성화). `fieldErrors`가 있으면 렌더링 건너뜀(중복 방지). | 있음 — `legacy.errorMessage.t1` i18n 키, `{traceId: traceId}` 파라미터(`ErrorMessage.tsx:60`) |
| `Alert` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:95`) | `legacy.approvalSettingsPage.t2` — "감사자(읽기전용) 역할이라 이 테넌트의 승인 방식을 변경할 수 없습니다." | `canTrade`가 false일 때 경고 배너 표시. 저장 버튼은 `disabled={!capabilities.canTrade}`(`:137`)로 비활성화. 재시도/액션 버튼 없음. | 없음 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-ApprovalSettingsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-ApprovalSettingsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-ApprovalSettingsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
