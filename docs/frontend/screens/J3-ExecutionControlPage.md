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
| 입력 오류(400/검증) | `CreateExecutionError`(`ExecutionControlPage.tsx:43-57`)에서 `classifyBadRequest(error)`(`ExecutionControlPage.tsx:44`)가 truthy — statusCode==400且 errorCode가 VALIDATION_INVALID_FIELD/VALIDATION_IDEMPOTENCY_KEY_REQUIRED/VALIDATION_DISCLOSURE_RETIRED/AUTH_MFA_INVALID/unknown 중 하나. 폼 입력값은 실패 후에도 유지됨(`setStrategyId("")`는 성공 시에만 호출, `ExecutionControlPage.tsx:210`). | `BadRequestNotice`(`BadRequestNotice.tsx:40-81`) 표시. 문구: `getApiErrorMessage(errorCode, message)`(`BadRequestNotice.tsx:62`). field 갈래且 `extractFieldErrors(error)`(`BadRequestNotice.tsx:49`) 비어있으면 `message \|\| getApiErrorMessage(errorCode)`(서버 사유 우선). field 갈래且 fields 있으면 null(인라인 표시 중복 금지). mfa_invalid일 때 `legacy.badRequestNotice.t2`(`BadRequestNotice.tsx:76`) 버튼, disclosure_retired일 때 `legacy.badRequestNotice.t1`(`BadRequestNotice.tsx:70`) 버튼. | field/unknown — 입력 수정 후 재시도(액션 버튼 없음, 재시도해도 같은 결과). disclosure_retired — `onReload`로 최신 revision 재조회(`BadRequestNotice.tsx:67-72`). mfa_invalid — `onFocusMfaCode`로 MFA 코드 입력 필드 포커스(`BadRequestNotice.tsx:73-78`). | 구현됨 |
| 인증 필요/세션 만료(401) | 이 파일에 401 전용 분기 없음. 401은 `ApiError`(`ExecutionControlPage.tsx:2`)로서 `CreateExecutionError`(`ExecutionControlPage.tsx:43`) 경로로 흘러가 `classifyBadRequest`(`ExecutionControlPage.tsx:44`)/`classifyForbidden`(`ExecutionControlPage.tsx:45`) 둘 다 null, 그 외 `ErrorMessage`(`ExecutionControlPage.tsx:49-55`)로 간다. | `ErrorMessage`(`ErrorMessage.tsx:27-72`) 표시. 문구: `getApiErrorMessage(errorCode, message)`(`ErrorMessage.tsx:36`). 재시도 버튼: `classifyRetry`가 "none"이 아닐 때만(`ErrorMessage.tsx:39`). 전역 401 처리는 `api-client/src/http.ts`(handleAuthFailure, `api-client/src/http.ts:104-130`)가 `notifyUnauthorized`(`api-client/src/http.ts:112`) 호출 → `configureUnauthorizedHandler`(`api-client/src/httpHeaders.ts:57`) 콜백으로 리다이렉트(로그인 페이지). 페이지 내 401 전용 UI 없음. | 전역 처리에 따라 로그인 페이지로 리다이렉트. 페이지 내 재시도 버튼 없음. | 없음 |
| 권한 없음/MFA 필요(403) | `CreateExecutionError`(`ExecutionControlPage.tsx:45`)에서 `classifyForbidden(error)`(`forbidden.ts:31`)가 truthy — statusCode==403且 errorCode가 AUTH_MFA_REQUIRED/AUTH_TENANT_MISMATCH/POLICY_*/RISK_*/AUTHZ_FORBIDDEN/AUTHZ_ZONE_VIOLATION 등. | `ForbiddenNotice`(`ForbiddenNotice.tsx:31-50`) 표시. 문구: `getApiErrorMessage(errorCode, message)`(`ForbiddenNotice.tsx:36`). mfa_required일 때 `legacy.forbiddenNotice.t1`(`ForbiddenNotice.tsx:45`) 버튼(onStepUp). policy일 때 `DenialReasons`(`ForbiddenNotice.tsx:41`) 컴포넌트(사유 목록). forbidden/tenant_mismatch — 버튼 없음. | mfa_required — `onStepUp`으로 step-up MFA 인증(`ForbiddenNotice.tsx:42-47`). policy — `DenialReasons`로 사유 확인 후 전략 수정. forbidden/tenant_mismatch — 재시도 불가(버튼 없음). (참고: `ExecutionsListError`(`ExecutionControlPage.tsx:62-83`)에는 classifyForbidden 분기 없음 — `ErrorMessage`로 간다) | 구현됨 |
| 충돌/상태 불일치(409) | `useConflictRetry`(`ExecutionControlPage.tsx:181-197`)가 감싸는 mutation에서 `classifyStateConflict(err)`(`stateConflict.ts:33`)가 `"refetch_retry"` — statusCode==409且 errorCode==STATE_CONCURRENCY_CONFLICT. | 재시도 중에는 별도 UI 없음(`useConflictRetry.ts:28-29`: refetch 후 mutate). 재시도 성공 시 제출 완료. 재시도 후에도 실패하면 `CreateExecutionError`(`ExecutionControlPage.tsx:43`) → `classifyServerError`(`ExecutionControlPage.tsx:47`) → `ErrorMessage`(`ExecutionControlPage.tsx:49-55`). `classifyServerError`(`serverError.ts:39`)는 EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY만 retryable로 보고, 409의 errorCode(STATE_CONCURRENCY_CONFLICT)는 대상 아님 → `not_applicable`(`serverError.ts:51`). 따라서 ErrorMessage에서 `serverError.kind === "retryable"`은 false → onRetry undefined(`ExecutionControlPage.tsx:54`) → 재시도 버튼 노출 없음. | `useConflictRetry`(`useConflictRetry.ts:18-36`)가 `refetch()` 후 `mutate(...args)`를 정확히 1회 재시도(`useConflictRetry.ts:28-29`). 재시도 후에도 409 발생 시 재던짐(무한 재시도 금지, `useConflictRetry.ts:27`). 최종 실패 시 ErrorMessage 표시되나 onRetry 없음(`classifyServerError`가 not_applicable). | 구현됨 |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-ExecutionControlPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 생성(POST) 423 AUTH_ACCOUNT_LOCKED: `CreateExecutionError`(`ExecutionControlPage.tsx:46`)에서 `routeApiError`가 `{kind: "account_locked", retryAfterSec}` 반환 — 그러나 이 컴포넌트는 `routed.kind`를 체크하지 않고 `classifyServerError` 결과로만 `onRetry`를 결정하므로, `account_locked`는 `classifyServerError`의 대상 아님 → `not_applicable` → `retryAfterSec`은 `undefined`(`ExecutionControlPage.tsx:53`). 429 RATE_LIMIT_EXCEEDED: `routeApiError`가 `{kind: "backoff_retry", afterSec}` 반환(`errorRouting.ts:121`), `retryAfterSec`은 설정되나 `classifyServerError`가 `not_applicable`(`serverError.ts:51`) → `onRetry`는 `undefined`. GET 429는 `withGetRetry`(`httpErrors.ts:59-60`)가 1회 자동 재시도(`client.test.ts:82-103`). POST 429는 자동 재시도 없음(`client.test.ts:111-118`). 페이지 내 accountLockout/deriveLockout 참조 없음. | 생성 423: `ErrorMessage` 표시(문구: 서버 메시지 또는 `extractDetailMessage` 폴백). 재시도 버튼 없음(`classifyRetry`가 "none", `onRetry`도 undefined). 429: `ErrorMessage`에 `retryAfterSec` 설정되나 `classifyServerError`가 `not_applicable` → `onRetry` undefined → 재시도 버튼 없음(`ErrorMessage.tsx:61`). GET 429는 api-client가 1회 자동 재시도(`httpErrors.ts:70-82`). 전역 `RateLimitNotice`(`main.tsx:29` 마운트, `RateLimitNotice.tsx:21-62`)는 `useRetryableAction`의 store 구독 — 그러나 이 페이지의 `submitExecution`(`ExecutionControlPage.tsx:1`)과 `useExecutions`(`:170`)가 `useRetryableAction`을 직접 쓰지 않으므로 배선 안됨(미확인). | 없음(재시도 버튼 없음). 423은 `retryAfterSec` 자체가 전달되지 않음. 429 GET은 api-client 자동 재시도. | 부분 구현(429 GET 자동 재시도만 동작, 429 POST/423은 재시도 UI 없음) |
| 서버 오류(5xx) | `classifyServerError`(`serverError.ts:25-28`): `EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY`(503)만 `retryable`. `EXCHANGE_FATAL`(502)/`INTERNAL_ERROR`(500)는 `fatal`. `classifyRetry`(`retryable.ts:27-31`): `RATE_LIMIT_EXCEEDED`/`EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY`만 `backoff`. `CreateExecutionError`(`ExecutionControlPage.tsx:54`): `serverError.kind === "retryable"`일 때만 `onRetry` 전달. 목록 `ExecutionsListError`(`:80`)도 동일 규칙. 503 `EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY`: `classifyRetry` → `backoff`, `classifyServerError` → `retryable` → `onRetry` 설정됨 → 재시도 버튼 노출. 502 `EXCHANGE_FATAL`/500 `INTERNAL_ERROR`: `classifyServerError` → `fatal`, `classifyRetry` → `none` → `canRetry` false(`ErrorMessage.tsx:39`) → 버튼 없음. GET 503은 `withGetRetry`(`httpErrors.ts:62`)가 1초→2초 상한 2회 자동 재시도. POST 503은 자동 재시도 없음(멱등키 충돌). | `EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY`(503): `ErrorMessage`에 재시도 버튼 노출(`ErrorMessage.tsx:61-69`). 카운트다운(`remainingSec`)과 "다시 시도" 버튼. 502/500: `ErrorMessage`만 표시(문구: `getApiErrorMessage`), 재시도 버튼 없음(`classifyRetry` → "none"). | 부분 구현(503 재시도 버튼 노출, 502/500 재시도 버튼 없음 — 의도적 설계) |
| 네트워크 단절 | 이 페이지에 TypeError/fetch 실패 전용 분기 없음(ExecutionControlPage.tsx 그랩 결과 0건). `http.ts:44-56`의 `fetchJson`에서 `fetch`가 TypeError 던지면: `executeRequest`(`http.ts:78-87`)에서 잡지 않음 → `performRequest`(`:69-76`)의 `handleRequestFailure`(`:73`)로 가나, `err instanceof ApiError` 체크(`http.ts:93`, `:104`)가 false → 재시도 없이 재던짐. 따라서 네트워크 실패는 TypeError로 원본 그대로 callers로 전파. 생성 경로(`ExecutionControlPage.tsx:213`): `err instanceof ApiError` false → `new Error(t("legacy.executionControlPage.t15"))`로 치환(원래 메시지 소실). 목록 경로(`:77`): `ExecutionsListError`에서 `error instanceof Error ? error.message : undefined` → TypeError 메시지 원문 노출. | 생성: `ErrorMessage`에 "실행 생성에 실패했습니다."(`ExecutionControlPage.tsx:213`) 표시. `classifyRetry`는 `errorCode`가 undefined → "none" → 재시도 버튼 없음. 목록: `ErrorMessage`에 TypeError 메시지 원문 노출(`:77`). 동일하게 재시도 버튼 없음. | 없음(TypeError → classifyRetry → "none" → 버튼 없음). 네트워크 복구 후 페이지 전체 리프레이트 필요. | 없음 |
| 부분 실패(일부 데이터만 실패) | 목록 GET 실패 시 `executionsIsError` true → `ExecutionsListError`(`ExecutionControlPage.tsx:320-321`)로 목록 영역 대체. 생성 폼(`:242-310`)은 `executionsIsError`와 무관 — 독립적으로 동작. `OperationStatusBar`(`:236-240`)에 `isError={executionsIsError}` 전달. `OperationStatusBar.tsx:107`: `{isError && <p className="text-danger">{t("operationStatusBar.loadFailed")}</p>}`로 "조회 실패" 문구 표시. `RiskVerdictPanel`(`:312-316`)에 `error={evaluateRiskGate.error}` 전달. `featureFlags.ts:16`: `FF_J3_RISK_PANEL` 기본 `false` → `submitExecution`(`:205-207`)에서 `evaluateRiskGate.mutate` 호출 안 함 → 기본 화면에서 위험 패널 에러 없음. | 목록 실패 시: 상단 상태 바에 "조회 실패"(`OperationStatusBar.tsx:107`) 표시, 목록 영역은 `ExecutionsListError`(에러 문구 + 재시도 버튼 if retryable). 폼 영역은 그대로 유지(입력값·제출 버튼 정상). 목록 재시도 성공 시 목록 영역 복귀. 위험 패널: 플래그 OFF이므로 기본 화면에서 에러 발생 안 함. | 목록 재시도(`ExecutionsListError.onRetry` → `refetch()`). 폼은 계속 사용 가능. | 구현됨 |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-ExecutionControlPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:134`) [취소] | `mode === "LIVE"`이고 `step === "confirm"`일 때 다이얼로그가 렌더된다(`ExecutionControlPage.tsx:289`). | `cancelLiveConfirm`(`:227~229`)이 `step`을 `"form"`으로 되돌려 다이얼로그가 닫힌다. 입력값은 유지된다. 비동기 호출 없음. | 같은 `/executions`. |
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:136`) [확정] | 다이얼로그가 열려 있을 때(`step === "confirm"`, LIVE). | `submitExecution`(`:199`)이 `setError(null)`, `setStep("form")`으로 다이얼로그를 닫고, `createExecutionWithRetry`(`:181~197`, `:208~209`)를 통해 POST를 보낸다. 성공 시 `strategyId`를 비운다(`:210`). `createExecution.data?.approvalRequestId`가 있으면 승인 대기 Alert(`:304~309`). | 같은 페이지. |
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:136`) [확정, 실패] | — | `DuplicateSubmitError`는 무시(`:212`). 그 외 `ApiError`면 `setError(err)`, 그 외 `t("legacy.executionControlPage.t15")`로 `setError` → `CreateExecutionError`(`:299-303`)가 `classifyBadRequest`→`BadRequestNotice`(`:44`), `classifyForbidden`→`ForbiddenNotice`(`:45`), 그 외 `ErrorMessage`(`:47-54`). 다이얼로그는 이미 닫혀 있음. 409는 `useConflictRetry`(`:181~197`)가 `refetch` 후 1회 재시도. | 재시도하려면 사용자가 다시 `실행 생성`(:279)을 눌러 확인 단계 열어야 함. |
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:279`) [제출] | `step === "form"`일 때만 버튼 렌더(`:278`). 필수 입력 `strategyId`/`strategyVersion`/`allocatedCapital`은 `required`(`:246`, `:250`, `:256`)라 브라우저가 빈 값 제출을 막음. | `handleSubmit`(`:217~225`)가 `submitExecution`을 호출. LIVE 첫 클릭은 `setStep("confirm")`만(`:219~222`). PAPER는 `submitExecution`이 바로 POST(`:224`). | 같은 페이지. |
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:279`) [제출, 실패] | — | 위 `CreateExecutionError` 경로(`:299-303`). | 재시도 필요. |
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:279`) [로딩] | `step === "form"`일 때. | `loading={createExecution.isPending}`(`:279`). | 미확인 — ui-web `Button`의 `loading` 동작 확인 안 함. |
| `Esc 키` (다이얼로그 닫기) | 다이얼로그 열림(`step === "confirm"`). | `useDialogFocusTrap`의 `onClose: onCancel`(`:107`) → `cancelLiveConfirm`(`:227~229`) → `setStep("form")`. | 다이얼로그 닫힘. |
| `Enter 키` (폼 제출) | `step === "form"`, 폼에 필수 입력 채움. | `onSubmit={handleSubmit}`(`:244`, `:217~225`) → LIVE면 `setStep("confirm")`(`:219~222`), PAPER면 `submitExecution`(`:224`). | 같은 페이지. |
| `handleModeChange` (`:172~175`) | 모드 변경(PAPER ↔ LIVE). | `setMode(next)`, `setStep("form")`으로 확인 단계 취소. | 같은 페이지. |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-ExecutionControlPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 있음. 이 화면은 금전 행동을 포함한다. (a) LIVE 모드 선택 시 실행 생성이 완료되면 실자금 주문을 생성한다(`ExecutionControlPage.tsx:219-222`, `:295`). LIVE 첫 제출은 확인 단계로만 간다(`:221`). (b) ExecutionCard의 LIVE 전환(`ExecutionCard.tsx:153`)과 노출 확대(`ExecutionCard.tsx:163`)가 있다. PAPER 생성은 모의 실행이므로 실자금이 아니다(`ExecutionControlPage.tsx:224`). 출금·위임장 변경은 이 화면에 없다. Grep 결과 `frontend/apps/web/src/routes/executions/`에서 출금(withdraw)/위임(delegat)/위임장(mandate) 키워드가 테스트 파일의 "delegateToRealClient"(실제 클라이언트 위임 테스트용 mock 함수명) 외에는 발견되지 않는다. |
| 확인 절차 | LIVE 첫 제출(`ExecutionControlPage.tsx:217-229`의 `handleSubmit`)은 주문을 보내지 않고 `ExecutionControlPage.tsx:221`의 `LiveOrderConfirmDialog`로 확인 단계를 거친다. 다이얼로그는 전략 ID·배분 자본·거래소를 요약하고 경고 문구를 표시한다(`ExecutionControlPage.tsx:124-132`). 확정 버튼(`ExecutionControlPage.tsx:136-142`)이 `ExecutionControlPage.tsx:295`의 `submitExecution()`을 호출하고, 취소 버튼(`ExecutionControlPage.tsx:134`) 또는 Esc(`ExecutionControlPage.tsx:107`)가 `ExecutionControlPage.tsx:227-229`의 `cancelLiveConfirm`으로 `setStep("form")`한다. PAPER는 확인 없이 바로 `ExecutionControlPage.tsx:224`의 `submitExecution()`을 호출한다. 프론트 위험 평가(`ExecutionControlPage.tsx:205-207`의 `FF_J3_RISK_PANEL`)는 `evaluateRiskGate.mutate`를 `await` 없이 호출하므로 제출을 차단하지 않음 — 백엔드가 막는지는 미확인. ExecutionCard의 LIVE 전환(`ExecutionCard.tsx:153`)은 `LiveConversionDialog`로 2단계 확인(input→confirm, `ExecutionCard.tsx:148`)을 거치며 비밀번호+TOTP 재인증을 받는다. 노출 확대(`ExecutionCard.tsx:163`)는 `ExposureExpandDialog`로 비밀번호+TOTP 재인증만 받고 별도의 확인 단계는 없다(한 번 입력하면 바로 제출). |
| 멱등성 | `ExecutionControlPage.tsx:160`의 `useIdempotentSubmit("executions.create")`이 `Idempotency-Key` 헤더를 관리한다. in-flight 중복 차단(`useIdempotentSubmit.ts:36-39`)으로 버튼 연타를 막고, 성공 또는 4xx 후 키를 폐기(`useIdempotentSubmit.ts:43`, `:58-59`). 호출부에서 `ExecutionControlPage.tsx:212`의 `DuplicateSubmitError`를 조용히 무시한다. 409 재시도는 `ExecutionControlPage.tsx:181`의 `useConflictRetry`가 `refetch()` 후 `mutate`를 1회 재시도하며, 재시도 시 새 idempotency 키를 발급한다(`useConflictRetry.ts:27-29`). 서버 측 멱등 처리는 프런트 범위 밖이므로 미확인. 확정 버튼의 `loading` 표시: `ExecutionControlPage.tsx:139`의 `LiveOrderConfirmDialog`가 `isPending`을 ui-web `Button`에 전달하지만, `ExecutionControlPage.tsx:201`의 `submitExecution`이 `setStep("form")`을 먼저 호출하므로 다이얼로그가 즉시 닫혀 로딩이 보이지 않을 수 있음 — 렌더 결과는 미확인. |
| 불확실 결과 표시 | (1) 네트워크 실패(TypeError): `ExecutionControlPage.tsx:213`에서 "실행 생성에 실패했습니다."로 원인이 사라진다. 주문이 접수됐는지 알 수 없다는 안내가 없음 — 구현됨(오류 표시)이나 원인 불명. (2) 이 경우 `shared-types/retryable.ts:41`의 `classifyRetry`가 errorCode 없이 `{kind: "none"}`을 반환하므로 재시도 버튼도 없음 — 부분(에러 표시만, 재시도 불가). (3) 승인 대기 상태: `ExecutionControlPage.tsx:304-309`의 `approvalRequestId`가 있으면 `Alert tone="warning"`으로 표시 — 구현됨. (4) 목록 반영: 5초 폴링(`shared-hooks/src/usePortfolio.ts:42`)으로 뒤늦게 반영 — 구현됨(지연 있음). |
| 미확인 | (a) 백엔드 측 idempotency 처리(서버가 Idempotency-Key를 어떻게 검증하는지). (b) `ExecutionControlPage.tsx:205-207`의 `FF_J3_RISK_PANEL` 활성화 시 백엔드가 PRE_SUBMIT 위험 평가를 통해 제출을 차단하는지. (c) ui-web `Button` 컴포넌트(`ui-web/src/Button.tsx:31`)의 `loading` prop이 `disabled`를 자동으로 설정하는지. (d) `ExposureExpandDialog`의 노출 확대가 백엔드에서 금전 행동으로 분류되는지. (e) ExecutionCard의 재시작(`ExecutionCard.tsx:58-66`의 `handleStart`)이 백엔드에서 금전 행동인지 — 재시작은 주문이 아닌 실행 상태 변경이므로 이 페이지의 money 범위에 포함하지 않음. |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-ExecutionControlPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:4`) | shared-types/apiError.ts EXACT_MESSAGES, 400 taxonomy 1:1 매핑 (에러 코드별 사용자 메시지) | 400 응답을 다섯 갈래(duplicate, field, disclosure_retired, mfa_invalid, unknown)로 분류. field는 인라인 오류가 있음이면 배너 숨김. disclosure_retired→`legacy.badRequestNotice.t1`("최신 내용 다시 불러오기") 버튼. mfa_invalid→`legacy.badRequestNotice.t2`("새 코드 입력") 버튼. | 아니요 — BadRequestNotice는 errorCode/message만 받고 trace_id를 받지 않음 (`BadRequestNotice.tsx:40-46`) |
| `classifyForbidden` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:5`) | shared-types/apiError.ts EXACT_MESSAGES, 403 taxonomy 1:1 매핑 (에러 코드별 사용자 메시지) | 403 응답을 네 갈래(policy, mfa_required, rate_limit, unknown)로 분류. policy→`DenialReasons` 컴포넌트 표시. mfa_required→`legacy.forbiddenNotice.t1`("step-up 인증") 버튼. | 아니요 — ForbiddenNotice는 errorCode/message만 받고 trace_id를 받지 않음 (`ForbiddenNotice.tsx:31-36`) |
| `classifyServerError` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:6`) | shared-types/apiError.ts EXACT_MESSAGES (에러 코드별 사용자 메시지) | EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY → `"retryable"`, EXCHANGE_FATAL/INTERNAL_ERROR → `"non_retryable"`. retryable이면 ErrorMessage에 onRetry 배선. | 아니요 — classifyServerError 자체는 판정만 반환. trace_id 노출 여부는 호출쪽 ErrorMessage에서 결정 |
| `isResourceNotFound` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:7`) | `legacy.executionControlPage.title1` ("실행 목록을 찾을 수 없습니다") | 404 응답이면 NotFoundState 렌더. 재시도 버튼 없음. 삭제되거나 존재하지 않는 데이터 안내. | 아니요 — NotFoundState는 title/description/action만 받고 trace_id를 받지 않음 (`NotFoundState.tsx:6-10`) |
| `routeApiError` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:8`) | shared-types/apiError.ts EXACT_MESSAGES (에러 코드별 사용자 메시지) | `"backoff_retry"`면 afterSec을 추출해 ErrorMessage의 retryAfterSec에 전달(카운트다운). 그 외(kind==="other")면 afterSec 없음. | 아니요 — routeApiError 자체는 trace_id를 반환하지 않음. trace_id는 별도 ApiError.instance 체크(`:50-51, :76-77`) |
| `Alert` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:11`) | `legacy.executionControlPage.t12` ("LIVE 모드 승인 대기 중입니다(요청 #{{approvalRequestId}}) — 강제 대기시간이 지난 뒤 관리자 승인이 필요합니다.") | 정보성 배너. 승인 요청 ID를 포함해 사용자에게 알림. 별도 액션 없음. | 아니요 — Alert 컴포넌트는 trace_id를 받지 않음 (`ExecutionControlPage.tsx:306-307`) |
| `BadRequestNotice` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:44`) | `getApiErrorMessage(errorCode, message)` — `BadRequestNotice.tsx:42-46`. field 갈래且 details.fields 비어 있으면 `message || fallbackText`. 그 외 `getApiErrorMessage(errorCode, message)` (`BadRequestNotice.tsx:62`) | tone="danger" Alert 배너. kind별 버튼: disclosure_retired→"최신 내용 다시 불러오기", mfa_invalid→"새 코드 입력". field且 fieldErrors 있으면 null(인라인 오류 중복 방지). | 아니요 — BadRequestNotice는 error 객체에서 errorCode/message만 추출하고 trace_id는 추출하지 않음 (`BadRequestNotice.tsx:28-38`) |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:45`) | `getApiErrorMessage(errorCode, message)` — `ForbiddenNotice.tsx:36`. kind="mfa_required"일 때 `legacy.forbiddenNotice.t1` ("step-up 인증") 버튼. | tone="danger" Alert 배너. kind="policy"이면 DenialReasons 하위에 상세 사유 표시. kind="mfa_required"이면 onStepUp 버튼. kind가 아니면 null(기존 ErrorMessage가 담당). | 아니요 — ForbiddenNotice는 errorCode/message만 추출하고 trace_id는 추출하지 않음 (`ForbiddenNotice.tsx:19-29`) |
| `ErrorMessage` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:49`) | `getApiErrorMessage(errorCode, message)` — `ErrorMessage.tsx:36`. trace_id 표시: `legacy.errorMessage.t1` ("지원코드: {{traceId}}"). 재시도 카운트다운: `legacy.errorMessage.t2` ("{{remainingSec}}초 후 재시도 가능"). 재시도 버튼: `legacy.errorMessage.t3` ("다시 시도"). | tone="danger" Alert 배너. errorCode+message로 사용자 메시지 생성. traceId 있으면 지원 코드 표시. classifyRetry(kind)가 "none" 아니면 재시도 버튼. backoff면 카운트다운 후 활성화. fieldErrors 있으면 null(중복 방지). | 예 — `ErrorMessage.tsx:60`: `traceId && <p>...{{traceId}}...</p>`로 trace_id 노출. CreateExecutionError(`:52`), ExecutionsListError(`:78`) 모두 `error instanceof ApiError ? error.traceId : undefined`로 전달 |
| `NotFoundState` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:66`) | `legacy.executionControlPage.title1` ("실행 목록을 찾을 수 없습니다") — `ExecutionControlPage.tsx:67`. description은 하드코딩 "삭제되었거나 존재하지 않는 데이터입니다." | dashed border 중앙 정렬 없음 상태. title + 선택 description. action prop으로 추가 버튼 가능. 재시도 버튼 없음. | 아니요 — NotFoundState는 trace_id prop을 받지 않음 (`NotFoundState.tsx:6-10`) |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-ExecutionControlPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | `LiveOrderConfirmDialog`에 `role="dialog"`(`ExecutionControlPage.tsx:113`), `aria-modal="true"`(`:114`), `aria-labelledby={LIVE_CONFIRM_TITLE_ID}`(`:115`) 적용. 제목 h2에 `id={LIVE_CONFIRM_TITLE_ID}`(`:120`). 경고 이모지 `aria-hidden="true"`(`:119`). `useDialogFocusTrap`으로 포커스 트랩 동작(`:107`). form Field 컴포넌트에 `label` prop으로 라벨 제공(`:245,248,255,263,268`). mode Select 변경 시 `handleModeChange`에서 `setStep("form")`으로 confirm 상태 초기화(`:174`). | `PageHeader`(`:234`)에 `aria-label` 명시 부재. `OperationStatusBar`(`:236-240`)에 role="status" 또는 aria-live 명시 부재. |
| i18n/지역화 | 모든 사용자 문구 `t("legacy.executionControlPage.*")` 키 사용(`ExecutionControlPage.tsx:67,121,125,127,129,132,135,142,213,234,243,248,268,273,274,280,286,307,319,331`, 총 20개 호출). facts 시트 카탈로그 누락 키 없음. `approvalRequestId`는 `{{approvalRequestId}}`로 interpolation(`:307`). | `NotFoundState.description`이 하드코딩 "삭제되었거나 존재하지 않는 데이터입니다."(`ExecutionControlPage.tsx:68`) — i18n 키 누락. |
| 성능 예산 | `useExecutions`는 5초 폴링(`refetchInterval: 5000`, `usePortfolio.ts:42`). `useCreateExecution`/`useEvaluateRiskGate`는 mutation(요청 시만 호출). `useConflictRetry`는 409 발생 시 refetch + 1회 재시도(무한 재시도 금지). `useIdempotentSubmit`은 in-flight 중복 차단. `FF_J3_RISK_PANEL` flag가 꺼져 있으면 risk gate 네트워크 호출 자체를 건너뜀(`ExecutionControlPage.tsx:205-207`). 목록 렌더링은 executions 배열 map으로 단순 반복, 각 ExecutionCard는 독립 컴포넌트. | useExecutions에 staleTime 미설정(기본 0 → 즉시 stale). 5초 폴링 중 매번 stale 재검사로 불필요 리페치 가능. |
| 텔레메트리 | `ExecutionControlPage.tsx` 전체에 명시적 telemetry/애널리틱스 호출 없음. 에러는 `ErrorMessage`/`BadRequestNotice`/`ForbiddenNotice` 컴포넌트로 사용자에게 표시(`:44-54,62-83`). `routeApiError`로 에러 분류(task-483). | 확인한 텔레메트리(로깅·애널리틱스·RUM) 없음. 실행 생성 성공/실패, LIVE 모드 전환, RiskGate 평가 등 핵심 사용자 액션 추적 없음. |
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
