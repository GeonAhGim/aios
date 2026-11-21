# J5-ReportsPage — `/reports`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ReportsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/reports` / `frontend/apps/web/src/routes/reports/ReportsPage.tsx`
- 여정 소속: J5


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J5-ReportsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — J5(감시·복구) 여정의 한 화면. 페르소나 목록: PERSONAS_AND_JOURNEYS.md §2. |
| 목적(한 문장) | 기간별 전략 성과(총수익률·승률·최대낙폭·거래횟수·손익추이·전략별기여도)를 확인하고 분석한다. |
| 진입 조건 | `/reports` 라우트(`frontend/apps/web/src/router.tsx:140`), `protect()` → `ProtectedRoute`로 인증 필요. 가드/플래그 없음(모든 인증 사용자 접근 가능). |
| 사용자 준비물 | 로그인 세션(`ProtectedRoute`), 기간 선택 가능(시작일/종료일, 기본 30일), 조회할 포트폴리오 데이터가 있는 상태. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J5-ReportsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useReport` | `frontend/packages/shared-hooks/src/usePortfolio.ts:29` | 1회 조회; `useQuery`(usePortfolio.ts:30); `refetchInterval` 없음(usePortfolio.ts:30-34); 폴링 없음; 자동 재조회는 queryKey(`["report", periodStart, periodEnd, executionId]`, usePortfolio.ts:31) 변경 시(기간 입력 바뀌면 키 변경), `enabled: !!periodStart && !!periodEnd`(usePortfolio.ts:33); 수동 재조회는 ReportsPage.tsx:58 `onRetry={() => refetch()}`; 변경 요청(mutation) 없음; react-query 전역 옵션(staleTime, retry 기본 횟수, refetchOnWindowFocus)은 미확인 | `isError`이면 ReportError, 로딩이면 LoadingState, 데이터 있으면 본문(ReportsPage.tsx:57-61); ReportError(ReportsPage.tsx:20-33): `classifyForbidden`(ReportsPage.tsx:21) 참이면 `ForbiddenNotice`(ReportsPage.tsx:21); 그 밖의 오류는 `routeApiError`(ReportsPage.tsx:22) 결과 분기; `refetch_retry` 또는 `backoff_retry`일 때만 재시도 버튼(onRetry, ReportsPage.tsx:23, 30); `backoff_retry`는 `retryAfterSec` 함께 전달(ReportsPage.tsx:29); 20-21줄 주석은 "err.message 직접 노출 안 함"이지만 27줄은 `error.message`를 ErrorMessage에 직접 전달 — 불일치 기록; ErrorMessage 렌더링 동작은 미확인 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J5-ReportsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `isLoading` true (`usePortfolio.ts:30`의 `useQuery`가 데이터 요청 중) | `LoadingState` 컴포넌트 (`ReportsPage.tsx:60`) — 스피너 아이콘과 "불러오는 중..." 문구 (`Feedback.tsx:35`) | 시작일/종료일 입력 변경 (`ReportsPage.tsx:49`, `:53`) | 구현됨 (`ReportsPage.tsx:59-60`) |
| 빈 상태 — 거래 기록 없음 | `report` 존재, `report.strategyContributions.length === 0` (`ReportsPage.tsx:91`) | `EmptyState` 컴포넌트 (`ReportsPage.tsx:115`) — `legacy.reportsPage.t13` ("해당 기간 거래 내역이 없습니다.") | 시작일/종료일 변경 후 재조회 | 구현됨 (`ReportsPage.tsx:91`, `:115`) |
| 빈 상태 — 일별 손익 없음 | `report` 존재, `report.dailyPnl.length === 0` (`ReportsPage.tsx:76`) | 손익 추이 카드(`Card`+`PnlChart`, `ReportsPage.tsx:76-87`)가 통째로 숨겨짐. 문구 없음. | 시작일/종료일 변경 후 재조회 | 구현됨 (`ReportsPage.tsx:76`) |
| 빈 상태 — 데이터도 로딩도 오류도 없음 | `periodStart` 또는 `periodEnd`가 빈 문자열 (`usePortfolio.ts:33` `enabled: !!periodStart && !!periodEnd` false) → `isLoading` false, `report` undefined | 119행 `null`로 빈 화면. 안내 문구 없음. | 시작일/종료일 입력 | 구현됨 (`ReportsPage.tsx:119`) |
| 정상 | `report` 존재 (`ReportsPage.tsx:61`) | `common.performanceNotGuaranteed` 경고 Alert (`ReportsPage.tsx:63`); 총수익률(`legacy.reportsPage.label4`, `:67`), 승률(`legacy.reportsPage.label5`, `:71`), MDD(`legacy.reportsPage.label6`, `:72`), 거래 횟수(`legacy.reportsPage.label7`, `:73`) Stat 4개; 손익 추이 `PnlChart` (`:76-87`, `legacy.reportsPage.t8`, `:78`); 전략별 기여도 표 (`:89-110`, `legacy.reportsPage.t9`, `:90`) — 전략(`t10`, `:96`), 실현 손익(`t11`, `:97`), 거래수(`t12`, `:98`) | 시작일/종료일 입력 변경 (`ReportsPage.tsx:49`, `:53`) | 구현됨 (`ReportsPage.tsx:61-118`) |
| 낡은 데이터(신선도 초과) | — | 없음 | 없음 | 없음 (`ReportsPage.tsx:1-9` import에 신선도 관리 클래스 없음; `usePortfolio.ts:30-34`에 refetchInterval 없음; generateReport 응답에 as_of 시각 필드 확인 못 함 — 미확인) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J5-ReportsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `classifyForbidden` false, `routeApiError`가 `field_errors` 또는 그 외 400 반환 (`errorRouting.ts:119-123` 분류기 전 `classifyBadRequest`가 400을 잡음 — 우선순위 규칙 주석 참조) | `ErrorMessage` (`ReportsPage.tsx:25`); `errorCode`/`message`/`traceId` 전달; 재시도 버튼 없음(`routed.kind`가 `refetch_retry`/`backoff_retry` 아님, `ReportsPage.tsx:23`) | 시작일/종료일 형식 교정 후 재조회 | 구현됨 (`ReportsPage.tsx:20-33`, `ErrorMessage.tsx:27-72`) |
| 인증 필요/세션 만료(401) | `classifyForbidden` false, `routeApiError`가 `{ kind: "auth_required" }` 반환 (`errorRouting.ts:128-129` — `isSessionExpiredErrorCode`) | `ErrorMessage` (`ReportsPage.tsx:25`); 세션 만료 문구(`ErrorMessage.tsx:36` — `getApiErrorMessage`); 재시도 버튼 없음(`auth_required`는 retryable 아님) | 재로그인 | 구현됨 (`ReportsPage.tsx:20-33`, `errorRouting.ts:128-129`) |
| 권한 없음/MFA 필요(403) | `classifyForbidden` 참 (`ReportsPage.tsx:21` — statusCode === 403) | `ForbiddenNotice` (`ReportsPage.tsx:21`); `classifyForbidden` 결과에 따라 4갈래(mfa_required/tenant_mismatch/policy/forbidden) 각각 문구(`forbidden.ts:31-41`); MFA일 때 onStepUp 버튼(ReportsPage는 onStepUp 미전달) | MFA 재인증, 관리자 문의 | 구현됨 (`ReportsPage.tsx:21`, `ForbiddenNotice.tsx:31-50`, `forbidden.ts:31-41`) |
| 충돌/상태 불일치(409) | `classifyForbidden` false, `routeApiError`가 `data_coverage_missing`/`wallet_balance_drift`/`invalid_transition` 반환 (`errorRouting.ts:104-113`) | `ErrorMessage` (`ReportsPage.tsx:25`); 재시도 버튼 없음(409 갈래가 retryable 아님) | 페이지 새로고침, 데이터 수집 후 재요청 | 구현됨 (`ReportsPage.tsx:20-33`, `errorRouting.ts:104-113`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J5-ReportsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | `statusCode=423` + `errorCode="AUTH_ACCOUNT_LOCKED"` (`errorRouting.ts:125-126` → `account_locked`) 또는 `statusCode=429` + `errorCode="RATE_LIMIT_EXCEEDED"` (`errorRouting.ts:119-121` → `backoff_retry`) | `ErrorMessage` (`ReportsPage.tsx:25`): `getApiErrorMessage`로 매핑된 한국어 문구 + `retryAfterSec` 카운트다운 + 재시도 버튼(카운트다운 0 전에는 disabled) (`ErrorMessage.tsx:49-66`) | 재시도 버튼 클릭(`onRetry` → `refetch`, `ReportsPage.tsx:30-31`); 카운트다운 후 수동 활성화 | 구현됨 (`ReportsPage.tsx:22-31`, `ErrorMessage.tsx:49-66`, `accountLockout.ts:41-50`, `retryable.ts:27-31`) |
| 서버 오류(5xx) | `statusCode=503` + `errorCode="EXCHANGE_UNAVAILABLE"`/`"DEPENDENCY_NOT_READY"` (`errorRouting.ts:115-116` → `backoff_retry`) 또는 `statusCode=502`/`500` + `errorCode="EXCHANGE_FATAL"`/`"INTERNAL_ERROR"` (`errorRouting.ts:117` → `server_fatal`) | retryable(503): `ErrorMessage` + `retryAfterSec` 카운트다운 + 재시도 버튼 (`ReportsPage.tsx:22-31`, `ErrorMessage.tsx:49-66`)<br>fatal(500/502): `ErrorMessage` + `traceId` 노출 (`ErrorMessage.tsx:60`) + 재시도 버튼 없음 | retryable: 재시도 버튼 클릭 → `refetch` (`ReportsPage.tsx:30-31`)<br>fatal: 페이지 새로고침 또는 관리자 문의 | 구현됨 (`ReportsPage.tsx:22-31`, `serverError.ts:25-30`, `errorRouting.ts:115-117`) |
| 네트워크 단절 | `fetch` throw (DNS 실패, 연결 거부, 타임아웃 등) → react-query가 `isError=true`, `error`에 `Error` 인스턴스 설정 | `ErrorMessage` (`ReportsPage.tsx:25`): `error.message` 직접 노출 (`ReportsPage.tsx:27`); `errorCode`/`traceId` 없음(plain Error이므로); 재시도 버튼 있음(`onRetry` 전달, `ReportsPage.tsx:30-31`) | 재시도 버튼 클릭 → `refetch` (`ReportsPage.tsx:30-31`) | 구현됨 (`ReportsPage.tsx:27-30`, `ReportsPage.test.tsx:100-111`) |
| 부분 실패(일부 데이터만 실패) | `useReport` 훅이 부분 데이터 반환(`shared-hooks/src/usePortfolio.ts:29`): `dailyPnl=[]` 또는 `strategyContributions=[]` | `dailyPnl=[]`: 손익 추이 카드 숨김 (`ReportsPage.tsx:76`), 문구 없음<br>`strategyContributions=[]`: `EmptyState` ("해당 기간 거래 내역이 없습니다.", `ReportsPage.tsx:115`)<br>기타 지표(수익률 등)는 정상 표시 | 시작일/종료일 변경 후 재조회 | 구현됨 (`ReportsPage.tsx:76`, `:91`, `:115`) — 단, `dailyPnl=[]` 시 안내 문구 없음(`ReportsPage.tsx:76` 조건부 렌더링만) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-ReportsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| 시작일 변경 (Input, ReportsPage.tsx:49) | 페이지 로딩 완료 또는 기존 기간 표시 중 (`periodStart` state 존재, ReportsPage.tsx:37) | `periodStart` state 업데이트(ReportsPage.tsx:37 `setPeriodStart`); `useReport`의 `enabled: !!periodStart && !!periodEnd`(usePortfolio.ts:33)에 따라 쿼리 키(`["report", periodStart, periodEnd, executionId]`, usePortfolio.ts:31) 변경 → 새 데이터 조회; 로딩 중이면 `LoadingState`(ReportsPage.tsx:59-60) | 기간 입력값이 유효한 날짜 형식이어야 함(HTML5 date input 검증); 유효하지 않으면 브라우저 기본 검증 메시지 표시, 조회 트리거되지 않음 | 동일 화면 (`/reports`, router.tsx:140) |
| 종료일 변경 (Input, ReportsPage.tsx:53) | 페이지 로딩 완료 또는 기존 기간 표시 중 (`periodEnd` state 존재, ReportsPage.tsx:38) | `periodEnd` state 업데이트(ReportsPage.tsx:38 `setPeriodEnd`); `useReport` 쿼리 키 변경 → 새 데이터 조회; 로딩 중이면 `LoadingState`(ReportsPage.tsx:59-60) | 기간 입력값이 유효한 날짜 형식이어야 함(HTML5 date input 검증); 유효하지 않으면 브라우저 기본 검증 메시지 표시, 조회 트리거되지 않음 | 동일 화면 (`/reports`, router.tsx:140) |
| 재조회 버튼 클릭 (ReportError → onRetry, ReportsPage.tsx:58) | `isError` true且且(`routed.kind === "refetch_retry"` 또는 `"backoff_retry"`, ReportsPage.tsx:23); `onRetry={() => refetch()}` 전달(ReportsPage.tsx:58) | `useReport`의 `refetch()` 호출 → 새 데이터 조회; 성공 시 정상 상태 표시(ReportsPage.tsx:61-118) | `canRetry` false(`routed.kind`가 위 두 가지 아님)이면 재시도 버튼 표시 안 함(ReportsPage.tsx:30 `onRetry={canRetry ? onRetry : undefined}`); `backoff_retry`일 때 `retryAfterSec` 초과 전에는 재시도 비활성 | 동일 화면 (`/reports`, router.tsx:140) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-ReportsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Input type="date">` (`ReportsPage.tsx:49`) — 시작일 변경 | 항상 활성(비활성화 조건 없음, `:47~50`), `value={periodStart}`(`:49`) | `onChange={(e) => setPeriodStart(e.target.value)}`(`:49`) → `periodStart` 상태 갱신. `useReport`(`usePortfolio.ts:29`)의 `queryKey`가 `["report", periodStart, periodEnd, executionId]`(`usePortfolio.ts:31`)이 변경되어 react-query가 자동으로 `generateReport` 재호출. `enabled: !!periodStart && !!periodEnd`(`usePortfolio.ts:33`) 참일 때만 요청 | `periodEnd`가 빈 문자열이면 `enabled` false → 요청 안 함. 그 밖의 네트워크 오류는 `isError` → `ReportError`(`:57-58`) 표시 | — (동일 화면, 데이터 갱신) |
| `<Input type="date">` (`ReportsPage.tsx:53`) — 종료일 변경 | 항상 활성(비활성화 조건 없음, `:51~54`), `value={periodEnd}`(`:53`) | `onChange={(e) => setPeriodEnd(e.target.value)}`(`:53`) → `periodEnd` 상태 갱신. `queryKey` 변경으로 `useReport` 자동 재호출. `enabled: !!periodStart && !!periodEnd`(`usePortfolio.ts:33`) 참일 때만 요청 | `periodStart`가 빈 문자열이면 `enabled` false → 요청 안 함. 그 밖의 오류는 `ReportError`(`:57-58`) | — (동일 화면, 데이터 갱신) |
| `ErrorMessage` `onRetry` (`ReportsPage.tsx:58`) — 재조회 | `isError` true (`usePortfolio.ts:30` useQuery가 오류 상태), `error` 존재 | `ReportError`(`:57-58`)의 `onRetry={() => refetch()}` 전달. `ErrorMessage` 재시도 버튼 클릭 시 `refetch()`(`usePortfolio.ts:29`) 호출 → 같은 기간으로 재조회. 성공 시 `report` 데이터 렌더링(`:61-118`) | `refetch()` 자체가 네트워크 오류를 반환하면 `isError` true 유지 → 같은 `ReportError` 다시 표시 | — (동일 화면) |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-ReportsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음. 이 화면은 기간별 전략 성과(총수익률·승률·MDD·거래횟수)를 표시할 뿐, 금액을 조작하거나 결제/이체/충전 등 금전 행위를 수행하지 않는다. `report.totalReturn`과 `report.maxDrawdown`는 퍼센트(%) 표시용 숫자이며(`ReportsPage.tsx:68,72`), Decimal(금액) 처리도 없음(클라이언트 화면의 읽기 전용 지표이므로 문제 없음). `strategyContributions` 배열의 `realizedPnl`(`ReportsPage.tsx:107`)은 표시만 하고 수정하지 않는다. |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-ReportsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:21`, `frontend/packages/shared-types/src/forbidden.ts:31`) | 403 응답을 네 갈래(mfa_required/tenant_mismatch/policy/forbidden)로 분류. `statusCode===403`일 때만 동작(`forbidden.ts:32`). errorCode별 갈래 매핑: `AUTH_MFA_REQUIRED`→mfa_required(`:35`), `AUTH_TENANT_MISMATCH`→tenant_mismatch(`:36`), `POLICY_*/RISK_*`→policy(`:37-38`), 그 외→forbidden(`:40`). | `ReportError`(`ReportsPage.tsx:21`)가 `classifyForbidden(error)` 참이면 `<ForbiddenNotice>` 렌더링. 403이 아니면 null 반환 → `routeApiError`(`:22`)로 계속. | trace_id 노출 없음. `classifyForbidden`은 errorCode/statusCode만 검사(403)하고 traceId 필드를 읽지 않음. |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:21`, `frontend/apps/web/src/components/ForbiddenNotice.tsx:31`) | 403 응답을 `Alert tone="danger"` 배너로 표시. `ForbiddenNotice.tsx:39-48`. `getApiErrorMessage`로 문구 조회(`:36`). policy 갈래면 `DenialReasons` 추가 표시(`:41`). mfa_required일 때 `onStepUp` 버튼(`:42-46`) 표시. | 문구는 `getApiErrorMessage`로 shared-types에서 조회(apiError.ts EXACT_MESSAGES). `Alert` 컴포넌트 렌더링. | trace_id 노출 없음. `ForbiddenNotice`는 errorCode/message만 사용하고 traceId를 렌더링하지 않음. |
| `routeApiError` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:22`, `frontend/packages/shared-types/src/errorRouting.ts:84`) | `errorRouting.ts:84-133`의 단일 진입점. 10단계 우선순위(idempotency→forbidden→stateConflict→data_coverage→wallet_drift→serverError→retry→notFound→lockout→sessionExpired→unknown)로 error_code를 18개 갈래 중 하나로 분류. | `RoutedApiError` 반환(kind + 부가 데이터). 호출부(`ReportError`)가 kind별로 `ErrorMessage` props 구성. | trace_id 노출: `server_fatal` 갈래일 때 `traceId` 전달(`errorRouting.ts:117`). |
| `ErrorMessage` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:25`, `frontend/apps/web/src/components/ErrorMessage.tsx:27`) | `ErrorMessage.tsx:27-72`. `getApiErrorMessage`로 문구 조회(`:36`). `classifyRetry`로 재시도 가능 여부 판정(kind: refetch/backoff/none, `:38`). backoff일 때 `retryAfterSec` 카운트다운(50-53줄). fieldErrors 있으면 렌더링 생략(55줄). | `Alert tone="danger"` 배너. traceId 있으면 `legacy.errorMessage.t1`(`:60`)로 노출. 재시도 가능하면 버튼 + 카운트다운(`:61-68`). | trace_id: `ErrorMessage.tsx:60`에서 `{traceId && <p>...t("legacy.errorMessage.t1", { traceId })</p>}`. backoff_retry일 때 `retryAfterSec` 전달(`ReportsPage.tsx:29`). |
| `Alert` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:63`) | `common.performanceNotGuaranteed` — "표시된 성과 지표(수익률·샤프·MDD 등)는 과거 측정값이며 공시 목적입니다. 미래 수익을 보장하지 않습니다." (`ReportsPage.tsx:63`, i18n 키 `common.performanceNotGuaranteed`) | 경고 배너. 닫기 버튼 없음. 항상 표시(조건부 숨김 없음). | trace_id 노출 없음. 정적 안내 문구. |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-ReportsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | `<Input type="date">`(`ReportsPage.tsx:49,53`) — `<label>`(`:47,51`)으로 감싸 라벨 연결. `Stat` 컴포넌트(`:66-73`) — `label` prop으로 텍스트 제공. `Alert tone="warning"`(`:63`) — `role="alert"` 자동 적용 여부 미확인. `ErrorMessage`(`:25`) — `Alert tone="danger"`(`ErrorMessage.tsx:58`). `PnlChart`(`:79`) — 차트 요소의 alt/aria-label 여부 미확인. `table`(`:93`) — `<th>`로 헤더 정의(`:95-98`). | `Stat`, `Card`, `PnlChart`, `LoadingState`, `EmptyState` 등 커스텀 컴포넌트의 aria 속성 구현 여부 미확인. `Alert`의 `role="alert"` 적용 여부 미확인. 숫자 표시(`totalReturn`, `maxDrawdown`)가 `%` 부호만으로 구분 — 색맹 사용자를 위해 `tone` prop 외에 텍스트 단서 추가 필요(§67 `Stat` 컴포넌트 구현 확인 필요). |
| i18n/지역화 | 14개 i18n 키 사용(`ReportsPage.tsx:44,48,52,63,67,71,72,73,78,90,96,97,98,115`). 모두 `t("legacy.reportsPage.*")` 또는 `t("common.performanceNotGuaranteed")` 형식. 날짜 입력은 HTML `type="date"`로 브라우저 로케일 의존(`:49,53`). 숫자 표시는 `${report.totalReturn}%`로 템플릿 문자열 — Intl.NumberFormat(표준 브라우저 API) 사용 안 함(`:68,72`). | 날짜 포맷이 브라우저 로케일에 따라 다름(서버에서 ISO 8601 반환하므로客户端 브라우저 설정에 의존). 숫자 포맷팅에 Intl.NumberFormat(표준 브라우저 API) 미사용(소수점 자리수 지역별 차이 없음). `winRate`가 `N/A`로 표시(`:71`) — i18n 키가 아닌 하드코딩 문자열. |
| 성능 예산 | 데이터 요청: `useReport` 1회(GET). 폴링 없음(`usePortfolio.ts:30-34`). 렌더: 차트 컴포넌트(`PnlChart`, `ReportsPage.tsx:79`)가 `dailyPnl`(`ReportsPage.tsx:82-83`) 배열을 `Number` 변환. `strategyContributions`(`ReportsPage.tsx:102`) 테이블 렌더링. React Query staleTime 기본값(0) — 즉시 stale. `refetchInterval` 없음. | LCP/INP 측정 목표 미정의. 차트 컴포넌트(`PnlChart`, `ReportsPage.tsx:79`)가 대용량 데이터셋에서 렌더링 성능이 떨어질 수 있음(데이터 포인트 수 제한 여부 미확인). |
| 텔레메트리 | `console.log`/`console.warn` 호출 없음(`ReportsPage.tsx`). React Query의 `onSuccess`/`onError` 콜백 없음. 페이지 뷰 기록, 오류 리포팅, 사용자 행동 추적 모두 없음. | 텔레메트리 미구현. 페이지 진입/종료, 기간 변경, 재시도 버튼 클릭 등 주요 이벤트 기록 없음. |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-ReportsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | `/reports` 라우트로 접근 시 로그인 세션이 없으면 `/login?next=/reports`로 리다이렉트된다. | `router.tsx:140`의 `protect(<ReportsPage />)` + `ProtectedRoute.tsx:20-21`의 `!token` 분기 |
| A2 | 시작일/종료일 입력 변경 시 `useReport`가 자동으로 재조회하고, `isError`일 때 `ReportError`가 렌더링되며, `isLoading`일 때 `LoadingState`가 표시된다. | `ReportsPage.tsx:57-60`의 조건부 렌더링 + `usePortfolio.ts:29`의 `useQuery` 상태 전이 |
| A3 | `report` 데이터가 있을 때 총수익률·승률·MDD·거래횟수 Stat 4개와 손익 추이 차트(데이터 있으면), 전략별 기여도 표(데이터 있으면)가 순차적으로 렌더링된다. 빈 거래 기간에는 `EmptyState`가 대신 표시된다. | `ReportsPage.tsx:65-118`의 렌더링 로직 — `dailyPnl.length > 0`(`:76`), `strategyContributions.length > 0`(`:91`) 조건부 분기 |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-ReportsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | 기간 선택 시 "조회" 버튼으로 명시적 요청 트리거 — 입력 변경마다 자동 재조회하면 빈 입력/빠른 키 입력 시 불필요한 요청 발생 | `onChange`에서 바로 `setPeriodStart`/`setPeriodEnd` 호출(`ReportsPage.tsx:49,53`) → 상태 갱신 후 react-query가 자동으로 재조회. "조회" 버튼 없음. | 낮(사용자 경험 개선) | "조회" 버튼 추가 또는debounce 처리(별도 리프) |
| G2 | `winRate`가 `null`일 때 "N/A" 대신 i18n 키로 표시 | `report.winRate ?? "N/A"`(`ReportsPage.tsx:71`) — `"N/A"`가 하드코딩됨. i18n 키(미정)로 추출 필요. | 낮(국제화 개선) | i18n 키 정의 및 추출(별도 리프) |
| G3 | `PnlChart`에 `aria-label` 또는 `role="img"` 등 접근성 속성이 있어야 함 | `PnlChart`(`ReportsPage.tsx:79`)에 `aria-*` prop 전달 없음. SVG 차트의 경우 `aria-label`이 필수(WCAG 1.4.11). | 중(접근성 위반) | `PnlChart` 컴포넌트에 `aria-label` prop 추가 |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
