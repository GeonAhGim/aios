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
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-ReportsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| TODO | | | |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-ReportsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-ReportsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:21`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:21`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:22`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:25`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/reports/ReportsPage.tsx:63`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-ReportsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-ReportsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-ReportsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
