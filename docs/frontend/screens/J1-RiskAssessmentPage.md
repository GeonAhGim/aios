# J1-RiskAssessmentPage — `/onboarding/risk-assessment`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/RiskAssessmentPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/risk-assessment` / `frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-RiskAssessmentPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:22`: "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독" [확인] |
| 목적(한 문장) | 회원가입(MFA 완료) 후 투자자 적합성평가를 통해 투자 경험·위험 감내도·투자 목표·자금 필요 시점을 수집하고, 이에 기반한 위험 등급을 안내한다. [확인: RiskAssessmentPage.tsx:142-144] |
| 진입 조건 | `router.tsx:88` — `/onboarding/risk-assessment` 라우트는 `protect()`로 ProtectedRoute 감싸짐. `ProtectedRoute.tsx:35-41` — `me.mfaEnabled=true`이고 `riskProfile`이 없을 때 강제 리다이렉트. 즉 MFA 완료 후 최초 진입 [확인] |
| 사용자 준비물 | MFA(2단계 인증) 완료 — `MfaSetupPage.tsx:72`에서 검증 성공 시 `/onboarding/risk-assessment`로 자동 전이 [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-RiskAssessmentPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useSubmitRiskAssessment` | `frontend/packages/shared-hooks/src/useSuitability.ts:15` | `useMutation` — 폼 제출(`RiskAssessmentPage.tsx:110`) 시 1회 POST `/api/v1/account/risk-assessment`(`frontend/packages/api-client/src/clients/account.ts:26`). `mutateAsync`로 동기 호출, 성공 시 `onSuccess` 훅(`useSuitability.ts:19`)이 `queryKey: ["riskProfile"]` 무효화. 폴링 없음(단일 POST). | `handleSubmit` catch(`RiskAssessmentPage.tsx:119`): `ApiError`면 그대로 `setError`, 아니면 `t("legacy.riskAssessmentPage.t12")`로 감싸서 `setError`. `SubmitError` 컴포넌트(`RiskAssessmentPage.tsx:64-76`)가 `classifyBadRequest`→`BadRequestNotice`, `classifyForbidden`→`ForbiddenNotice`, 나머지는 `routeApiError`→`ErrorMessage`(trace_id, retry_after 지원). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-RiskAssessmentPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩(초기) | 없음. 이 화면은 서버 조회를 하지 않는다. `useRiskProfile`은 `frontend/packages/shared-hooks/src/useSuitability.ts:6-13`에 정의만 있고 이 파일에서 쓰지 않음. | 로딩 스피너나 대기 UI 없음. 폼이 바로 렌더링된다. | — | 없음 |
| 로딩(제출 중) | `submit.isPending`이 true (`useSubmitRiskAssessment`의 mutation state). | 제출 버튼에 스피너 표시. 버튼 문구는 `legacy.riskAssessmentPage.t11` "제출하기" (`:203`). 입력 필드는 제출 중에도 `disabled`가 없어 수정 가능 (`:149-199`). | — | 미확인 (ui-web Button의 loading 스피너 시각 표현 확인 안 함) |
| 빈 상태 | 폼에 사용자가 입력하지 않은 상태. | 빈 상태 UI 없음('없음'). 대신 기본값이 모두 세팅됨: 경력 0년 (`:82`), 투자 가능 비중 10% (`:83`), 감내 손실 10% (`:84`), `LONG_TERM_GROWTH` (`:86`), `WITHIN_1_YEAR` (`:89`). 연수 입력은 `Number(e.target.value)` (`:154`)라 빈 문자열이 0이 됨. | 기본값으로 채워진 폼을 수정 후 제출 가능. | 없음(빈 상태 UI 없음 — 기본값으로 대체) |
| 정상 | 폼에 값이 입력된 상태. | 제목 `t1` "투자자 적합성평가 (필수)" (`:142`), 설명 `t2` ("투자 경험과 위험 감내도를 바탕으로 맞는 위험등급을 안내해드립니다. 강제 차단이 아니라 참고용 조언·불일치 경고 목적으로만 사용됩니다.") (`:144`), 라벨 `label3` "투자 경험 연수" (`:148`)·`label4` "투자 목표" (`:180`)·`label7` "자금 필요 시점" (`:190`), 선택지 `t5` "단기 수익 추구"·`t6` "장기 자산 성장" (`:185-186`), `t8` "1년 이내"·`t9` "1~3년"·`t10` "3년 이상" (`:195-197`), 제출 버튼 `t11` "제출하기" (`:203`). | 제출 성공 시 `clearDraft()` 후 `navigate("/dashboard")` (`:117-118`). 뒤로가기 링크 `backLink` "← 이전 단계(2단계 인증)로 돌아가기" → `/onboarding/mfa-setup` (`:127-131`). | 구현됨 (`:142-144`, `:148`, `:180`, `:185-186`, `:190`, `:195-197`, `:203`, `:117-118`, `:127-131`) |
| 낡은 데이터(신선도 초과) | 신선도 기준 없음. `readDraft()` (`:33-41`)는 타임스탬프나 만료 시간이 없다. | sessionStorage 초안을 복원한다 (`:81`, `:92`). 복원 안내 `restoredNotice` "세션 만료로 중단됐던 입력값을 복원했습니다." (`:135`)는 초안이 존재하기만 하면 표시됨 (`:133` `restoredFromDraft`). | 복원된 초안 값을 수정 후 제출 가능. 초안은 제출 성공 시에만 지워진다 (`:117`). | 구현됨 (`:33-41`, `:81`, `:92`, `:133-137`). 갭: 문구의 '세션 만료' 조건과 실제 조건(초안 존재 시 모두 표시)이 다름 |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-RiskAssessmentPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `classifyBadRequest(error)`가 null이 아님 (`:4`) — statusCode가 400. 네 갈래: field(`VALIDATION_INVALID_FIELD`), idempotency_key_required(`VALIDATION_IDEMPOTENCY_KEY_REQUIRED`), disclosure_retired(`VALIDATION_DISCLOSURE_RETIRED`), mfa_invalid(`AUTH_MFA_INVALID`). 알 수 없으면 `unknown`으로 수렴 (`shared-types/badRequest.ts:34-43`). | `<BadRequestNotice>`(`:65`)가 `<Alert tone="danger">`로 오류 문구 표시. 문구는 `getApiErrorMessage(errorCode, message)` (`shared-types/apiError.ts` EXACT_MESSAGES). field 갈래 중 `extractFieldErrors(error)`가 비어 있으면 배너 표시(인라인 필드 오류가 없을 때 폴백). `disclosure_retired`면 "최신 공시 다시 보기" 버튼(`legacy.badRequestNotice.t1`), `mfa_invalid`면 "MFA 코드 재입력" 버튼(`legacy.badRequestNotice.t2`) 표시. | 필드 오류 수정 후 재제출. `disclosure_retired`면 onReload(최신 공시 재조회) 클릭. `mfa_invalid`면 onFocusMfaCode(MFA 코드 입력란 포커스) 클릭. idempotency_key_required/disclosure_retired 외 갈래는 버튼 없음(재시도 무의미). | 구현됨 (`:4`, `:65`, `shared-types/badRequest.ts:34-43`, `components/BadRequestNotice.tsx:40-81`) |
| 인증 필요/세션 만료(401) | `classifyBadRequest`·`classifyForbidden`이 null, `routeApiError`가 `{kind:"auth_required"}` 반환 (`:6`). `isSessionExpiredErrorCode`가 true — errorCode가 `AUTH_TOKEN_EXPIRED`(`shared-types/apiError.ts:13`) 등 세션 만료 코드 (`shared-types/errorRouting.ts:128-130`). | `<ErrorMessage>`(`:69`)가 `<Alert tone="danger">`로 문구 표시. `getApiErrorMessage(errorCode, message)`로 번역 문구, trace_id 있으면 `legacy.errorMessage.t1`으로 보조 표시. 재시도 가능 여부: `classifyRetry`가 "refetch"면 `refetch_retry`(대기 없이 재시도 버튼 활성화), "backoff"면 `backoff_retry`(초 카운트다운 후 버튼 활성화). | 재로그인 후 페이지 재방문. backoff_retry면 retryAfterSec 카운트다운 후 재시도 버튼 클릭. refetch_retry면 즉시 재시도 버튼 클릭. | 구현됨 (`:6`, `:69`, `shared-types/errorRouting.ts:128-130`, `components/ErrorMessage.tsx:27-72`) |
| 권한 없음/MFA 필요(403) | `classifyForbidden(error)`가 null이 아님 (`:5`) — statusCode가 403. 네 갈래: mfa_required(`AUTH_MFA_REQUIRED`), tenant_mismatch(`AUTH_TENANT_MISMATCH`), policy(`POLICY_*/RISK_*`), forbidden(기타 403) (`shared-types/forbidden.ts:31-41`). | `<ForbiddenNotice>`(`:66`)가 `<Alert tone="danger">`로 문구 표시. `getApiErrorMessage(errorCode, message)`. policy 갈래면 `<DenialReasons>`로 상세 사유 목록 표시. mfa_required면 onStepUp 버튼(`legacy.forbiddenNotice.t1` "MFA 단계 업그레이드") 표시. | mfa_required면 onStepUp(MFA 단계 업그레이드) 클릭. policy 갈래면 DenialReasons에서 거부 사유 확인 후 답변 수정. tenant_mismatch/forbidden은 재시도해도 같은 결과(버튼 없음). | 구현됨 (`:5`, `:66`, `shared-types/forbidden.ts:31-41`, `components/ForbiddenNotice.tsx:31-50`) |
| 충돌/상태 불일치(409) | `classifyBadRequest`·`classifyForbidden`이 null, `routeApiError`가 stateConflict 갈래 반환 (`:6`). `classifyStateConflict`가 "refetch_retry"(`STATE_CONCURRENCY_CONFLICT`) 또는 "invalid_transition"(미지 409). DATA_COVERAGE_MISSING/INTEGRITY_WALLET_BALANCE_DRIFT도 409로 먼저 매핑 (`shared-types/errorRouting.ts:104-113`). | `<ErrorMessage>`(`:69`)로 표시. errorCode·traceId·retryAfterSec 전달. `classifyRetry` 분류: refetch_retry면 재시도 버튼 즉시 활성화, backoff_retry면 초 카운트다운, server_fatal이면 traceId만 표시. data_coverage_missing/wallet_balance_drift/invalid_transition은 재시도 버튼 없음(onRetry가 호출되지 않음). | refetch_retry면 즉시 재시도. backoff_retry면 카운트다운 후 재시도. data_coverage_missing/wallet_balance_drift/invalid_transition은 데이터 수집/정합성 조치 후 재시도(화면 재시도 버튼 없음). | 구현됨 (`:6`, `:69`, `shared-types/errorRouting.ts:104-113`, `shared-types/stateConflict.ts`, `components/ErrorMessage.tsx:27-72`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-RiskAssessmentPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | mutation 훅이 423(LOCKED) 또는 429(RATE_LIMIT_EXCEEDED)를 반환. `classifyRetry`(`shared-types/retryable.ts:40`)가 `RATE_LIMIT_EXCEEDED`를 `kind: "backoff"`로 분류. 423은 `classifyRetry`에 없으므로 `kind: "none"` 수렴. `routeApiError`(`shared-types/errorRouting.ts:88`)는 backoff면 `backoff_retry`, 나머지는 하위 분류기 매핑 실패 후 `classifyRetry(kind: "none")` → 기본 `not_found` 갈래를 타지 않고 `ErrorMessage`의 `errorCode`로 전달됨. | `ErrorMessage`(`RiskAssessmentPage.tsx:69-74`) 배너: `getApiErrorMessage`(`shared-types/apiError.ts`)가 `RATE_LIMIT_EXCEEDED`→"요청이 너무 많습니다. 잠시 후 다시 시도해 주세요." 문구 표시. `retryAfterSec`이 있으면 초 카운트다운(`:41-43`, `:49-53`)과 비활성화 재시도 버튼(`:66`). 카운트다운 0이 되면 버튼 활성화. 423(LOCKED)은 `kind: "none"` → 재시도 버튼 없음. | backoff_retry면 카운트다운 후 재시도 버튼 클릭. 423은 재시도 버튼 없음(같은 키로 잠김이 해소될 때까지 대기 후 새로고침). | 구현됨 (`:69-74`, `ErrorMessage.tsx:38-69`, `shared-types/retryable.ts:27-31`, `shared-types/errorRouting.ts:119-121`) |
| 서버 오류(5xx) | mutation 훅이 500~503 반환. `classifyServerError`(`shared-types/serverError.ts:39`)가 503(EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY)을 `retryable`, 500(EXCHANGE_FATAL/INTERNAL_ERROR)을 `fatal`로 분류. `routeApiError`(`:115-117`)는 retryable→`backoff_retry`, fatal→`server_fatal`. | `ErrorMessage` 배너: `getApiErrorMessage`가 errorCode에 따른 한국어 문구 표시. `fatal`(`server_fatal`)은 `traceId`를 `legacy.errorMessage.t1`로 `:60`에 표시. `retryable`(`backoff_retry`)은 `retryAfterSec` 카운트다운과 재시도 버튼(`:63-68`). | backoff_retry면 카운트다운 후 재시도. server_fatal은 traceId만 표시, 재시도 버튼 없음(서버 측 조치 필요). | 구현됨 (`:69-74`, `ErrorMessage.tsx:36-69`, `shared-types/serverError.ts:25-30`, `shared-types/errorRouting.ts:115-117`) |
| 네트워크 단절 | axios/fetch가 연결 실패, 타임아웃, DNS 실패 등을 반환. `err instanceof ApiError`가 false (`:120`). | `ErrorMessage` 배너: 상태 설정 함수가 `new Error(t("legacy.riskAssessmentPage.t12"))`로 감싸서 전달(`:120`). `errorCode`는 undefined, `traceId`는 undefined. `getApiErrorMessage(undefined, err.message)`가 fallback 문구("알 수 없는 오류가 발생했습니다.") 표시. 재시도 버튼 없음(classification.kind === "none"). | "제출하기" 버튼 재클릭. 네트워크 복구 후 동일 폼 값으로 재시도(초안 sessionStorage에 자동 저장됨 `:97-104`). | 구현됨 (`:119-121`, `:69-74`, `ErrorMessage.tsx:36-69`, `:97-104`) |
| 부분 실패(일부 데이터만 실패) | 해당 없음. 이 화면은 단일 POST `/api/v1/account/risk-assessment`(`frontend/packages/api-client/src/clients/account.ts:26`)로 5개 필드를 원자적으로 제출한다. 서버 단에서 일부만 기록되는 아키텍처가 아니다. | — | — | 없음(원자적 제출 아키텍처 — 부분 실패 경로 없음) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-RiskAssessmentPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:202`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-RiskAssessmentPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-RiskAssessmentPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:4`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:5`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:6`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:65`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:66`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:69`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-RiskAssessmentPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-RiskAssessmentPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-RiskAssessmentPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
