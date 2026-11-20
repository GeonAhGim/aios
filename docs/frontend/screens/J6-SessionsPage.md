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
| 입력 오류(400/검증) | `query.isError`(`:187`) 또는 `actionError`(`:188`)이고 `routeApiError`가 `{kind:"field_errors"}` 반환. `statusCode===400`且 errorCode가 `VALIDATION_*` (`errorRouting.ts:22-23`). | `ErrorBanner`(`SessionsPage.tsx:63-76`) → `classifyForbidden` null → `routeApiError` → `ErrorMessage`(`SessionsPage.tsx:68`)가 `getApiErrorMessage`로 문구 표시. 필드 오류일 경우 `fieldErrors`로 개별 필드 안내(`ErrorMessage.tsx:55`). | 「다시 시도」(`onRetry` → `query.refetch()`, :187). 필드 오류는 필드별 수정 후 재전송(현재 세션 목록 조회는 재조회만 지원). | 구현됨 `SessionsPage.tsx:187` |
| 인증 필요/세션 만료(401) | `statusCode===401`且 `isSessionExpiredErrorCode(errorCode)` true (`errorRouting.ts:128-129`). 세션 토큰 만료 또는 유효하지 않은 토큰. | `ErrorBanner` → `classifyForbidden` null → `routeApiError` → `{kind:"auth_required"}` → `ErrorMessage`(`SessionsPage.tsx:68`)가 `getApiErrorMessage`로 문구 표시. 재시도 버튼 없음(`classifyRetry` "none"). | 없음(로그인 필요). 사용자가 수동 재로그인 후 재접속. | 구현됨 `SessionsPage.tsx:187` |
| 권한 없음/MFA 필요(403) | `statusCode===403` (`forbidden.ts:32`). errorCode가 `AUTHZ_FORBIDDEN`/`AUTH_MFA_REQUIRED`/`AUTH_TENANT_MISMATCH`/`POLICY_*`/`RISK_*` 중 하나. | `ErrorBanner` → `classifyForbidden`(`SessionsPage.tsx:10`) true → `ForbiddenNotice`(`SessionsPage.tsx:64`) 표시. `getApiErrorMessage`로 문구, policy 갈래면 `DenialReasons` 상세(`ForbiddenNotice.tsx:41`). mfa_required면 `legacy.forbiddenNotice.t1` 버튼(`ForbiddenNotice.tsx:45`). | mfa_required일 때 `onStepUp` 버튼(현재 미배정). 정책 거부 시 사유 확인 후 정책 변경 요청. | 구현됨 `SessionsPage.tsx:64` |
| 충돌/상태 불일치(409) | `statusCode===409`且 errorCode가 `DATA_COVERAGE_MISSING`/`INTEGRITY_WALLET_BALANCE_DRIFT`/`STATE_CONCURRENCY_CONFLICT` 등 (`errorRouting.ts:104-113`). | `ErrorBanner` → `classifyForbidden` null → `routeApiError` → 409 갈래(`data_coverage_missing`/`wallet_balance_drift`/`invalid_transition`/`refetch_retry`) → `ErrorMessage`(`SessionsPage.tsx:68`)가 문구 표시. `refetch_retry`일 때만 재시도 버튼. | `refetch_retry`(STATE_CONCURRENCY_CONFLICT)일 때 「다시 시도」. 그 외 409는 수집 후 재요청 또는 안내만. | 구현됨 `SessionsPage.tsx:187` |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-SessionsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-SessionsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:105`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:182`) | TODO | TODO | TODO | TODO |
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
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:10`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:13`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:64`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:68`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:206`) | TODO | TODO | TODO |
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
