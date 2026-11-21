# J5-PerformanceStatementsPage — `/portfolio/performance-statements`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/PerformanceStatementsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/portfolio/performance-statements` / `frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx`
- 여정 소속: J5


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J5-PerformanceStatementsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 개인 트레이더(투자자) — 포트폴리오의 수익률·구성 요소별 순손익을 확인하고, 명세서에 오류가 있으면 정정 요청한다. [확인: PERSONAS_AND_JOURNEYS.md §2 P1, routes/portfolio/PerformanceStatementsPage.tsx:323 "실적 명세서"] |
| 목적(한 문장) | 포트폴리오의 실적 명세서를 계산·조회·정정하여 투자 성과의 투명성과 정확성을 보장한다. [확인: routes/portfolio/PerformanceStatementsPage.tsx:61-274 MoneyCell/StatementDetail/ComputeForm] |
| 진입 조건 | `router.tsx:134` — `/portfolio/performance-statements` 경로, `protect(<PerformanceStatementsPage />)`로 인증 필요. [확인: router.tsx:134] |
| 사용자 준비물 | 성과 데이터를 가진 포트폴리오, 명세서 계산 기간(periodStart~periodEnd). [확인: routes/portfolio/PerformanceStatementsPage.tsx:203-216 ComputeForm] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J5-PerformanceStatementsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `listQuery` (`useQuery`) — 명세서 목록 | `apiClient.listPerformanceStatements(params)` (`routes/portfolio/PerformanceStatementsPage.tsx:302`) | 1회 조회 — 마운트 시 1회, `scopeFilter`/`portfolioId` 변경 시 `listParams`가 바뀌어 `queryKey`가 달라지고 `useQuery`가 자동 재페칭. 폴링 옵션 미설정(폴링 없음). [확인: routes/portfolio/PerformanceStatementsPage.tsx:300-303] | `ErrorMessage` 컴포넌트 표시 (`routes/portfolio/PerformanceStatementsPage.tsx:359-366`). `routeApiError`로 오류 종류 판정 (`routes/portfolio/PerformanceStatementsPage.tsx:317`). `backoff_retry`면 `retryAfterSec` 표시, `refetch_retry`/`backoff_retry`면 `onRetry`로 수동 재조회 가능 (`routes/portfolio/PerformanceStatementsPage.tsx:318-319, 364-365). |
| `detailQuery` (`useQuery`) — 선택 명세서 상세 | `apiClient.getPerformanceStatement(statementId, params)` (`routes/portfolio/PerformanceStatementsPage.tsx:307`) | 1회 조회 — `selectedId`가 null이 아닐 때만 `enabled: true`로 실행 (`routes/portfolio/PerformanceStatementsPage.tsx:308`). `selectedId` 변경 시 재페칭. 폴링 옵션 미설정(폴링 없음). [확인: routes/portfolio/PerformanceStatementsPage.tsx:305-309] | 404(`isResourceNotFound`)면 `NotFoundState` 표시 (`routes/portfolio/PerformanceStatementsPage.tsx:396-400`). 그 외 오류면 `ErrorMessage` 표시 — `classifyServerError`가 `retryable`이면 `onRetry`로 `detailQuery.refetch()` 가능 (`routes/portfolio/PerformanceStatementsPage.tsx:402-409`). |
| `correct` (`useMutation`) — 정정 요청 | `apiClient.correctPerformanceStatement(statementId, body)` (`routes/portfolio/PerformanceStatementsPage.tsx:103-107`) | 사용자 트리거 — 정정 사유 입력 후 `<Button>` 클릭 (`routes/portfolio/PerformanceStatementsPage.tsx:184`). `mutationFn`이 `() => correctStatement(...)`으로 래핑됨. [확인: routes/portfolio/PerformanceStatementsPage.tsx:103-107] | `isError` 상태 시 `ErrorMessage` 표시 (`routes/portfolio/PerformanceStatementsPage.tsx:188-194`). 성공(`isSuccess`) 시 성공 `Alert` 표시 + `onCorrected()` 호출 → `refreshAfterMutation`로 쿼리 무효화 (`routes/portfolio/PerformanceStatementsPage.tsx:195, 415`). |
| `compute` (`useMutation`) — 명세서 계산 | `apiClient.computePerformanceStatement(body)` (`routes/portfolio/PerformanceStatementsPage.tsx:208-216`) | 사용자 트리거 — ComputeForm에서 기간/범위 입력 후 `<Button>` 클릭 (`routes/portfolio/PerformanceStatementsPage.tsx:261`). `onSuccess`에서 `onComputed(statement.id)` 호출 → `refreshAfterMutation`. [확인: routes/portfolio/PerformanceStatementsPage.tsx:208-217] | `isError` 상태 시 `ErrorMessage` 표시 (`routes/portfolio/PerformanceStatementsPage.tsx:265-271`). 성공 시 새로 계산된 명세서 ID로 `refreshAfterMutation` 호출하여 목록/상세 갱신 (`routes/portfolio/PerformanceStatementsPage.tsx:216`). |
| `refreshAfterMutation` (로컬 함수) — mutation 후 쿼리 갱신 | `queryClient.invalidateQueries` (`routes/portfolio/PerformanceStatementsPage.tsx:313-314`) | mutation 성공 시 자동 — `correct.mutate()` 성공 시 `onCorrected()` → `refreshAfterMutation(id)` (`routes/portfolio/PerformanceStatementsPage.tsx:415`), `compute`의 `onSuccess` 시 `onComputed(id)` → `refreshAfterMutation(id)` (`routes/portfolio/PerformanceStatementsPage.tsx:216`). `queryClient.invalidateQueries`로 목록(`["performanceStatements"]`)과 상세(`["performanceStatement", id]`) 쿼리 무효화. [확인: routes/portfolio/PerformanceStatementsPage.tsx:311-315] | invalidateQueries는 실패 시 자동으로 재페칭하지 않음(`staleTime: Infinity` 기본). 수동 재페칭 필요 시 queryClient.refetchQueries 호출 필요(현재 미구현). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J5-PerformanceStatementsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | 목록 쿼리 `listQuery.isLoading` (`routes/portfolio/PerformanceStatementsPage.tsx:367-368`) 또는 선택된 명세서 상세 쿼리 `detailQuery.isLoading` (`:393-394`) | `LoadingState` 컴포넌트 표시. 이 파일에는 문구 키가 없으므로 문구는 미확인(LoadingState 내부 문구는 이 파일 밖). [확인: routes/portfolio/PerformanceStatementsPage.tsx:23 LoadingState import, :368, :394] | 없음(대기) | 구현됨 — 목록 로딩: `listQuery.isLoading` 시 `<LoadingState />` (`routes/portfolio/PerformanceStatementsPage.tsx:367-368`). 상세 로딩: `detailQuery.isLoading` 시 `<LoadingState />` (`routes/portfolio/PerformanceStatementsPage.tsx:393-394`). |
| 빈 상태 | 목록 로드 성공 후 `statements.length === 0` (`routes/portfolio/PerformanceStatementsPage.tsx:369-370`) | `performanceStatementsPage.listEmpty` = "실적 명세서가 없습니다." [확인: facts 시트 :36, 코드 :370] | 빈 상태 자체에 CTA가 없고, 같은 화면의 명세서 계산 폼(`ComputeForm`, `routes/portfolio/PerformanceStatementsPage.tsx:327`)이 유일한 생성 경로 | 구현됨 — `statements.length === 0` 시 `performanceStatementsPage.listEmpty` 문구 표시 (`routes/portfolio/PerformanceStatementsPage.tsx:370`). |
| 정상 | 목록에 항목이 있고 (`routes/portfolio/PerformanceStatementsPage.tsx:371-388`, `data-testid="performance-statement-items"` `:372`) 상세 데이터가 로드됨 (`detailQuery.data` 분기 `:411`) | 상세 헤더: `performanceStatementsPage.asOf` = "기준 시각: {{asOf}}"와 `rev {revisionNo}` (`:119`). 구성 요소 표 헤더: `performanceStatementsPage.component` (`:129`), `performanceStatementsPage.amount` (`:130`). 정정 폼: `performanceStatementsPage.correctReasonLabel` (`:172`), `performanceStatementsPage.correctReasonPlaceholder` (`:173`), `performanceStatementsPage.correctSubmit` (`:186`), 성공 시 `performanceStatementsPage.correctSuccess` (`:195`). 수익률: `performanceStatementsPage.returns` (`:148`), `performanceStatementsPage.annualized` (`:152`). 정적 경고: `performanceStatementsPage.identityMismatch` (`:161`). | 목록 항목 선택 (`routes/portfolio/PerformanceStatementsPage.tsx:375-377` `<button>`). 정정 사유 입력 후 "정정 요청" (`routes/portfolio/PerformanceStatementsPage.tsx:186`). | 구현됨 — 목록 항목 렌더링: `data-testid="performance-statement-items"` (`routes/portfolio/PerformanceStatementsPage.tsx:372`). 상세 헤더: `asOf` + `revisionNo` (`:119`). 구성 요소 표: `:129-130`. 정정 폼: `:170-195`. |
| 낡은 데이터(신선도 초과) | 해당 없음 — 구현 없음. 신선도 판정 로직, 경과 시간 계산, stale/isStale/staleTime/refetchInterval 관련 코드를 검색했으나 없음을 확인. `asOf`는 원본 값을 그대로 표시 (`routes/portfolio/PerformanceStatementsPage.tsx:119`). 쿼리 옵션 (`routes/portfolio/PerformanceStatementsPage.tsx:300-309`)에도 해당 옵션 없음. | 기준 시각 원문만 표시 (`routes/portfolio/PerformanceStatementsPage.tsx:119`), 낡음 경고 없음 | 없음(수동 새로고침 버튼 없음; 재조회는 오류 시에만 `routes/portfolio/PerformanceStatementsPage.tsx:365`, `:407`) | 없음 — 신선도 판정 로직 없음. 해당 옵션 검색 결과 없음. `asOf` 원문만 표시 (`routes/portfolio/PerformanceStatementsPage.tsx:119`). 쿼리 옵션 (`routes/portfolio/PerformanceStatementsPage.tsx:300-309`)에 해당 옵션 없음. |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J5-PerformanceStatementsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 정정 mutation 실패 시 `correct.isError`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:189`) 가 참. `reason`이 공백이면 정정 버튼이 비활성(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:183`)이라 제출 불가. 계산 mutation 실패 시 `compute.isError`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:265`). 기간 시작·종료가 모두 없으면 계산 버튼 비활성(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:260`). | 정정 실패는 `ErrorMessage`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:190`)가 `errorCode`·`message`·`traceId`를 받아 표시. 계산 실패도 `ErrorMessage`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:266`) 동일. 문구는 `getApiErrorMessage`로 생성(`frontend/apps/web/src/components/ErrorMessage.tsx:36`). 필드 단위 검증 문구 키는 이 파일에 없음. | 재시도 버튼 없음: 정정·계산 `ErrorMessage`에 `onRetry`를 전달하지 않는다(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:190`, `frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:266`). 입력을 고쳐 다시 제출만 가능. | 오류 표시·빈 값 비활성화 구현됨. 필드 단위 검증 문구는 없음. `ErrorMessage` 내부 문구 키(`legacy.errorMessage.t1`~`t3`)는 `frontend/apps/web/src/components/ErrorMessage.tsx:60-67`에서 참조되며 문구 내용은 미확인. |
| 인증 필요/세션 만료(401) | 이 페이지에 401 전용 분기 없음. 목록 오류는 `routeApiError`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:317`)로 분류하고 `listCanRetry`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:318`)가 참일 때만 재시도. 상세 오류는 `isResourceNotFound`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:396`)로 404를 가른 뒤 그 외는 `classifyServerError`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:407`)로 판정. 401을 어느 kind로 분류하는지는 미확인(shared 패키지 범위 밖). | 목록 실패: `ErrorMessage`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:360`) 표시. 상세 실패(404 아님): `ErrorMessage`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:402`) 표시. 로그인 이동·세션 만료 안내는 이 파일에 없음. | `refetch_retry`/`backoff_retry`면 목록 재시도 버튼(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:365`). 상세는 `retryable`일 때만 재시도(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:407`). | 페이지 전용 401 처리 없음(`없음`). 라우터 보호 래퍼의 401 동작은 미확인. |
| 권한 없음/MFA 필요(403) | 이 페이지에 403·MFA 전용 분기 없음. 분류 경로는 401 행과 동일(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:317`, `frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:396`, `frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:407`). 403을 어느 kind로 분류하는지는 미확인. | 401 행과 동일: 일반 `ErrorMessage`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:360`, `frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:402`). MFA 안내 문구는 없음. | 401 행과 동일(재시도 조건 충족 시에만). | 전용 처리 `없음`. MFA 처리 여부는 미확인. |
| 충돌/상태 불일치(409) | 409 전용 분기 없음. 정정 실패는 `ErrorMessage`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:190`), 계산 실패는 `ErrorMessage`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:266`)만 표시. 정정 성공 후 갱신 `refreshAfterMutation`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:311`)도 409를 구분하지 않는다. | 400/검증 행과 동일한 일반 `ErrorMessage`. 충돌 전용 문구 키 없음. | 재시도 버튼 없음(`onRetry` 미전달, `frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:190`, `frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:266`). | 전용 처리 `없음`. 일반 오류 표시만 구현됨. |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J5-PerformanceStatementsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 목록 오류 시 `routeApiError`(frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:317)가 423(`AUTH_ACCOUNT_LOCKED`) → `{kind: "account_locked"}`, 429(`RATE_LIMIT_EXCEEDED`) → `{kind: "backoff_retry"}` 반환. `listCanRetry`(`:318`)가 `backoff_retry`/`refetch_retry`일 때 참. [확인: shared-types/src/errorRouting.ts:125-126(account_locked), :119-121(backoff_retry); shared-types/src/accountLockout.ts:41-50(deriveLockout); shared-types/src/retryable.ts:25-28(RATE_LIMIT_EXCEEDED)] | 목록 오류 시 `ErrorMessage`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:360-365`)가 `errorCode`·`message`·`traceId` 표시. `backoff_retry`면 `retryAfterSec`(`:364`)로 카운트다운. `ErrorMessage` 내부에서 `classifyRetry`가 "backoff"일 때 초 단위 카운트다운 + 활성화 대기 버튼 표시(`frontend/apps/web/src/components/ErrorMessage.tsx:49-53, 63-66`). [확인: components/ErrorMessage.tsx:38-39(classifyRetry), :49-53(카운트다운 타이머), :63-66(비활성 버튼)] | `listCanRetry`(`:318`)가 참일 때 재시도 버튼(`:365`) 표시. `backoff_retry`면 카운트다운 종료 후 활성화. 423 잠김은 `ErrorMessage`에 `onRetry` 전달 안 함(`:360-364`) — 카운트다운만 표시. [확인: routes/portfolio/PerformanceStatementsPage.tsx:365(listCanRetry ? onRetry : undefined)] | 구현됨 — 목록 오류 경로(`:317-318, 360-365`)가 `routeApiError` 분류 결과를 `ErrorMessage`로 전달. 429 속도 제한: `backoff_retry` → 카운트다운 재시도 버튼(`:364-365`). 423 잠김: `account_locked`.kind지만 페이지 전용 분기 없음(일반 `ErrorMessage` 표시). `ErrorMessage` 컴포넌트(`:360`)가 `errorCode`·`message`·`traceId`·`retryAfterSec`를 받아 표시. |
| 서버 오류(5xx) | `classifyServerError`(frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:407)가 5xx 코드를 분류: `EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY`(503) → `{kind: "retryable"}`, `EXCHANGE_FATAL`(502)/`INTERNAL_ERROR`(500) → `{kind: "fatal"}`. [확인: shared-types/src/serverError.ts:25-30(RETRYABLE/FATAL 코드 집합), :39-52(classifyServerError)] | 상세 오류 시 `ErrorMessage`(`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:402-409`)가 `errorCode`·`message`·`traceId` 표시. `retryable`이면 `onRetry`로 `detailQuery.refetch()`(`:407`). `classifyServerError`가 `fatal`이면 `onRetry` 없음. [확인: shared-types/src/serverError.ts:47-48(fatal → traceId 포함)] | 재시도 가능(`retryable`): `ErrorMessage` 내 `onRetry` 클릭 → `detailQuery.refetch()`. 재시도 불가(`fatal`): 버튼 없음, 오류 문구만 표시. [확인: routes/portfolio/PerformanceStatementsPage.tsx:406-408(classifyServerError.kind === "retryable" ? refetch : undefined)] | 구현됨 — 상세 오류 분기(`:395-410`)에서 `classifyServerError`(`:407`)로 `retryable`/`fatal` 판정. `retryable`일 때만 `onRetry` 전달(`:407`). 목록 오류도 동일 경로(`:317-318, 360-365`). `ErrorMessage` 컴포넌트(`frontend/apps/web/src/components/ErrorMessage.tsx:58-70`)가 danger `Alert`로 문구·trace_id·재시도 버튼 표시. |
| 네트워크 단절 | `useQuery`의 네트워크 실패는 `isError` 상태 + 일반 `Error` 객체(`message`만 있음). `routeApiError`(`:317`)는 `errorCode`가 없어 `{kind: "unknown"}` 반환. `listCanRetry`는 `false`. [확인: errorRouting.ts:132(unknown 수렴)] | 목록 오류 시 `ErrorMessage`(`:360-364`)가 `message`(Error.message)만 표시. `errorCode`/`traceId` 없음. React Query 기본 재시도(3회) 후 실패 시 표시. [확인: routes/portfolio/PerformanceStatementsPage.tsx:362(error instanceof Error ? error.message : undefined)] | React Query 자동 재시도(기본 3회) 후에도 실패하면 `ErrorMessage` 표시. 수동 재시도 버튼 없음(`listCanRetry` false). [확인: routes/portfolio/PerformanceStatementsPage.tsx:318(listCanRetry = false for "unknown")] | 부분 구현 — 네트워크 오류는 `ErrorMessage`로 표시되나(`:360-364`), 수동 재시도 버튼 없음. React Query 기본 재시도(3회)에 의존. 네트워크 복구 후 수동 새로고침 방법 없음(재조회 버튼 미구현). |
| 부분 실패(일부 데이터만 실패) | 목록(`listQuery`)과 상세(`detailQuery`)는 독립 `useQuery`. 목록 성공/상세 실패 또는 그 반대 가능. [확인: routes/portfolio/PerformanceStatementsPage.tsx:300-309(listQuery, detailQuery 독립 queryKey)] | 목록 성공 시 항목 렌더링(`:371-388`). 선택한 상세만 실패하면 해당 상세 영역에 `ErrorMessage`(`:402-409`) 또는 `NotFoundState`(`:397-400`) 표시. 목록 실패 시 상세 영역 표시 안 함(`:359-368`). [확인: routes/portfolio/PerformanceStatementsPage.tsx:359-417(목록 오류 분기, 상세 분기 독립)] | 목록은 재조회(`:365`), 상세는 `retryable`일 때 재시도(`:407`). 다른 항목 선택 시 재조회 자동(`selectedId` 변경 → `detailQuery` 재실행). [확인: routes/portfolio/PerformanceStatementsPage.tsx:306-308(enabled: selectedId !== null)] | 구현됨 — 목록/상세 쿼리 독립 실행(`:300-309`). 목록 실패 시 상세 영역 렌더 안 함(`:359-368`), 상세 실패 시 목록은 유지(`:395-410`). |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-PerformanceStatementsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:178`) — 정정 요청 | 정정 대상 명세서(`statement`)가 로드됨. `reason` 입력 필드에 공백이 아닌 텍스트가 있음 (`:183` `disabled={!reason.trim()}`). [확인: routes/portfolio/PerformanceStatementsPage.tsx:102(reason state), :183(disabled)] | `correct.mutate()` → `correctStatement(statement.id, { reason })` API 호출 (`:104`). `onSuccess`에서 `setReason("")` (`:106`) + `onCorrected()` 호출 → `refreshAfterMutation(id)`로 목록/상세 쿼리 무효화 (`:107, :415`). 성공 시 `Alert tone="success"`로 "정정 요청이 반영되었습니다." 표시 (`:195`). [확인: routes/portfolio/PerformanceStatementsPage.tsx:103-107, :415] | `correct.isError`가 true → `ErrorMessage` 컴포넌트가 `errorCode`/`message`/`traceId` 표시 (`:189-193`). 재시도 버튼 없음(입력 고쳐 재제출). [확인: routes/portfolio/PerformanceStatementsPage.tsx:189-193] | 같은 화면 (정정 후 목록/상세 갱신, 화면 전환 없음) |
| `<Button>` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:256`) — 계산 요청 | `periodStart`와 `periodEnd`가 모두 입력됨 (`:260` `disabled={!periodStart || !periodEnd}`). `methodologyVersion`은 선택사항. [확인: routes/portfolio/PerformanceStatementsPage.tsx:227-253(form state)] | `compute.mutate()` → `computePerformanceStatement(body)` API 호출 (`:212`). `onSuccess`에서 `onComputed(newStatementId)` 호출 → `refreshAfterMutation(id)`로 목록/상세 갱신 (`:216`). 새 명세서가 목록에 추가됨. [확인: routes/portfolio/PerformanceStatementsPage.tsx:208-217] | `compute.isError`가 true → `ErrorMessage` 컴포넌트가 `errorCode`/`message`/`traceId` 표시 (`:265-270`). 재시도 버튼 없음. [확인: routes/portfolio/PerformanceStatementsPage.tsx:265-270] | 같은 화면 (계산 후 목록에 새 명세서 추가, 화면 전환 없음) |
| `<button>` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:375`) — 명세서 선택 | 목록 쿼리(`listQuery`)가 성공했고 `statements.length > 0` (`:371-388`). [확인: routes/portfolio/PerformanceStatementsPage.tsx:371-388] | `setSelectedId(s.id)` (`:377`) → `detailQuery.enabled`가 true가 되어 `getStatement(selectedId, { portfolioId })` 호출 (`:307-308`). 상세 데이터 로드 후 `StatementDetail` 컴포넌트에 전달 (`:412-416`). [확인: routes/portfolio/PerformanceStatementsPage.tsx:306-308, :412-416] | 상세 쿼리 실패 시 — 404(`isResourceNotFound`)면 `NotFoundState` 표시 (`:396-400`). 그 외 오류면 `ErrorMessage` 표시 (`:402-409`). `classifyServerError`가 `retryable`이면 `onRetry`로 재시도 (`:407`). [확인: routes/portfolio/PerformanceStatementsPage.tsx:396-409] | 같은 화면 (상세 영역에 선택한 명세서 상세 표시, 화면 전환 없음) |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-PerformanceStatementsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음 — 이 화면은 실적 명세서 조회·정정 전용이며, 금전·위험 행동(A1~A12: 주문, 전략 시작/정지/종료, LIVE 전환, 출금/충전, 위임장 변경, 킬스위치, 승인 설정 변경, 리밸런싱 등)이 없다. 있는 mutation은 두 개뿐: `computeStatement` (`PerformanceStatementsPage.tsx:208-217`, `apiClient.computePerformanceStatement`) — 명세서 계산 요청으로 데이터 재계산이며 자금 이동/주문/실행 상태 변경이 아님. `correctStatement` (`PerformanceStatementsPage.tsx:103-109`, `apiClient.correctPerformanceStatement`) — 명세서 정정 사유 제출로 데이터 정정 요청이며 금전 동작을 수반하지 않음. `routes/portfolio/PerformanceStatementsPage.tsx:52-57` 범위 내 API 호출 모두 GET 또는 명세서 데이터 조작에 국한됨. |
| 확인 절차 | 해당 없음 — 금전 행동 자체가 없음 | 해당 없음 |
| 멱등성 | 해당 없음 — 상태 변경 API 호출이 없음(`PerformanceStatementsPage.tsx:52-57` 범위 내 mutation은 compute/correct 둘 다 금전 행동 아님) | 해당 없음 |
| 불확실 결과 표시 | 해당 없음 — 비동기 서버 호출이 compute/correct 두 개뿐이며 둘 다 금전 행동이 아님. `correct.isError` 시 `ErrorMessage` 렌더링(`:189-194`), `compute.isError` 시 동일(`:266-271`) — 이는 일반적인 API 오류 표시일 뿐 금전 행동 불확실 결과 처리가 아님 | 해당 없음 |
| 다음 단계 화면의 금전 행동 | 미확인 — 이 리프의 범위는 현재 화면(`PerformanceStatementsPage.tsx`)뿐. 정정/계산 성공 시 `refreshAfterMutation`로 쿼리 무효화(`:313-314, 415, 216`)하여 목록/상세 갱신만 수행하고 다른 화면으로 네비게이트하지 않음 | 미확인 |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-PerformanceStatementsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `Alert` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:160`) | `performanceStatementsPage.identityMismatch` (residual 변수 포함) [확인: catalog.ko.extra.ts:74, catalog.en.ts:477] | 구성 요소 합계와 순손익 불일치 — 정정 요청(correct) 가능 또는 무시. trace_id 없음. | 아니요 |
| `Alert` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:325`) | `common.performanceNotGuaranteed` [확인: catalog.ko.ts:25, catalog.en.ts:32] | 성과 지표 공시 고지 (정보성). 별도 행동 없음. | 아니요 |
| `ErrorMessage` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:189`) | `getApiErrorMessage(errorCode, message)` + `legacy.errorMessage.t1`(trace_id), `legacy.errorMessage.t2`(retry 카운트다운), `legacy.errorMessage.t3`(재시도 버튼) [확인: ErrorMessage.tsx:36,60,64,67] | 정정 요청 실패 — 재시도 버튼 없음(onRetry 미전달). 원인 수정 후 재전송 필요. | 예 (ApiError.traceId) |
| `ErrorMessage` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:266`) | 동일 (legacy.errorMessage.t1/t2/t3) [확인: ErrorMessage.tsx:36,60,64,67] | 명세서 계산 실패 — 재시도 버튼 없음. 기간/범위 확인 후 재시도. | 예 (ApiError.traceId) |
| `ErrorMessage` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:360`) | 동일 + `retryAfterSec` (backoff_retry 시 카운트다운) [확인: ErrorMessage.tsx:36,60,64,67] | 명세서 목록 조회 실패 — backoff_retry면 카운트다운 후 버튼 활성화, refetch_retry면 즉시 활성화. | 예 (ApiError.traceId) |
| `routeApiError` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:317`) | 내부 유틸리티 — 사용자 문구 없음. kind 반환(refetch_retry/backoff_retry/other) [확인: routes/portfolio/PerformanceStatementsPage.tsx:317-318] | listQuery 오류 분류. ErrorMessage 렌더링 여부 결정. | 아니요 (분류만 수행) |
| `isResourceNotFound` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:396`) | 내부 유틸리티 — 사용자 문구 없음. boolean 반환 [확인: routes/portfolio/PerformanceStatementsPage.tsx:396] | 404 여부 판정. true면 NotFoundState, false면 ErrorMessage 렌더링. | 아니요 |
| `NotFoundState` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:397`) | `performanceStatementsPage.notFoundTitle`, `performanceStatementsPage.notFoundDescription` [확인: catalog.ko.extra.ts:79-80, catalog.en.ts:482-483] | 명세서 404 — "명세서를 찾을 수 없습니다". 별도 행동 없음. | 아니요 |
| `ErrorMessage` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:402`) | 동일 (legacy.errorMessage.t1/t2/t3) [확인: ErrorMessage.tsx:36,60,64,67] | 상세 조회 실패 (404 외) — classifyServerError(kind==="retryable")면 onRetry로 refetch 가능. | 예 (ApiError.traceId) |
| `classifyServerError` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:407`) | 내부 유틸리티 — 사용자 문구 없음. `{kind: "retryable"|"non-retryable"}` 반환 [확인: routes/portfolio/PerformanceStatementsPage.tsx:407] | detailQuery 오류 재시도 가능 여부 판정. 재시도 버튼 표시 결정. | 아니요 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-PerformanceStatementsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-PerformanceStatementsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-PerformanceStatementsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
