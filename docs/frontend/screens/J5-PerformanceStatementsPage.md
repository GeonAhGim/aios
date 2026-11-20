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
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-PerformanceStatementsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:178`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:256`) | TODO | TODO | TODO | TODO |
| `<button>` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:375`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-PerformanceStatementsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-PerformanceStatementsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `Alert` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:16`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:189`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:317`) | TODO | TODO | TODO |
| `isResourceNotFound` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:396`) | TODO | TODO | TODO |
| `NotFoundState` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:397`) | TODO | TODO | TODO |
| `classifyServerError` (`frontend/apps/web/src/routes/portfolio/PerformanceStatementsPage.tsx:407`) | TODO | TODO | TODO |
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
