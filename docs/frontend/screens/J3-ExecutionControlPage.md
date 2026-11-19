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
| `useExecutions` | `frontend/packages/shared-hooks/src/usePortfolio.ts:37` | 마운트 시 자동 조회(`queryFn: apiClient.listExecutions()` `:41`). 5초 폴링(`refetchInterval: 5000` `:42`), queryKey `["executions"]` `:40`. | 훅에 `onError`/`retry` 없음(`frontend/packages/shared-hooks/src/usePortfolio.ts:37-43`), React Query 기본 동작. 화면 전달은 소비부 확인 전까지 '미확인' — 현재 코드에서 `useExecutions` 결과(`executionsError`, `executionsIsError` `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:155-156`)를 에러 표시에 배선하지 않음. |
| `useCreateExecution` | `frontend/packages/shared-hooks/src/usePortfolio.ts:46` | 변경 요청(`mutate`). 성공 시 `onSuccess`에서 `["executions"]` invalidate(`:56`)로 목록 즉시 갱신. | 훅에 `onError` 없음(`:46-57`), 예외가 호출부로 전파. `ExecutionControlPage.tsx:211-214` catch에서 `DuplicateSubmitError`면 silent, `ApiError`면 그대로 `setError`, 그 외는 `t("legacy.executionControlPage.t15")`("실행 생성에 실패했습니다.")로 `error` 상태 설정. `CreateExecutionError` 컴포넌트(`:43`)가 `classifyBadRequest`/`classifyForbidden`/`routeApiError`/`classifyServerError`로 분류해 `BadRequestNotice`/`ForbiddenNotice`/`ErrorMessage`로 화면 표시(`:300-302`). |
| `useEvaluateRiskGate` | `frontend/packages/shared-hooks/src/useRiskGate.ts:50` | 변경 요청만(`mutate({ gateKind: "PRE_SUBMIT" })` `ExecutionControlPage.tsx:206`). 자동 조회·invalidate 없음(`:50-53`). | `onError` 없음, 예외가 호출부로 전파. `ExecutionControlPage.tsx:205-207` — `FF_J3_RISK_PANEL` 플래그 아래에서만 호출(기본 OFF). 훅 실패는 `submitExecution`의 try/catch 바깥(`:205-207`)에서 catch되지 않음 — '미확인'. |
| `useIdempotentSubmit` | `frontend/apps/web/src/hooks/useIdempotentSubmit.ts:30` | 변경 요청을 감싸는 래퍼. `inFlightRef`로 동시 제출 가드(`:36-38`), `DuplicateSubmitError` 던짐(`:37`). 같은 키 재사용(`manager.getOrCreateKey` `:40`). 성공 시 키 폐기(`manager.discardKey` `:43`). | 예외를 다시 던짐(`:61`). 409 `new_key`·`missing_header`는 자동 재제출 안 함(`:46-50` 주석). `isNonRetryableFailure`로 4xx면 키 폐기(`:58-59`). 5xx/네트워크 오류는 키 유지(재시도 가능). |
| `useConflictRetry` | `frontend/apps/web/src/hooks/useConflictRetry.ts:18` | 변경 요청 래퍼. 1차 실패이 `classifyStateConflict(err) === "refetch_retry"`(`:27`)이면 `refetch()` 후 1회 재시도(`:28-29`). | 재시도 후에도 실패하면 그대로 throw, 무한 재시도 금지(`:12` 주석). `ExecutionControlPage.tsx:181-197`에서 `createExecutionWithRetry`로 사용하고, `submitExecution`의 catch(`:211-214`)가 최종 수신. `refetch` 인자는 `useExecutions`의 `refetch`(`:154`). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-ExecutionControlPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
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
