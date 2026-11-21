# J6-SessionsPage — `/settings/sessions`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/SessionsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/settings/sessions` / `frontend/apps/web/src/routes/settings/SessionsPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-SessionsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 — 개인 트레이더(투자자). PERSONAS_AND_JOURNEYS.md §3: J6(설정·승인·감사) 여정의 주 페르소나. |
| 목적(한 문장) | 사용자가 로그인한 활성 세션(기기)을 확인하고, 의심 세션을 폐기하거나 전체 로그아웃하여 계정 접근을 관리한다. |
| 진입 조건 | `router.tsx:151` — 경로 `/settings/sessions`, `protect(<SessionsPage />)`으로 `ProtectedRoute` 가드 적용(로그인 필요). |
| 사용자 준비물 | 로그인 상태(access token 보유). `SessionsPage.tsx:51` — `VITE_API_BASE_URL` 환경 변수(기본 `http://localhost:8000`). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-SessionsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| 세션 목록 (`useQuery`, queryKey: `["sessions"]`) | `SessionsPage.tsx:132-135` — `queryFn: fetchSessions` | 마운트 시 1회 조회. 폴링 미설정(반복 갱신 없음). `queryClient.invalidateQueries({ queryKey: ["sessions"] })`로 수동 갱신 — `handleRevoke` 성공 시(현재 세션 아닌 경우) `SessionsPage.tsx:157`에서 호출. | `ErrorBanner` 컴포넌트(`SessionsPage.tsx:63-76`)가 오류 분류: `classifyForbidden`(`SessionsPage.tsx:10`) true → `ForbiddenNotice`(`SessionsPage.tsx:64`) 표시. 그 외 → `routeApiError`(`SessionsPage.tsx:13`)로 라우트 후 `ErrorMessage`(`SessionsPage.tsx:68`) 표시. `ErrorMessage`는 `backoff_retry` 또는 `refetch_retry`일 때 `onRetry` 버튼 제공(사용자 재시도). `fetchSessionsDefault`(`SessionsPage.tsx:53-54`)는 현재 `SessionsRouteNotImplementedError`를 던짐(서버 API 미구현 중). |
| 개별 세션 폐기 (`handleRevoke`) | `SessionsPage.tsx:142-163` — `client.revoke(view.sessionId)` | 사용자가 "폐기" 버튼 클릭 시 1회 요청(`SessionsPage.tsx:105-106`). `window.confirm` 확인 필요. | `try/catch`로 `actionError`에 저장(`SessionsPage.tsx:159`) → `ErrorBanner(error={actionError})`(`SessionsPage.tsx:188`) 표시. 현재 세션이면 `navigate("/login")`(`SessionsPage.tsx:154`)으로 리다이렉트. 비동기 실패 시 `setPendingId(null)`(`SessionsPage.tsx:161`)로 버튼 비활성화 해제. |
| 전체 로그아웃 (`handleRevokeAll`) | `SessionsPage.tsx:168-174` — `client.revokeAll()` → `logoutClient.logoutAll()` | 사용자가 "전체 로그아웃" 버튼 클릭 시 1회 요청(`SessionsPage.tsx:182-183`). `window.confirm` 확인 필요(`SessionsPage.tsx:169`). | `logout.ts`의 logoutAll은 "best-effort"(서버 결과 무시, 로컬 정리만 보장, task-454 decision) — 여기서 성공/실패 분기하지 않음. 무조건 `navigate("/login", { replace: true })`(`SessionsPage.tsx:173`). `pendingAll` 상태(`SessionsPage.tsx:122`)로 버튼 로딩 표시. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-SessionsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | 최초 조회 중 (`!query.isError && query.isLoading`, :190). 재조회(`invalidateQueries`, :157) 중에는 `isLoading`이 아니므로 로딩 표시가 나오지 않는다. | `LoadingState` — "불러오는 중..." (:35, Feedback.tsx:35). 시트 밖 문구. | 없음(대기). | 구현됨 `SessionsPage.tsx:190` |
| 빈 상태 | 조회 성공이고 `rows.length === 0` (:191). `rows`는 :137에서 `query.data ?? []`로 만든다. | `EmptyState` — `legacy.sessionsPage.t8` = "활성 세션이 없습니다." (:191). | 없음. 페이지 헤더의 「전체 로그아웃」 버튼(:182-183)은 그대로 보인다. | 구현됨 `SessionsPage.tsx:191` |
| 정상 | 조회 성공이고 `rows.length > 0` (:192-212). 파싱 실패 항목(`parseSessionView`가 null)은 `legacy.sessionsPage.t9` "세션 정보를 해석할 수 없습니다."(:206) Alert가 나온다. | 행마다 — `legacy.sessionsPage.t1` "이 기기"(현재 세션, :96), `t2` "폐기됨"(폐기 시각 있음, :97), `t3`·`t4` 생성/최근 활동 시각(:100), `t5` "폐기" 버튼(`canRevoke`일 때만, :104-106). 제목 `title6` "활성 세션"(:180), 버튼 `t7` "전체 로그아웃"(:183). | 「폐기」(`handleRevoke`, :142-163), 「전체 로그아웃」(`handleRevokeAll`, :168-174). | 구현됨 `SessionsPage.tsx:192-212` (행별 :104-106 인용) |
| 낡은 데이터(신선도 초과) | `useQuery`(:132-135)에 staleTime·refetchInterval 설정 없음. 갱신 경로는 마운트 시 1회 조회와 폐기 성공 후 `invalidateQueries`(:157)뿐. 전역 `QueryClient`(main.tsx:15)도 기본 생성자로 설정 없음. | 오래된 데이터를 "낡음" 표시로 알려주는 문구는 시트에 없다. 재조회 실패(`query.isError`) 시 기존 목록은 숨겨지고 `ErrorBanner`만 보인다(:187, :192 가드). | 「다시 시도」 버튼(`onRetry`, :187, :73, `refetch_retry`/`backoff_retry`일 때만). | 「낡음 표시」 없음(근거: :132-135에 staleTime/refetchInterval 없음, main.tsx:15 기본 생성자). 「다시 시도」 구현됨 `SessionsPage.tsx:187` |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-SessionsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `query.isError`(`:187`) 또는 `actionError`(`:188`)이고 `routeApiError`가 `{kind:"field_errors"}` 반환. `statusCode===400`且 errorCode가 `VALIDATION_*`. | `ErrorBanner`(`SessionsPage.tsx:63-76`) → `classifyForbidden` null → `routeApiError` → `ErrorMessage`(`SessionsPage.tsx:68`)가 오류 문구 표시. 필드 오류일 경우 `fieldErrors`로 개별 필드 안내(`ErrorMessage.tsx:55`). | 「다시 시도」(`onRetry` → `query.refetch()`, :187). 필드 오류는 필드별 수정 후 재전송(현재 세션 목록 조회는 재조회만 지원). | 구현됨 `SessionsPage.tsx:187` |
| 인증 필요/세션 만료(401) | `statusCode===401`且 `isSessionExpiredErrorCode(errorCode)` true (`errorRouting.ts:128-129`). 세션 토큰 만료 또는 유효하지 않은 토큰. | `ErrorBanner` → `classifyForbidden` null → `routeApiError` → `{kind:"auth_required"}` → `ErrorMessage`(`SessionsPage.tsx:68`)가 오류 문구 표시. 재시도 버튼 없음(`classifyRetry` "none"). | 없음(로그인 필요). 사용자가 수동 재로그인 후 재접속. | 구현됨 `SessionsPage.tsx:187` |
| 권한 없음/MFA 필요(403) | `statusCode===403` (`forbidden.ts:32`). errorCode가 `AUTHZ_FORBIDDEN`/`AUTH_MFA_REQUIRED`/`AUTH_TENANT_MISMATCH`/`POLICY_*`/`RISK_*` 중 하나. | `ErrorBanner` → `classifyForbidden`(`SessionsPage.tsx:10`) true → `ForbiddenNotice`(`SessionsPage.tsx:64`) 표시. 오류 문구 표시, policy 갈래면 `DenialReasons` 상세(`ForbiddenNotice.tsx:41`). mfa_required면 `legacy.forbiddenNotice.t1` 버튼(`ForbiddenNotice.tsx:45`). | mfa_required일 때 `onStepUp` 버튼(현재 미배정). 정책 거부 시 사유 확인 후 정책 변경 요청. | 구현됨 `SessionsPage.tsx:64` |
| 충돌/상태 불일치(409) | `statusCode===409`且 errorCode가 `DATA_COVERAGE_MISSING`/`INTEGRITY_WALLET_BALANCE_DRIFT` 등 (`errorRouting.ts:104-113`). | `ErrorBanner` → `classifyForbidden` null → `routeApiError` → 409 갈래(`data_coverage_missing`/`wallet_balance_drift`/`invalid_transition`/`refetch_retry`) → `ErrorMessage`(`SessionsPage.tsx:68`)가 오류 문구 표시. `refetch_retry`일 때만 재시도 버튼. | `refetch_retry`일 때 「다시 시도」. 그 외 409는 수집 후 재요청 또는 안내만. | 구현됨 `SessionsPage.tsx:187` |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-SessionsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | API 응답 상태 423 (`accountLockout.ts:10` `AUTH_ACCOUNT_LOCKED`) 또는 429 (`retryable.ts:28` `RATE_LIMIT_EXCEEDED`). `ErrorBanner`(`SessionsPage.tsx:63-76`) → `classifyForbidden`(`SessionsPage.tsx:10`) null → `routeApiError`(`SessionsPage.tsx:13`) → `deriveLockout`(errorRouting.ts:125, 423 → `account_locked`) 또는 `classifyRetry`(errorRouting.ts:119, 429 → `backoff_retry`). | 423 → `ErrorMessage`(`SessionsPage.tsx:68`)에 잠금 안내(traceId 없음). 429 → `ErrorMessage`에 백오프 재시도 안내(`retryAfterSec` 표시). 둘 다 `SessionsPage.tsx:187`(query error) 또는 :188(action error)에서 표시. | 423 → 대기(재시도 버튼 없음). 429 → `ErrorMessage`의 `onRetry` prop(`SessionsPage.tsx:73`)으로 `query.refetch()` 재시도(`retryAfterSec` 후). | 423 구현됨 errorRouting.ts:125-126 → `ErrorMessage`. 429 구현됨 errorRouting.ts:119-121 → `ErrorMessage`(`retryAfterSec` 제공). |
| 서버 오류(5xx) | API 응답 상태 500/502/503. `routeApiError` → `classifyServerError`(errorRouting.ts:115): 502/503 → `backoff_retry`, 500 → `server_fatal`. | 502/503 → `ErrorMessage`에 백오프 재시도 안내. 500 → `ErrorMessage`에 `traceId`(`SessionsPage.tsx:71`) 노출, 재시도 불가 안내. | 502/503 → `onRetry` prop(`SessionsPage.tsx:73`)으로 재시도. 500 → 대기 또는 관리자에게 trace_id 보고. | 502/503 구현됨 errorRouting.ts:116 → `backoff_retry`. 500 구현됨 errorRouting.ts:117 → `server_fatal`(traceId 노출). |
| 네트워크 단절 | `fetchSessions`(`SessionsPage.tsx:134`)가 네트워크 실패(타임아웃, 연결 끊김 등)로 예외 던짐. `query.isError` true, `classifyForbidden` null, `routeApiError` → `{kind: "unknown"}`. | `ErrorBanner`(`SessionsPage.tsx:63-76`)(:187) → `ErrorMessage`에 오류 메시지 표시. `canRetry` false (`SessionsPage.tsx:66`, errorRouting.ts:132 → `unknown`) → 재시도 버튼 없음. | 사용자가 페이지 새로고침 또는 브라우저 재시도 수동으로 재조회. | 구현됨(표시) — 재시도 버튼 없음(`ErrorMessage`에서 `onRetry` 미제공, :66-73). |
| 부분 실패(일부 데이터만 실패) | `parseSessionView`(`SessionsPage.tsx:139`)가 일부 raw 항목에서 null 반환. `rows`(`SessionsPage.tsx:137-140`)에 `view: null` 항목이 섞임. | 정상 파싱 행은 `SessionRow`(`SessionsPage.tsx:197-203`)로 표시. 실패 행은 `Alert tone="danger"`(`SessionsPage.tsx:206`)로 `legacy.sessionsPage.t9` "세션 정보를 해석할 수 없습니다." 표시. | 실패 행에 대한 사용자 행동 없음(정보 전용). 정상 행은 여전히 `폐기` 버튼 사용 가능. | 구현됨 `SessionsPage.tsx:192-212` (행별 :196-208 분기). |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-SessionsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:105`) — 폐기 | `view.canRevoke` true(`SessionsPage.tsx:104`), 현재 세션 아님(`isCurrentSession` false). `disabled` false(`SessionsPage.tsx:104`, `pendingId !== view.sessionId`). | `window.confirm` 확인 후 `handleRevoke(view)`(`SessionsPage.tsx:142-163`) — `client.revoke(view.sessionId)`(`SessionsPage.tsx:146`). 성공 시 `setPendingId(view.sessionId)`(`SessionsPage.tsx:148`) → `queryClient.invalidateQueries({queryKey:["sessions"]})`(`SessionsPage.tsx:157`)로 목록 갱신. 현재 세션이면 `navigate("/login",{replace:true})`(`SessionsPage.tsx:154`). | `client.revoke`가 예외 던짐 → `actionError`에 저장(`SessionsPage.tsx:159`) → `ErrorBanner(error={actionError})`(`SessionsPage.tsx:188`) 표시. `setPendingId(null)`(`SessionsPage.tsx:161`)로 버튼 비활성화 해제. | 동일 화면(`SessionsPage.tsx:188` 에러 표시 후 대기). |
| `<Button>` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:182`) — 전체 로그아웃 | 사용자가 "전체 로그아웃" 버튼 클릭. `pendingAll` false(`SessionsPage.tsx:182`, `loading={pendingAll}`으로 로딩 가드). | `window.confirm` 확인(`SessionsPage.tsx:169`) → `client.revokeAll()`(`SessionsPage.tsx:172`) → `logoutClient.logoutAll()`(`SessionsPage.tsx:173`, best-effort) → `navigate("/login",{replace:true})`(`SessionsPage.tsx:173`). | `logoutAll`은 best-effort이므로 실패 분기 없음(`SessionsPage.tsx:173`). 무조건 `/login`으로 리다이렉트. | `/login`(`SessionsPage.tsx:173`) — `replace:true`로 뒤로가기 불가. |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-SessionsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-SessionsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:10`) | 직접 i18n 키 없음 — `shared-types/forbidden.ts:31` `classifyForbidden()`는 `ForbiddenKind \| null`을 반환(문구 아님). 호출 지점 `SessionsPage.tsx:64`에서 `true`일 때 `ForbiddenNotice`로 위임. | `ErrorBanner`(`SessionsPage.tsx:64`)에서 `classifyForbidden(error)`가 null이 아니면 즉시 `ForbiddenNotice` 컴포넌트를 반환하고 그 아래 `routeApiError` 경로를 타지 않는다. 403이 아닌 경우에만 아래 `routeApiError`로 넘어감. | **노출 안 함** — `classifyForbidden()`(`shared-types/src/forbidden.ts:31~41`)는 `ForbiddenKind \| null` 반환, traceId 필드 없음. 호출 지점(`SessionsPage.tsx:64`)에서 반환값으로 `if` 조건만 검사, traceId 추출 안 함. |
| `routeApiError` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:13`) | 직접 i18n 키 없음 — `errorRouting.ts:84~133` `routeApiError()`는 `RoutedApiError` 구조체 반환(`kind` 필드만). 실제 한국어 문구는 `ErrorMessage` 컴포넌트가 `getApiErrorMessage()`(`shared-types/src/apiError.ts:106`)로 변환. | `ErrorBanner`(`SessionsPage.tsx:65~66`): `routed.kind === "refetch_retry" \| "backoff_retry"`일 때 `canRetry=true`, `backoff_retry`일 때 `routed.afterSec`를 `retryAfterSec`으로 전달. `canRetry && onRetry`일 때 `ErrorMessage`가 재시도 버튼 표시. 그 외 kind(`server_fatal`, `not_found`, `unknown` 등)는 문구만 표시, 재시도 버튼 없음. | `routeApiError` 호출 지점(`SessionsPage.tsx:65`)은 반환값에서 `kind`와 `afterSec`만 추출. `server_fatal` kind에 `traceId` 필드가 있지만(`errorRouting.ts:117`), `ErrorBanner`가 이를 추출하지 않으므로 이 호출 지점에서는 유실됨. |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:64`) | `getApiErrorMessage(errorCode, message)`(`shared-types/src/apiError.ts:106`) — `EXACT_MESSAGES` 맵(`shared-types/src/apiError.ts:55~84`)으로 errorCode→한국어 1:1 매핑, 매핑 없으면 prefix 매핑, 그마저 없으면 `DEFAULT_API_ERROR_MESSAGE`. MFA step-up 버튼: `legacy.forbiddenNotice.t1` (`ForbiddenNotice.tsx:45`). | `ForbiddenNotice.tsx:31~49`: `classifyForbidden(error)`로 403 갈래 판별. `mfa_required`일 때만 `onStepUp` 버튼 표시(`ForbiddenNotice.tsx:42~46`). `policy`일 때 `DenialReasons` 컴포넌트 추가 표시(`ForbiddenNotice.tsx:41`). 나머지(`forbidden`, `tenant_mismatch`(`shared-types/src/forbidden.ts:14`))는 문구만 표시, 재시도 버튼 없음(재시도해도 같은 결과 반복). error가 403이 아니면 `null` 반환(기존 `ErrorMessage` 배너가 계속 담당). | **노출 안 함** — `ForbiddenNotice.tsx:38~48`에서 `Alert`에 텍스트만 전달, `traceId` 추출·전달 로직 없음. `extractErrorCode`/`extractMessage`만 사용(`ForbiddenNotice.tsx:19~28`). |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:68`) | `getApiErrorMessage(errorCode, message)`(`shared-types/apiError.ts`) — 동일 매핑 로직. trace_id 표시: `legacy.errorMessage.t1` (`ErrorMessage.tsx:60`) — `{traceId}` 치환. 재시도 카운트다운: `legacy.errorMessage.t2` (`ErrorMessage.tsx:64`) — `{remainingSec}` 치환. 재시도 버튼: `legacy.errorMessage.t3` (`ErrorMessage.tsx:67`). | `ErrorMessage.tsx:27~72`: `getApiErrorMessage`로 매핑된 한국어 문구 표시(`ErrorMessage.tsx:59`). `traceId`가 있으면 `legacy.errorMessage.t1`로 하단에 표시(`ErrorMessage.tsx:60`). `canRetry && onRetry`일 때 재시도 섹션(`ErrorMessage.tsx:61~69`): `backoff_retry`는 카운트다운 후 버튼 활성화(`disabled={remainingSec > 0}`), `refetch_retry`는 즉시 활성화. `fieldErrors`가 있으면 배너 전체 숨김(`ErrorMessage.tsx:55`). | **노출됨** (`SessionsPage.tsx:71`: `error instanceof ApiError ? error.traceId : undefined` — `traceId`가 undefined가 아니면 `ErrorMessage.tsx:60`에서 `legacy.errorMessage.t1`로 화면에 표시). `ApiError`가 아닌 객체(fallback)일 때는 `undefined` 전달 → 노출 안 함. |
| `Alert` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:206`) | `legacy.sessionsPage.t9` — "세션 정보를 해석할 수 없습니다." / "Unable to parse session information." (`SessionsPage.facts.md:21`) | `SessionsPage.tsx:204~208`: `parseSessionView`가 `null`을 반환한 항목을 `<li>`로 렌더링할 때 `tone="danger"` `Alert` 표시. 다른 행은 정상 렌더링 계속. 사용자 행동 없음(단순 안내). `parseSessionView`가 null을 반환하는 원인: 서버 응답 배열에 예전 세션 형식이나 누락 필드가 섞인 경우. | **노출 안 함** — `Alert` 컴포넌트는 `tone`과 children만 받음(`ui-web` 기준). `traceId` 전달 인자 없음. |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-SessionsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-SessionsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-SessionsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
