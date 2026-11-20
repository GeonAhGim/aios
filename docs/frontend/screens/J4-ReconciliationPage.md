# J4-ReconciliationPage — `/admin/reconciliation`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ReconciliationPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/admin/reconciliation` / `frontend/apps/web/src/routes/admin/ReconciliationPage.tsx`
- 여정 소속: J4


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J4-ReconciliationPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P8(운영자) — 가입 승인·검증 대기열·분쟁·입출금 승인·안전장치·정합성 점검 (`PERSONAS_AND_JOURNEYS.md` §2) |
| 목적(한 문장) | 대사(Reconciliation) 불일치 상태를 조회하고, 해소 사유를 입력하여 불일치를 해결한다. |
| 진입 조건 | `/admin/reconciliation` 경로 접근; `router.tsx:175`의 `protectAdmin(<ReconciliationPage />)` 가드 — `ProtectedRoute`(로그인) + `AdminRoute`(관리자 권한) 동시 통과 |
| 사용자 준비물 | 관리자 권한을 가진 활성 세션 |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J4-ReconciliationPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useReconciliationStates` | `frontend/packages/shared-hooks/src/useReconciliation.ts:10` | `useQuery` 1회 조회 (`queryKey: ["reconciliationStates"]`, `queryFn: apiClient.listReconciliationStates()`). `refetchInterval` 미설정 → 폴링 없음(`useReconciliation.ts:11-14`, 참조 `frontend/packages/shared-hooks/src/usePortfolio.ts:42`). 마운트 시 1회 GET `/reconciliation/list`(route: `reconciliation.list`, `frontend/packages/api-client/src/clients/reconciliation.ts:17`). window focus 시 자동 refetch (react-query 기본값). | `useQuery`의 `isError`/`error` 상태 발생 → 페이지 렌더링(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:114-115`)에서 `<ReconciliationActionError error={error} onRetry={() => refetch()} />` 표시. 에러 분류: `classifyBadRequest`(400)(`frontend/packages/shared-types/src/badRequest.ts:34`) → `BadRequestNotice`(`frontend/apps/web/src/components/BadRequestNotice.tsx:40`), `classifyForbidden`(403)(`frontend/packages/shared-types/src/forbidden.ts:31`) → `ForbiddenNotice`(`frontend/apps/web/src/components/ForbiddenNotice.tsx:31`), 그 외 → `ErrorMessage`(trace_id, retryAfterSec 포함, `frontend/apps/web/src/components/ErrorMessage.tsx:27`). retry 버튼 클릭 시 `refetch()` 재시도. |
| `useResolveReconciliation` | `frontend/packages/shared-hooks/src/useReconciliation.ts:17` | `useMutation` — 사용자가 `<Button>`(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:91`) 클릭 시 수동 호출. `mutationFn`: POST `/reconciliation/resolve/:targetRef`(route: `reconciliation.resolve`, `frontend/packages/api-client/src/clients/reconciliation.ts:24`). `onSuccess`: `qc.invalidateQueries({ queryKey: RECONCILIATION_STATES_QUERY_KEY })` → `useReconciliationStates` 자동 refetch(`useReconciliation.ts:18-22`). | 페이지 59-62행(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:59-62`)에서 `onError: (err) => setActionError({ targetRef, error: err })` → `actionError` 상태(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:46`) 설정 → 101-105행(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:101-105`)에서 `<ReconciliationActionError error={actionError.error} />` 표시. 에러 분류는 `ReconciliationActionError` 컴포넌트(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:25-38`)에서 동일 3-way 패턴(classifyBadRequest → BadRequestNotice, classifyForbidden → ForbiddenNotice, 그 외 → ErrorMessage). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J4-ReconciliationPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `useReconciliationStates`의 `isLoading === true` — `useQuery`(`shared-hooks/src/useReconciliation.ts:11`) 마운트 후 첫 `queryFn`(`apiClient.listReconciliationStates()`, `shared-hooks/src/useReconciliation.ts:13`)가 아직 완료되지 않음 | `<LoadingState />` 컴포넌트 표시 (`ReconciliationPage.tsx:117`) | 대기 | `구현됨` — `ReconciliationPage.tsx:116-117`: `{isLoading ? <LoadingState /> : ...}` |
| 빈 상태 | `data`가 truthy하지만 `data.states.length === 0` — API는 200 OK를 반환했으나 불일치 항목 없음 | `<EmptyState>{t("legacy.reconciliationPage.t5")}</EmptyState>` → "대사 상태가 없습니다." (`ReconciliationPage.tsx:121`, i18n 키 `legacy.reconciliationPage.t5`) | 없음 (데이터가 없는 것이 정상일 수 있음) | `구현됨` — `ReconciliationPage.tsx:120-121`: `: <EmptyState>{t("legacy.reconciliationPage.t5")}</EmptyState>` |
| 정상 | `data && data.states.length > 0` — 불일치 항목 1개 이상 존재 | 각 항목별 카드(`<li>`) 렌더링 (`renderState`, `ReconciliationPage.tsx:65-108`). 카드 내: `targetType · targetRef` + `<StatusBadge>` (line 70-73), `blockingReason` (line 75), "최근 확인: ..." + "최근 정상: ..." + "revision ..." (line 76-81), resolvable 항목은 `<Textarea>` placeholder "해소 사유" (line 85) + `<Button>` "불일치 해소" (line 98) | resolvable 항목: 해소 사유 입력 후 "불일치 해소" 버튼 클릭 → `POST /reconciliation/resolve/:targetRef` 호출 (`handleResolve`, `ReconciliationPage.tsx:52-63`) | `구현됨` — `ReconciliationPage.tsx:118-119`: `{data.states.map(renderState)}`, `renderState` 함수 (line 65-108) |
| 낡은 데이터(신선도 초과) | react-query useQuery에서 staleTime 미설정 → 기본값 0 (즉시 stale 표시). 하지만 UI에서 stale 상태를 감지하는 로직 없음 (shared-hooks/src/useReconciliation.ts:11-14에 staleTime/gcTime 설정 없음) | 없음 | 없음 | 없음 — shared-hooks/src/useReconciliation.ts:11-14: staleTime 미설정. frontend/apps/web/src/routes/admin/ReconciliationPage.tsx: stale 체크 없음. window focus 시 refetch는 발생하지만(react-query 기본값), 사용자에게 낡은 데이터임을 알리는 UI 없음 |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J4-ReconciliationPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 해소 사유 입력 없이 `<Button>` 클릭 (`ReconciliationPage.tsx:53-54`): `reason.trim()`이 빈 문자열 → `setActionError({ targetRef, error: new Error("해소 사유를 입력하세요.") })` (`ReconciliationPage.tsx:55`) | `ReconciliationActionError` 컴포넌트 표시 (`ReconciliationPage.tsx:101-103`). 3-way 분류: `classifyBadRequest(plain Error)` → false (statusCode 없음), `classifyForbidden(plain Error)` → false, `routeApiError(plain Error)` → `{kind: "unknown"}`. 최종 `<ErrorMessage message="해소 사유를 입력하세요." traceId={undefined} retryAfterSec={undefined} />` (`ReconciliationActionError:28-36`) | 사유 입력 후 재시도 | `구현됨` — 조건: `ReconciliationPage.tsx:54-56`, 표시: `ReconciliationPage.tsx:101-103`, ErrorMessage: `frontend/apps/web/src/components/ErrorMessage.tsx:27-72` |
| 인증 필요/세션 만료(401) | API 응답 statusCode=401. `classifyBadRequest` → false (400 전용), `classifyForbidden` → false (403 전용). `routeApiError`: `isSessionExpiredErrorCode(errorCode)` → `{kind: "auth_required"}` (`errorRouting.ts:128-130`). `classifyRetry` → "none" → retryAfterSec undefined | `<ErrorMessage message={err.message} traceId={undefined} retryAfterSec={undefined} />` (`ReconciliationActionError:28-36`). retry 버튼 없음 (errorMessage.t2, errorMessage.t3 키 사용 안함) | 로그인 재인증 후 재접속 | `구현됨` — 3-way 패턴의 폴스루 경로: `ReconciliationPage.tsx:28-36`, 라우팅: `errorRouting.ts:128-130` |
| 권한 없음/MFA 필요(403) | API 응답 statusCode=403. `classifyForbidden(error)` → `ForbiddenKind` (`mfa_required`·`tenant_mismatch`·`policy`·`forbidden`) (`forbidden.ts:31-41`) | `<ForbiddenNotice error={error} />` (`ReconciliationActionError:27`). `getApiErrorMessage(errorCode, message)`로 문구 표시 (`ForbiddenNotice.tsx:36`). policy 갈래 → `<DenialReasons>` 추가 (`ForbiddenNotice.tsx:41`). mfa_required → onStepUp 버튼 (`ForbiddenNotice.tsx:42-46`) | 갈래에 따른 별도 행동 (MFA 재인증, 정책 사유 확인) | `구현됨` — `ReconciliationActionError:27`, `ForbiddenNotice.tsx:31-49` |
| 충돌/상태 불일치(409) | API 응답 statusCode=409. `classifyBadRequest` → false, `classifyForbidden` → false. `routeApiError`: `DATA_COVERAGE_MISSING`/`WALLET_BALANCE_DRIFT`/`classifyStateConflict` → `refetch_retry`/`invalid_transition` (`errorRouting.ts:104-113`). `classifyRetry` → "none" (409는 retryable 아님) | `<ErrorMessage message={err.message} traceId={undefined} retryAfterSec={routed.afterSec} />` (`ReconciliationActionError:28-36`). `data_coverage_missing`/`wallet_balance_drift`/`invalid_transition` 모두 `afterSec` undefined → 재시도 버튼 없음 | 사유 확인 후 재시도 (수동) | `구현됨` — 폴스루: `ReconciliationPage.tsx:28-36`, 409 분류: `errorRouting.ts:104-113`. 주석 참조: `ReconciliationPage.tsx:21-24` ("409 재구현 안 함, ErrorMessage 표시") |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J4-ReconciliationPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 서버가 423 Locked(AUTH_ACCOUNT_LOCKED) 또는 429 Too Many Requests(RATE_LIMIT_EXCEEDED) 반환. 423: `deriveLockout(err)`가 `{locked: true, retryAfterSec}` 반환(`accountLockout.ts:41-49`). `routeApiError`: `deriveLockout` 갈래 → `{kind: "account_locked", retryAfterSec}`(`errorRouting.ts:125-126`). 429: `classifyRetry(err)`가 `BACKOFF_ERROR_CODES`("RATE_LIMIT_EXCEEDED") 매칭 → `kind: "backoff"`, `afterSec` 추출(`retryable.ts:45-50`). `routeApiError`: `classifyRetry` 갈래 → `{kind: "backoff_retry", afterSec}`(`errorRouting.ts:119-121`). 둘 다 `classifyBadRequest`/`classifyForbidden` false → `ErrorMessage` 폴스루. | `ErrorMessage` 컴포넌트: `getApiErrorMessage(errorCode)`로 매핑 문구 + `retryAfterSec` 초 카운트다운 + 재시도 버튼(`legacy.errorMessage.t3`). 423/429 모두 `classifyRetry`가 "backoff" → `remainingSec` 카운트다운(`ErrorMessage.tsx:49-53`). 버튼은 `remainingSec > 0` 시 비활성화(`ErrorMessage.tsx:66`). 423의 `retryAfterSec`는 `clampRetryAfterSec`로 최소 60초(`accountLockout.ts:31-36`). | 재시도 버튼 클릭 시 `onRetry` 콜백 호출. 423/429 모두 `remainingSec > 0` 시 비활성화, 0이 되면 활성화. | 구현됨 (`ReconciliationPage.tsx:28-36`: 3-way 분류 후 `ErrorMessage` 폴스루. `ErrorMessage.tsx:38-39`: `classifyRetry`로 "backoff" 판정. `ErrorMessage.tsx:41-53`: `remainingSec` 카운트다운. `ErrorMessage.tsx:66`: `disabled={remainingSec > 0}`. `accountLockout.ts:41-49`: 423 잠금 판정. `errorRouting.ts:125-126`: 423 → `account_locked`. `errorRouting.ts:119-121`: 429 → `backoff_retry`) |
| 서버 오류(5xx) | 서버가 500/502/503 등 반환. `classifyServerError(err)`(`serverError.ts:39-52`): EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY(503) → `{kind: "retryable", afterSec}`, EXCHANGE_FATAL(502)/INTERNAL_ERROR(500) → `{kind: "fatal", traceId}`. `routeApiError`(`errorRouting.ts:115-117`): retryable → `{kind: "backoff_retry", afterSec}`, fatal → `{kind: "server_fatal", traceId}`. 둘 다 `ErrorMessage` 폴스루. | `ErrorMessage`: 오류 문구 + `traceId` 노출(`legacy.errorMessage.t1`, `ErrorMessage.tsx:60`). retryable(503)이면 재시도 버튼 즉시 활성화. fatal(500/502)이면 `traceId`만 노출, 재시도 버튼 없음(`classifyRetry` → "none", `canRetry` false, `ErrorMessage.tsx:61`). | retryable(503)이면 재시도 버튼으로 수동 재시도. fatal(500/502)이면 재시도 버튼 없음(서버 측 복구 필요). | 구현됨 (`ReconciliationPage.tsx:28-36`: 3-way 분류 후 `ErrorMessage` 폴스루. `serverError.ts:39-52`: 5xx 분류기. `errorRouting.ts:115-117`: retryable→backoff_retry, fatal→server_fatal. `ErrorMessage.tsx:38-39`: `classifyRetry` → `canRetry`. `ErrorMessage.tsx:60`: `traceId` 노출. `ErrorMessage.tsx:61-69`: `canRetry && onRetry` 조건부 버튼) |
| 네트워크 단절 | 클라이언트-서버 간 연결 실패(HTTP 요청 실패). React Query가 `isError: true`로 전환. `useReconciliationStates`의 `error`에 `Error` 인스턴스 저장. | 페이지 114-115행(`ReconciliationPage.tsx:114-115`): `isError` 분기 → `<ReconciliationActionError error={error} onRetry={() => refetch()} />`. `ReconciliationActionError:26-28`: `classifyBadRequest`/`classifyForbidden`이 false (network Error는 HTTP 상태 코드 없음) → `routeApiError` → `isErrorCodeLike` false → `{kind: "unknown"}`. 최종 `ErrorMessage` (`ReconciliationPage.tsx:30-36`): `errorCode`/`traceId` undefined, `message`는 `error.message`. 재시도 버튼 노출(`onRetry={() => refetch()}` 전달됨). | 재시도 버튼 클릭 시 `refetch()`로 `GET /reconciliation/list` 재조회. | 구현됨 (`ReconciliationPage.tsx:42`: `isError`/`error`/`refetch` 구조 분해. `ReconciliationPage.tsx:114-115`: `isError` 분기, `onRetry={() => refetch()}` 전달. `ReconciliationPage.tsx:26-28`: network Error는 `classifyBadRequest`/`classifyForbidden` false. `errorRouting.ts:87-88`: `isErrorCodeLike` false → unknown. `ErrorMessage.tsx:38`: `classifyRetry(undefined)` → "none" → `canRetry` false. 재시도 버튼 없음 — 네트워크 오류 시 `onRetry`가 전달되지만 `canRetry`가 false이므로 버튼이 표시되지 않음) |
| 부분 실패(일부 데이터만 실패) | `useReconciliationStates`는 단일 쿼리(GET /reconciliation/list)로 전체 상태 목록을 한 번에 반환 — 부분 데이터 반환 없음. `useResolveReconciliation` mutation은 각 `targetRef`별로 독립 호출. `actionError` 상태는 `{targetRef, error}` 구조로 targetRef별 개별 에러 관리(`ReconciliationPage.tsx:46`). | 각 항목별 카드 하단에 개별 에러 배너 노출(`ReconciliationPage.tsx:101-105`): `{actionError?.targetRef === state.targetRef && <ReconciliationActionError error={actionError.error} />}`. 실패한 항목만 에러 표시, 다른 항목은 정상 렌더링. | 각 에러 영역에 `onRetry` 미전달 — `ReconciliationPage.tsx:103`: `<ReconciliationActionError error={actionError.error} />` (onRetry 없음). 사유 재입력 후 "불일치 해소" 버튼 재클릭으로 재시도. | 구현됨 (`ReconciliationPage.tsx:46`: `actionError` 상태 정의. `ReconciliationPage.tsx:55`: 입력 오류 시 `setActionError({targetRef, error})`. `ReconciliationPage.tsx:61`: mutation onError 시 `setActionError({targetRef, error})`. `ReconciliationPage.tsx:101-105`: targetRef별 조건부 에러 노출. `ReconciliationPage.tsx:103`: onRetry 미전달) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J4-ReconciliationPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:91`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J4-ReconciliationPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J4-ReconciliationPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:4`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:5`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:6`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:26`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:27`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:30`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J4-ReconciliationPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J4-ReconciliationPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J4-ReconciliationPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
