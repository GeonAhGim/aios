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

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-ReportsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음. 이 화면은 조회(reading) 전용이며, 자금 이동·주문·체결·전환 등 금전 행위(mutation)를 포함하지. `useReport`는 `useQuery`(read-only)이며 `usePortfolio.ts:29-34`에 mutation 훅 없음. 총수익률·승률·MDD는 Decimal 수치를 표시할 뿐이며, `Number()` 캐시(`ReportsPage.tsx:82-83`)는 차트 렌더링용(PnlChart가 number[] 요구)으로 `shared-types` 계약에 따른 필수 변환. |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-ReportsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:21`) | `getApiErrorMessage`에서 반환된 한국어 문구(forbidden.ts:7-9의 EXACT_MESSAGES) | `ForbiddenNotice`로 전이(ReportsPage.tsx:21); 403 유형별: `mfa_required`면 onStepUp 버튼(ForbiddenNotice.tsx:42-46), `policy`면 DenialReasons 상세(ForbiddenNotice.tsx:41), 나머지는 문구만 표시 | trace_id 노출 안 함(ForbiddenNotice는 errorCode만 사용, ForbiddenNotice.tsx:36) |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:21`) | `getApiErrorMessage` 매핑 결과(단일출처 apiError.ts) | `mfa_required`: MFA 단계 상향(onStepUp, ForbiddenNotice.tsx:44-46); `policy`: 사유 확인 후 관리자 문의; 그 밖의 403 유형: 문구 확인 후 브라우저 뒤로가기 또는 재로그인 | trace_id 노출 안 함(ForbiddenNotice.tsx:39-48) |
| `routeApiError` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:22`) | —(분류기, 문구 생성 안 함) | `refetch_retry`/`backoff_retry`면 재시도 버튼 표시; `server_fatal`이면 traceId 노출; `field_errors`면 ErrorMessage가 fieldErrors 받음(errorRouting.ts:86-132) | kind에 따라 다름(TRACE_ID는 server_fatal만) |
| `ErrorMessage` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:25`) | `getApiErrorMessage(errorCode, message)`(ErrorMessage.tsx:36) | 재시도 가능하면 onRetry 버튼 표시(카운트다운 후 활성화, ErrorMessage.tsx:61-68); fieldErrors 있으면 배너 숨김(ErrorMessage.tsx:55); traceId 있으면 하단 작은 글씨로 노출(ErrorMessage.tsx:60) | traceId 있으면 `legacy.errorMessage.t1` 키로 "traceId: {value}" 표시(ErrorMessage.tsx:60) |
| `Alert` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:63`) | `common.performanceNotGuaranteed` | 경고 배너(읽기 전용, 액션 버튼 없음); 스크롤 시 항상 가시 영역에 들어옴(고정 없음) | 해당 없음(성공 경로) |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-ReportsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | `label` 요소가 `span`과 `Input`을 연결(Rollup: label-for 규칙 준수, ReportsPage.tsx:48-49, :52-53); `Alert`이 `tone="warning"`/`"danger"`로 시각적 신호(색 비의존 표현 — 아이콘/이모지 추가 필요, 미확인); `table`에 `scope="col"` 미사용(th에 aria-label 필요, ReportsPage.tsx:96-98); PnLChart의 접근성 속성 미확인; `EmptyState`에 role/aria-live 속성 미확인 | `table` th에 `scope="col"` 추가 필요; 차트에 `aria-label` 대체 텍스트 필요; `Alert`에 아이콘 추가하여 색 비의존 표현 필요 |
| i18n/지역화 | 모든 가시 문자열이 `t()` 호출+i18n 키(`legacy.reportsPage.*`, `common.performanceNotGuaranteed`, `legacy.errorMessage.*`, `legacy.forbiddenNotice.*`); 날짜 입력은 HTML5 `type="date"`(로컬화 의존, ISO 형식 고정, ReportsPage.tsx:49, :53); 숫자 표시(`report.totalReturn%`, `ReportsPage.tsx:68`)가 지역별 포맷(천 단위 구분자 등) 미적용 | 숫자 포맷팅에 지역화 라이브러리 적용 필요; `isoDaysAgo`가 로컬 시간대 기준(UTC 권장, ReportsPage.tsx:12-14) |
| 성능 예산 | `useReport`는 1회 조회(useQuery, refetchInterval 없음, usePortfolio.ts:30-34); `PnlChart`가 `dailyPnl.map(Number)` 캐시 없음(재렌더마다 재계산, ReportsPage.tsx:80-84); `strategyContributions.map`도 재렌더마다 재실행(ReportsPage.tsx:102-110); 리플로우 없음(폴링/스트림 없음) | `PnlChart` 데이터와 `strategyContributions.map` 결과를 메모이제이션해야 함(데이터 크기 크기에 따라 영향) |
| 텔레메트리 | 페이지 로드/오류 이벤트 기록 코드 없음(ReportsPage.tsx import에 telemetry 없음); `ApiError`의 `traceId`는 ErrorMessage에 표시되지만 별도 로그 전송 없음 | 오류 발생 시 `traceId` 포함 텔레메트리 발송 필요(현재는 ErrorMessage UI 표시만) |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-ReportsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | 기간 선택(시작일/종료일) 입력 후 `useReport`가 `enabled: !!periodStart && !!periodEnd` 조건으로 두 날짜가 모두 채워질 때만 API 조회를 트리거한다. — 빈 문자열일 때는 조회가 발생하지 않는다. | `ReportsPage.test.tsx`: `periodStart`/`periodEnd`를 빈 문자열로 설정하고 `useReport` 호출 횟수 검증; 두 필드에 값 입력 후 `queryFn` 호출 확인 |
| A2 | 조회 실패 시 `classifyForbidden` 참이면 `ForbiddenNotice`만 렌더링하고 `ErrorMessage`는 렌더링하지 않는다. `classifyForbidden` 거짓이면 `ErrorMessage`를 렌더링하고 재시도 버튼은 `routed.kind`가 `refetch_retry` 또는 `backoff_retry`일 때만 표시한다. | `ReportsPage.test.tsx`: 403 응답 모킹 후 `ForbiddenNotice` 존재 + `ErrorMessage` 부재 검증; 500 응답 모킹 후 `ErrorMessage` + `traceId` + 재시도 버튼 부재 검증; 429 응답 모킹 후 `ErrorMessage` + 재시도 버튼 존재 검증 |
| A3 | `report.strategyContributions.length === 0`일 때 `EmptyState`("해당 기간 거래 내역이 없습니다.")가 표시되고, `report.dailyPnl.length === 0`일 때 손익 추이 카드(`Card`+`PnlChart`)가 통째로 숨겨진다. | `ReportsPage.test.tsx`: 빈 `strategyContributions` + 빈 `dailyPnl` 반환 모킹 후 `EmptyState` 존재 + `PnlChart` 부재 검증 |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-ReportsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | 숫자(총수익률, MDD)가 지역별 포맷(천 단위 구분자, 소수점 자리)으로 표시되어야 함 | `report.totalReturn%`와 `report.maxDrawdown%`가 `${value}%` 템플릿 리터럴로 직접 표시(ReportsPage.tsx:68, :72); 지역별 포맷 미적용 | 중요 | Phase 1 |
| G2 | `report.tradeCount`가 Decimal 또는 숫자 타입일 때 `%s` 없이 직접 표시(ReportsPage.tsx:73); 문자열 변환 없이 `{report.tradeCount}`로 렌더링 — 타입 불일치 시 "NaN" 또는 "[object Object]" 표시 가능성 | 개선 필요 | 개선 | Phase 1 |
| G3 | `isoDaysAgo`가 로컬 시간대 `Date`를 사용(ReportsPage.tsx:12-14); `new Date().toISOString().slice(0, 10)`은 로컬 날짜를 ISO 문자열로 잘라내므로 UTC 기준과 다른 날짜가 시작일 기본값이 될 수 있음 | 개선 필요 | 개선 | Phase 1 |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
