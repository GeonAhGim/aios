# J3-ExecutionControlPage — `/executions`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ExecutionControlPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/executions` / `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-ExecutionControlPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더/투자자) — J1~J6 전체의 핵심 페르소나. 백테스트·검증(J2)을 거친 전략을 실제 실행(PAPER 또는 LIVE)으로 등록하고, 실행 목록을 감시·관리함 [PERSONAS_AND_JOURNEYS.md §2] |
| 목적(한 문장) | 전략 ID·버전·배분 자본·거래소·모드(PAPER/LIVE)를 입력해 실행을 생성하고, 생성된 실행 목록을 감시하며, LIVE 모드는 1회 확인 단계(UX_JOURNEYS.md §6.3)를 거쳐 실자금 주문을 제출한다 |
| 진입 조건 | `frontend/apps/web/src/router.tsx:127` — `/executions` 경로, `protect()` 래퍼(`ProtectedRoute`)로 인증된 세션 필수. LIVE 모드 선택 시 `Alert tone="warning"`으로 실제 자금 사용 경고 표시(`ExecutionControlPage.tsx:284~288`). FF_J3_RISK_PANEL 플래그 기본 OFF(`featureFlags.ts:16`) — 활성화 시 PRE_SUBMIT 위험 평가 배선(`ExecutionControlPage.tsx:205~207`) |
| 사용자 준비물 | 로그인 세션, 실행할 전략의 ID 및 버전, 배분할 자본 금액(USDT), 거래소 선택(bitget 현재 단일). LIVE 모드는 관리자 승인 대기 중일 수 있음(`approvalRequestId` 표시, `ExecutionControlPage.tsx:304~309`) |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-ExecutionControlPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useExecutions` | `frontend/packages/shared-hooks/src/usePortfolio.ts:37` | 5초 폴링 (`refetchInterval: 5000`, `FD-16.4` 원칙 — `frontend/packages/shared-hooks/src/usePortfolio.ts:42`) | `executionsIsError`/`executionsError` → `ExecutionsListError` 컴포넌트 표시(`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:320-321`). 404(`isResourceNotFound`)면 `NotFoundState`(`:64-71`), 그 외 `ErrorMessage` + `classifyServerError.kind === "retryable"`일 때 `onRetry` 버튼. `OperationStatusBar`에도 `isError` 전달(`:239`) |
| `useCreateExecution` | `frontend/packages/shared-hooks/src/usePortfolio.ts:46`, `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:158` | mutation (POST). 성공 시 `qc.invalidateQueries({queryKey: ["executions"]})`로 목록 자동 갱신(`usePortfolio.ts:56`) | `submitExecution`의 try/catch(`:211-214`). `DuplicateSubmitError`면 조용히 무시(`:212`). 그 외 `setError(err)` → `CreateExecutionError`(`:299-303`). `CreateExecutionError`: `classifyBadRequest`→`BadRequestNotice`(`:44`), `classifyForbidden`→`ForbiddenNotice`(`:45`), 그 외 `ErrorMessage`(재시도 가능 여부 `classifyServerError`로 판정, `:47-54`) |
| `useEvaluateRiskGate` | `frontend/packages/shared-hooks/src/useRiskGate.ts:50`, `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:159` | mutation (POST). `PRE_SUBMIT` 시점, `isFeatureEnabled("FF_J3_RISK_PANEL")`일 때만 호출(`:205-207`). flag 꺼지면 네트워크 호출 없음 | `RiskVerdictPanel`이 `status`/`data`/`error` prop으로 받음(`:312-316`). `error`는 `RiskVerdictPanel` 내부에서 처리 |
| `useIdempotentSubmit` | `frontend/apps/web/src/hooks/useIdempotentSubmit.ts:30`, `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:160` | 호출부 `submit(fn)`이 `Idempotency-Key` 헤더 관리. `inFlightRef`로 in-flight 중복 차단(`:36-39`). 성공 또는 4xx(재시도 불가) 후 키 폐기(`:43`, `:58-59`) | `DuplicateSubmitError`로 버튼 연타 차단(`:37`). 호출부에서 `err instanceof DuplicateSubmitError`면 return(`:212`). 4xx면 `discardKey` 후 에러 재던짐(`:58-59`). `missing_header`(개발 결함)는 `console.warn`(`:53-56`) |
| `useConflictRetry` | `frontend/apps/web/src/hooks/useConflictRetry.ts:18`, `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:181` | `run(...args)`로 감싼 mutation. `classifyStateConflict(err) !== "refetch_retry"`면 그대로 던지고, `"refetch_retry"`면 `refetch()` 후 `mutate(...args)`를 정확히 1회 재시도(`:27-29`) | 재시도 불가 타입(`STATE_INVALID_TRANSITION`, idempotency 실패, 409 아님)은 즉시 던짐(`:27`). 재시도 후에도 409 발생 시 재던짐(무한 재시도 금지). `:181-197`에서 `refetch`로 `useExecutions.refetch()` 전달 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-ExecutionControlPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `isLoading`이 true(153, 322). executions 훅에서 가져온 `isLoading`(153)은 react-query 기본값으로 첫 렌더 시 true이다. | 목록 영역에는 LoadingState(ui-web 컴포넌트) 렌더(322~323). 문구 키 없음. `OperationStatusBar`(236~240)가 `isLoading`을 받아 `summary === null`일 때 "알 수 없음"으로 표시한다(OperationStatusBar.tsx:66, 82~104). | 목록 영역에는 없음. 생성 폼(242~283)은 로딩과 무관하게 입력 가능. | 목록 LoadingState: `구현됨`(322~323행). `OperationStatusBar` "알 수 없음": `구현됨`(OperationStatusBar.tsx:66, 82~104). |
| 빈 상태 | 에러 아님, 로딩 아님, `executions`가 없거나 길이 0(324, 330). | 섹션 제목 `t13` "실행 목록"(319). 본문 `EmptyState`에 `t14` "실행 중인 전략이 없습니다."(331). | 목록에서는 없음. 위 폼으로 실행을 생성하는 것이 유일한 경로. | `구현됨`(330~331행). |
| 정상 | `executions.length > 0`(324). | 2열 그리드에 `ExecutionCard` 나열(325~328). 각 카드: 전략ID(`:72`), 교환/모드/자본 문구(`t1`, `:74`), 실현 PnL(`t2`, `:81`), 미실현 PnL(`t3`, `:83`), `StatusBadge`(`:77`), 시작/정지/재시작/LIVE전환/노출확대/은퇴 버튼(`:87~:177`). | 시작(`handleStart`, `:58~:66`), 정지(`pause.mutate`, `:143`), 재시작(`start.mutateAsync`, `:104`), LIVE 전환(`showLiveConversion`, `:148~:157`), 노출 확대(`showExposureExpand`, `:158~:167`), 은퇴(`retire.mutate`, `:168~:177`). | `구현됨`. 목록 렌더(325~328행), 카드 세부 모두 `ExecutionCard.tsx` 전체에서 확인. |
| 낡은 데이터(신선도 초과) | 이 파일에 stale·freshness 관련 참조가 없다(Grep 확인). | 보이는 낡음 표시 없음. `useExecutions` 훅이 `refetchInterval: 5000`(5초 폴링)을 설정(`usePortfolio.ts:42`). | 수동 갱신 경로 두 개: 목록 에러 재시도 `refetch`(321), 충돌 재조회 `refetch`(196). 새로고침 버튼 없음. | 화면 측 신선도 표시: `없음`(이 파일에 해당 참조 없음). 훅 측 자동 갱신: `구현됨`(`refetchInterval: 5000`, `usePortfolio.ts:42`). |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-ExecutionControlPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-ExecutionControlPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-ExecutionControlPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:134`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:136`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:279`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-ExecutionControlPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-ExecutionControlPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:4`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:5`) | TODO | TODO | TODO |
| `classifyServerError` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:6`) | TODO | TODO | TODO |
| `isResourceNotFound` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:7`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:8`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:11`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:44`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:45`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:49`) | TODO | TODO | TODO |
| `NotFoundState` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:66`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-ExecutionControlPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-ExecutionControlPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-ExecutionControlPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
