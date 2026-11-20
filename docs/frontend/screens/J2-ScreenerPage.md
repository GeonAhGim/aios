# J2-ScreenerPage — `/screener`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ScreenerPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/screener` / `frontend/apps/web/src/routes/screener/ScreenerPage.tsx`
- 여정 소속: J2


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J2-ScreenerPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — J2 여정(종목 발견)의 주체. PERSONAS_AND_JOURNEYS.md §3: J2는 P1(+P2) 중심 [확인] |
| 목적(한 문장) | 필터 빌더로 조건을 구성해 종목을筛选하고, 결과를 차트/백테스트로 연결하여 종목 발견을 시작한다. |
| 진입 조건 | `/screener` 라우트(`router.tsx:121`) — `protect()`(`router.tsx:70-72`)가 `ProtectedRoute`로 감싸 인증 필요. feature flag 없음 [확인] |
| 사용자 준비물 | 없음. 인증된 사용자면 즉시 접근 가능 [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J2-ScreenerPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useQuery(["screener-run", submitted])` → `client.runScreen` | `POST /v1/foundation/screener/run` (`frontend/packages/api-client/src/apiRoutes.ts:431`, implemented=true), 호출은 `frontend/packages/api-client/src/clients/screener.ts:27-32`, 훅 사용 `frontend/apps/web/src/routes/screener/ScreenerPage.tsx:143-147` | 변경 요청 성격의 조회: `submitted`가 null이면 비활성(`enabled: submitted !== null`, `frontend/apps/web/src/routes/screener/ScreenerPage.tsx:146`), 「실행」이 검증을 통과해 `setSubmitted`할 때만 1회 조회(`handleRun`, `{P}:170-176`). refetchInterval 옵션 없음 → 폴링 없음. 수동 재시도는 오류 배너의 `query.refetch()`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:315`). retry·staleTime 등은 이 훅에서 지정하지 않고, 앱 루트 `new QueryClient()`도 옵션 없이 생성(`frontend/apps/web/src/main.tsx:15`) — 라이브러리 기본값의 실제 값은 미확인 | `query.isError`이면 `ScreenerErrorBanner`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:314-316`)가 `ErrorMessage`로 errorCode·message·traceId 표시(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:109-115`). `routeApiError` 결과가 `refetch_retry`/`backoff_retry`일 때만 재시도 버튼, `backoff_retry`면 `retryAfterSec` 전달(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:107,113`). 로딩은 `LoadingState`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:313`). 401/403/409/429 각각의 세부 분기는 `routeApiError` 정의(shared-types)를 읽지 않아 미확인 |
| `ScreenerRouteNotImplementedError` 단락 | `frontend/packages/api-client/src/clients/screener.ts:17-32` | `isRouteImplemented("screener.run")`이 false일 때만 fetch 전에 던짐. 현재 `apiRoutes.ts:431`이 true라 현재 설정에서는 도달하지 않는 방어 경로 | 던져지면 같은 `ScreenerErrorBanner`로 표시하되 재시도 버튼은 끔(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:105-107`) |
| `defaultScreenerClient()` (`useMemo`) | `createScreenerClient(baseUrl, getToken)` — baseUrl은 `VITE_API_BASE_URL ?? "http://localhost:8000"`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:52`), 토큰은 `useAuthStore.getState().token`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:54-57`) | 마운트·`screenerClient` prop 변경 시에만 재생성(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:134`). 서버 조회 아님 | 해당 없음(생성 시 오류 경로 없음). 토큰 누락·만료 시 처리는 기반 HTTP 클라이언트(http.ts)를 읽지 않아 미확인 |
| `useState`(universe, filters, sort, validationIssues, submitted) | 로컬 상태 `frontend/apps/web/src/routes/screener/ScreenerPage.tsx:136-141` | 사용자 입력 시 갱신, 서버 호출 없음 | 검증 실패는 `describeScreenDefinitionIssues`가 `validationIssues`로 담아 `screener-validation-alert`로 표시하고 조회하지 않음(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:174-176`) |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J2-ScreenerPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `submitted !== null && query.isLoading` (`ScreenerPage.tsx:313`) | `<LoadingState />` (`ScreenerPage.tsx:313`) | 대기 | 구현됨 (`ScreenerPage.tsx:313`) |
| 빈 상태 (미실행) | `submitted === null` (`ScreenerPage.tsx:312`) | `<EmptyState>{t("screener.results.beforeRun")}</EmptyState>` → "필터를 구성하고 실행하세요." (`ScreenerPage.tsx:312`, `facts:36`) | 필터 구성 후 `handleRun` (`ScreenerPage.tsx:171-177`) 클릭 | 구현됨 (`ScreenerPage.tsx:312`) |
| 빈 상태 (실행 후 결과 없음) | `submitted !== null && !query.isError && !query.isLoading && rows.length === 0` (`ScreenerPage.tsx:317-318`) | `<EmptyState>{t("screener.results.empty")}</EmptyState>` → "조건에 맞는 종목이 없습니다." (`ScreenerPage.tsx:317-318`, `facts:37`) | 필터 조건 수정 후 `handleRun` 재실행 | 구현됨 (`ScreenerPage.tsx:317-318`) |
| 정상 | `rows.length > 0` (`ScreenerPage.tsx:320`) | 총 개수 `screener.results.totalLabel` (`ScreenerPage.tsx:322-324`), 1,000행 상한 초과 시 `screener.results.truncated` 경고 (`ScreenerPage.tsx:325`), 각 행에 심볼+Badge+`screener.results.viewInChart` 링크 (`ScreenerPage.tsx:327-341`) | 각 행의 차트 링크로 이동(`/chart?instrument_id=...`), 필터 수정 후 재실행 | 구현됨 (`ScreenerPage.tsx:320-344`) |
| 낡은 데이터(신선도 초과) | — | — | — | 없음 — 폴링/자동 갱신 옵션 지정 없음(`ScreenerPage.tsx:143-147`). stale time 기본값은 앱 `new QueryClient()` 옵션 없음으로 미확인(`main.tsx:15`) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J2-ScreenerPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `handleRun` (`ScreenerPage.tsx:171-177`)에서 `describeScreenDefinitionIssues` (`:173`)가 issues 반환, `issues.length > 0` (`:175`) → 서버 조회 안 함. 서버 400 전용 분기 없음 → `ScreenerErrorBanner` (`:104-117`) → `ErrorMessage` (`:109-115`)로 흐름 | 클라이언트 검증 실패: `screener-validation-alert` (`:292`) 안에 `Alert tone="danger"` (`:293`)로 문구 표시. i18n 키 4개: `screener.validation.universeRequired` (`:122`), `filtersRequired` (`:124`), `filterIncomplete` (`:126`), `filterResearchAsOfRequired` (`:128`). 서버 400: 전용 문구 키 없음 (공용 ErrorMessage 표시) | 필드를 고치고 「실행」(`screener-run`, `:303`)을 다시 누름. 성공 시 `validationIssues`가 빈 배열로 갱신되어 경고 사라짐 (`:174`) | 클라이언트 검증: `구현됨` (`:171-177`, `:291-301`). 서버 400 전용 처리: `없음` |
| 인증 필요/세션 만료(401) | 화면 전용 401 분기 없음. 토큰은 `useAuthStore.getState().token` (`:55-57`)로 읽으나 갱신/재로그인 로직 없음. 오류 분류(`shared-types/src/errorRouting.ts:88-133`)가 `auth_required` 반환하지만 화면에서 분기하지 않음 | HTTP 클라이언트 레벨에서 `handleAuthFailure` (`api-client/http.ts:104`)가 401 refresh 재시도. 화면 전용 오류 문구: 없음. 공용 오류 배너로 표시 여부: 오류 분류 반환값을 화면에서 분기하지 않으므로 미확인 | 재로그인 안내 또는 토큰 갱신: 화면 고유 처리 없음 | 없음 (화면 고유 처리 없음) |
| 권한 없음/MFA 필요(403) | 화면 전용 403 분기 없음. MFA 관련 코드 이 파일에 없음 (grep 확인) | HTTP 클라이언트 레벨에서 `classifyForbidden(err) === "mfa_required"` (`api-client/http.ts:93`) 처리. 화면 전용 오류 문구: 없음. 공용 오류 배너로 표시 여부: 미확인 | MFA 재입력 또는 권한 요청: 화면 고유 처리 없음 | 없음 (화면 고유 처리 없음) |
| 충돌/상태 불일치(409) | 화면 전용 409 분기 없음. `routeApiError`가 `refetch_retry`/`invalid_transition`/`data_coverage_missing`/`wallet_balance_drift` 등으로 분류하지만 화면에서 분기하지 않음 | 공용 `ScreenerErrorBanner` (`:104-117`) → `ErrorMessage` (`:109-115`)로만 흐름. 전용 문구: 없음 | 재시도 버튼: `ScreenerErrorBanner`가 `routeApiError.kind`로 `canRetry` 결정 (`:107`). 그 외 복구 안내: 없음 | 없음 (화면 고유 처리 없음) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J2-ScreenerPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | `routeApiError(error)` (`shared-types/src/errorRouting.ts:88-133`)가 `backoff_retry` (429 RATE_LIMIT_EXCEEDED → `classifyRetry` → `retry.kind === "backoff"`, `:121`) 또는 `refetch_retry` (429 RATE_LIMIT_EXCEEDED 중 retry_after_seconds 없음 → `retry.kind === "refetch"`, `:120`) 반환. 423 ACCOUNT_LOCKED → `deriveLockout` → `account_locked` (`:126`) | `ScreenerErrorBanner` (`ScreenerPage.tsx:104-117`) → `ErrorMessage` (`:109-115`). 429(backoff): `ErrorMessage`가 `classifyRetry`로 `backoff` 판별 시 `retryAfterSec` 카운트다운 (`ErrorMessage.tsx:41-53`), 카운트다운 종료 후 재시도 버튼 활성화 (`:66`). 429(refetch)/423: 재시도 버튼 표시 여부 — `ScreenerErrorBanner`가 `canRetry`를 `routeApiError.kind === "refetch_retry" || backoff_retry`로 결정 (`ScreenerPage.tsx:107`). 423(account_locked)은 `canRetry=false` → 재시도 버튼 없음. 423 전용 문구: 없음 | 429: 카운트다운 후 재시도 버튼 클릭 (`query.refetch()`, `:315`). 423: 재시도 불가 — 관리자 문의 필요 (전용 안내 없음) | 429(backoff): `구현됨` (ErrorMessage.tsx:41-53, ScreenerPage.tsx:107,113). 429(refetch): `구현됨` (canRetry=true, 버튼 활성화). 423: `없음` (account_locked은 canRetry=false, 재시도 버튼 숨김) |
| 서버 오류(5xx) | `classifyServerError(err)` (`shared-types/src/serverError.ts`)가 `retryable` (503 EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY, `serverError.ts:42-44`) → `backoff_retry` 또는 `fatal` (502 BAD_GATEWAY/500 INTERNAL_SERVER_ERROR, `serverError.ts:47-49`) → `server_fatal` (`errorRouting.ts:117`). `ScreenerPage.tsx:314-316`의 `query.isError` 조건에서 포착 | `ScreenerErrorBanner` → `ErrorMessage`. 503(backoff_retry): `retryAfterSec` 카운트다운 + 재시도 버튼. 502/500(server_fatal): `traceId` 표시 (`ErrorMessage.tsx:60`), 재시도 버튼 없음 (`canRetry=false`). i18n 키 없음 — 공용 ErrorMessage 사용 | 503: 재시도 버튼 클릭 (`query.refetch()`, `:315`). 502/500: 재시도 불가 — 서버 복구 대기 | 503: `구현됨` (classifyServerError retryable → backoff_retry, ScreenerPage.tsx:107, ErrorMessage.tsx:41-53). 502/500: `부분 구현` (traceId 표시는 됨, 재시도 불가 안내는 전용 문구 없음). server_fatal일 때 `ScreenerErrorBanner`가 `canRetry=false`로 재시도 버튼 숨김 (`:107`) — 동작은 맞으나 사용자에게 "서버 오류 재시도 불가" 안내 문구 없음 |
| 네트워크 단절 | `fetch()` (`http.ts:46`)가 실패 — 네트워크 없음, DNS 실패, CORS 차단 등. `ApiError`가 아님 (`statusCode` 없음). `routeApiError` (`errorRouting.ts:88`)가 `isErrorCodeLike(err)`에서 false → 모든 분류기를 우회 → `unknown` (`:132`) | `ScreenerErrorBanner` → `ErrorMessage`. `error instanceof Error`이므로 `error.message` 표시 (`ScreenerPage.tsx:111`). `routeApiError`가 `unknown` → `canRetry=false` (`ScreenerPage.tsx:107`). 재시도 버튼 없음. i18n 키 없음 — `TypeError: Failed to fetch` 등 원본 메시지 표시 | 네트워크 복귀 후 페이지 새로고침 또는 수동 재실행. 전용 재시도 버튼 없음 | `없음` — 네트워크 오류는 `unknown`으로 수렴, 재시도 버튼 표시 안 됨 (`ScreenerPage.tsx:107: canRetry=false`). `ErrorMessage`도 `classifyRetry`가 "none"이면 재시도 버튼 숨김 (`ErrorMessage.tsx:39: canRetry=false`) |
| 부분 실패(일부 데이터만 실패) | 스크리너가 여러 소스(지표/펀더멘털/리서치)에서 데이터를 가져오는데 일부만 실패하는 경우 | 현재 구현은 `query.data?.rows` 전체 또는 없음. 부분 실패를 구분하는 로직 없음 (`ScreenerPage.tsx:179: rows = query.data?.rows ?? []`) | — | `없음` — API 응답 수준에서 부분 실패 구분 없음. `api-client/src/clients/screener.ts:27`의 `runScreen` 반환형 `ScreenRunResponse` (`api-client/src/clients/screener.ts:8`)를 읽지 않아 서버-side 부분 실패 형식 미확인. 화면은 전체 성공/전체 실패만 처리 |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J2-ScreenerPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:257`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:271`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:303`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J2-ScreenerPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J2-ScreenerPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:9`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:18`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:109`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J2-ScreenerPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J2-ScreenerPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J2-ScreenerPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
