# J4-SafetyControlsPage — `/admin/safety-controls`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/SafetyControlsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/admin/safety-controls` / `frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx`
- 여정 소속: J4


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J4-SafetyControlsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P8 운영자 — `docs/frontend/PERSONAS_AND_JOURNEYS.md:29`(P8 행에 `safety-controls` 명시). 보조 P9(마스터) |
| 목적(한 문장) | 운영자가 안전 통제(R-53)를 조회·개통·해제하고, 리스크 게이트 평가·복구 평가·룰번들 승인/활성화를 수행하는 화면 — `frontend/apps/web/src/routes/admin/AdminHomePage.tsx:17`(`R-53 조회·해제`), `SafetyControlsPage.tsx:115`(activateHeading), `:163`(evaluateHeading), `:214`(approveHeading), `:299–338`(deactivate/recovery) |
| 진입 조건 | 라우트: `frontend/apps/web/src/router.tsx:174`(`/admin/safety-controls`, `protectAdmin(<SafetyControlsPage />)`). 가드: `router.tsx:74–77`에서 `AdminRoute`로 감쌈. `AdminRoute.tsx:45`는 `me.isPlatformAdmin`이 거짓이면 `/dashboard`로 보냄. 테스트 `AdminRoute.test.tsx:114`가 `isPlatformAdmin=false → /dashboard`를 확인. 진입 링크: `navItems.ts:66`, `AdminHomePage.tsx:17`. 기능 플래그: 미확인. MFA 요구 여부: 미확인 |
| 사용자 준비물 | 플랫폼 관리자 계정(`isPlatformAdmin=true`, `AdminRoute.tsx:45`). 개통: 대상 참조 scope_ref(선택, `SafetyControlsPage.tsx:126–128`), 사유(필수, `:133–135`). 평가: 커넥션 ID(선택, `:174–176`), 게이트 종류·범위 선택(`:165`의 Select). 룰번들: 룰번들 ID(필수, `:218–220`), 승인 참조 approval_ref(필수, `:225–227`). 해제·복구: 승인 요청 ID(미확인, `:319–321`), 증빙 참조 evidence_ref(미확인, `:326–328`) |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J4-SafetyControlsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useActivateSafetyControl` | `frontend/packages/shared-hooks/src/useRiskGate.ts:42` | Mutation(사용자 액션触发). 성공 시 `qc.invalidateQueries(["safetyControls"])`로 목록 갱신(`:46`). 폴링 없음(반복 폴링 없음). | 에러 콜백으로 에러 전달(`SafetyControlsPage.tsx:109`). `classifyForbidden`로 403 판정 후 `ForbiddenNotice`(`:35–36`), 그 외는 `ErrorMessage`(`:38`) |
| `useEvaluateRiskGate` | `frontend/packages/shared-hooks/src/useRiskGate.ts:50` | Mutation(사용자 액션触发). 성공 시 `onSuccess`로 로컬 상태 `result`에 결과 저장(`SafetyControlsPage.tsx:157`). 캐시 무효화 없음(`:52–53`). 폴링 없음. | 에러 콜백으로 에러 전달(`:157`). 동일하게 `ForbiddenNotice`/`ErrorMessage`로 라우팅 |
| `useApproveRuleBundle` | `frontend/packages/shared-hooks/src/useRiskGate.ts:56` | Mutation(사용자 액션触发). 성공 시 캐시 무효화 없음(`:57–60`). 폴링 없음. | 에러 콜백으로 에러 전달(`SafetyControlsPage.tsx:200`). 동일 라우팅 |
| `useActivateRuleBundle` | `frontend/packages/shared-hooks/src/useRiskGate.ts:63` | Mutation(사용자 액션触发). 성공 시 `onSuccess`로 로컬 상태 `bundle`에 결과 저장(`:207`). 캐시 무효화 없음(`:64–66`). 폴링 없음. | 에러 콜백으로 에러 전달(`:208`). 동일 라우팅 |
| `useSafetyControls` | `frontend/packages/shared-hooks/src/useRiskGate.ts:12` | useQuery(마운트 시 1회 조회, `:13–16`). `queryKey: ["safetyControls"]`, `queryFn: apiClient.listSafetyControls()`. 폴링 없음(반복 폴링 없음). 수동 갱신: `refetch()`(`SafetyControlsPage.tsx:244`, 360). 실패 시 재시도: React Query 기본(재시도 횟수 미설정 → 기본 0). | `isLoading`/`isError`/`data` 상태로 `SafetyControlsPage.tsx:359–366`에서 분기. 로딩 중: `LoadingState`(`:362`). 에러 시: `SafetyControlActionError` with retry 버튼(`:359–360`). 데이터 없음: `EmptyState`("No active safety controls.")(`:366`) |
| `useDeactivateSafetyControl` | `frontend/packages/shared-hooks/src/useRiskGate.ts:19` | Mutation(사용자 액션触发). 성공 시 `qc.invalidateQueries(["safetyControls"])`로 목록 갱신(`:23`). 폴링 없음. | 에러 콜백으로 에러 전달(`SafetyControlsPage.tsx:265`). 동일 라우팅 |
| `useEvaluateRecovery` | `frontend/packages/shared-hooks/src/useRiskGate.ts:27` | Mutation(사용자 액션触发). 성공 시 `qc.invalidateQueries(["safetyControls"])`(`:37`) + `onSuccess`로 로컬 상태에 결과 저장(`:279`). 폴링 없음. | 에러 콜백으로 에러 전달(`:280`). 동일 라우팅. validation 실패(approvalId 비숫자): `new Error("승인 요청 ID를 숫자로 입력하세요.")` 직접 발생(`:272`) |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J4-SafetyControlsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `useSafetyControls()`의 `isLoading === true` (`shared-hooks/src/useRiskGate.ts:13`) | `SafetyControlsPage.tsx:362`에서 `<LoadingState />` 표시 | 없음(로딩 중 동작 불가) | `구현됨` — `SafetyControlsPage.tsx:361-362` (`isLoading` 분기, `<LoadingState />` 컴포넌트 사용) |
| 빈 상태 | `data.controls.length === 0` (`shared-hooks/src/useRiskGate.ts:15` 조회 결과 빈 배열) | `SafetyControlsPage.tsx:366`에서 `<EmptyState>{t("legacy.safetyControlsPage.t9")}</EmptyState>` — 문구: "활성 안전 통제가 없습니다." | 없음(활성 통제 없음) | `구현됨` — `SafetyControlsPage.tsx:366` (`data.controls.length > 0`이 거짓일 때), 문구 키 `legacy.safetyControlsPage.t9` (`:366`) |
| 정상 | `data.controls.length > 0` (`shared-hooks/src/useRiskGate.ts:15` 조회 결과 비어 있지 않음) | `SafetyControlsPage.tsx:363-364`에서 `<ul>`로 각 통제 항목 렌더링 — `renderControl()`(`:285-349`)이 scope, scopeRef, StateBadge, reason, fenceToken, createdAt/deactivatedAt 표시. 활성 통제: 해제 버튼(`:304-313`), 복구 평가 입력+버튼(`:315-340`) | 1) 개통(`ActivateSafetyControlPanel`, `:113-143`), 2) 평가(`EvaluateRiskGatePanel`, `:161-185`), 3) 룰번들 승인/활성화(`RuleBundlePanel`, `:212-238`), 4) 해제(`:304-313`), 5) 복구 평가(`:315-340`) | `구현됨` — `SafetyControlsPage.tsx:363-364`, 각 패널 및 개별 통제 렌더링(`:285-349`) |
| 낡은 데이터(신선도 초과) | `useSafetyControls()`에 스테일스태스 설정 없음(`shared-hooks/src/useRiskGate.ts:13-16`) — 기본값으로 매번 새로 조회 | 정상과 동일하게 표시되지만 매 마운트/리페치 시 서버에서 최신 데이터 가져옴 | 재조회 시 자동으로 최신화 | `구현됨` — 스테일스태스 미설정(기본 0, 매번 fetch), 폴링 없음(`shared-hooks/src/useRiskGate.ts:13-16`) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J4-SafetyControlsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `routeApiError(error)`가 `field_errors`·`disclosure_retired`·`mfa_invalid` 반환 (`shared-types/src/errorRouting.ts:94–99`) | `ErrorMessage` 컴포넌트(`SafetyControlsPage.tsx:38`) — API 오류 메시지로 표시. `fieldErrors`가 있으면 배너 숨김(`ErrorMessage.tsx:55`) — 필드별 이미 표시 중. trace_id: `ErrorMessage.tsx:60`에서 `legacy.errorMessage.t1` 키로 노출 | `classifyRetry`가 "none"이 아니면 재시도 버튼(`ErrorMessage.tsx:39,61-67`). `field_errors`면 재시도 불가(필드 수정 필요) | `구현됨` — `SafetyControlsPage.tsx:36-44`에서 `routeApiError` 결과 전달, `ErrorMessage.tsx:55`에서 `fieldErrors` 체크 후 렌더링 분기 |
| 인증 필요/세션 만료(401) | `routeApiError(error)`가 `auth_required` 반환 (`shared-types/src/errorRouting.ts:127` — `isSessionExpiredErrorCode` 판정) | `ErrorMessage` 컴포넌트(`SafetyControlsPage.tsx:38`) — "인증이 필요합니다" 문구 표시. trace_id: ApiError일 때 `legacy.errorMessage.t1`로 노출 | `classifyRetry`가 "none"(세션 만료는 retry 아님) → 재시도 버튼 없음. 사용자 재로그인 필요 | `구현됨` — `routeApiError`가 `auth_required` 감지, `ErrorMessage`가 API 메시지 번역. 재시도 버튼 없음 |
| 권한 없음/MFA 필요(403) | `classifyForbidden(error)`가 truthy (`SafetyControlsPage.tsx:35`) | `ForbiddenNotice` 컴포넌트(`SafetyControlsPage.tsx:35`) — `classifyForbidden` 결과 4갈래(forbidden, mfa_required, tenant_mismatch, policy). API 메시지로 문구 표시. policy 갈래면 `DenialReasons` 상세 표시(`ForbiddenNotice.tsx:41`). MFA 갈래면 onStepUp 버튼(`ForbiddenNotice.tsx:42-46`) | MFA 갈래면 `onStepUp` 버튼. 나머지 갈래는 재시도 버튼 없음(`ForbiddenNotice.tsx:42` — 재시도해도 같은 결과 반복) | `미완` — `ForbiddenNotice`는 표시되나 `SafetyControlActionError` props에 `onStepUp`이 전달되지 않음(`SafetyControlsPage.tsx:34` 시그니처에 없음). MFA 단계 상승 액션 누락 |
| 충돌/상태 불일치(409) | `routeApiError(error)`가 `refetch_retry`·`invalid_transition` 반환 (`shared-types/src/errorRouting.ts:113–115`) | `ErrorMessage` 컴포넌트(`SafetyControlsPage.tsx:38`) — `getApiErrorMessage`로 메시지 표시. trace_id: ApiError일 때 `legacy.errorMessage.t1`로 노출 | `refetch_retry`면 `classifyRetry`가 refetch → 재시도 버튼 즉시 활성화(`ErrorMessage.tsx:39,66`). `invalid_transition`면 재시도 불가 | `구현됨` — `routeApiError`가 `refetch_retry`/`invalid_transition` 감지, `ErrorMessage.tsx:39`에서 `classifyRetry`로 재시도 가능 판정 |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J4-SafetyControlsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | `deriveLockout(err)`가 locked=true (423/AUTH_ACCOUNT_LOCKED, `accountLockout.ts:41–49`) 또는 `classifyRetry(err)`가 backoff (429/RATE_LIMIT_EXCEEDED, `retryable.ts:27–31`) | `ErrorMessage` 컴포넌트(`SafetyControlsPage.tsx:38`) — 메시지 표시. 423이면 `account_locked` 갈래(`errorRouting.ts:125–126`), 429이면 `backoff_retry` 갈래(`errorRouting.ts:116–117,121`). `ErrorMessage.tsx:41–43`에서 `retryAfterSec` 초기화, `:49–53`에서 1초 간격 카운트다운. 재시도 버튼 `:66`에서 `remainingSec > 0` 시 disabled | 카운트다운 종료 후 재시도 버튼 활성화. 423 잠김이면 서버 지정 시간(`retryAfterSec`)까지 대기 후 재시도. 429이면 백오프 후 재시도 | `구현됨` — `deriveLockout`(`accountLockout.ts:41–49`)가 423 판정, `classifyRetry`(`retryable.ts:40–53`)가 429 판정. `routeApiError`가 `account_locked`/`backoff_retry`로 매핑(`errorRouting.ts:116,121,126`). `ErrorMessage`가 카운트다운(`:49–53`)과 disabled 재시도 버튼(`:63–66`) 구현 |
| 서버 오류(5xx) | `classifyServerError(err)` 판정 — retryable: EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY(503, `serverError.ts:25–28`), fatal: EXCHANGE_FATAL/INTERNAL_ERROR(502/500, `:30`) | retryable(503): `backoff_retry` 갈래(`errorRouting.ts:116`) — `ErrorMessage`가 카운트다운+재시도 버튼 표시. fatal(502/500): `server_fatal` 갈래(`errorRouting.ts:117`) — `ErrorMessage`가 `traceId` 노출(`:60`), 재시도 불가(`classifyRetry`가 "none") | retryable이면 서버 백오프 후 재시도(1초, 2초 상한 2회, `httpErrors.ts:52`). fatal이면 재시도 불가, trace_id 확인 후 관리자에게 보고 | `구현됨` — `classifyServerError`(`serverError.ts:39–52`)가 retryable/fatal 판정. `routeApiError`가 `backoff_retry`/`server_fatal`으로 매핑(`errorRouting.ts:116–117`). `ErrorMessage`가 trace_id 노출(`:60`), 재시도 버튼은 `classifyRetry.kind !== "none"`일 때만 표시(`:39,61`) |
| 네트워크 단절 | `fetch`가 network error 던짐 (TypeError, network unreachable, DNS 실패 등). `http.ts:46`의 `fetch()`에서 try/catch 없음 → 그대로 전파 | `SafetyControlActionError`(`SafetyControlsPage.tsx:34–46`)가 network error 받음. `classifyForbidden`은 falsy(`:35`), `routeApiError`는 `unknown` 반환(`:36`). `ErrorMessage`가 errorCode/message/traceId 모두 undefined로 렌더 — "요청을 처리할 수 없습니다" 기본 문구(`httpErrors.ts:98`). trace_id 노출 없음 | `SafetyControlActionError`에 `onRetry={refetch}` 전달(`:360`) — 수동 재조회. React Query 기본 재시도 0회(재시도 횟수 미설정, `shared-hooks/src/useRiskGate.ts:13–16`)로 자동 재시도 없음 | `구현됨` — `SafetyControlsPage.tsx:359–360`에서 `isError` 분기 시 `refetch()` 재시도 제공. 네트워크 오류는 `ErrorMessage`에 undefined props 전달(`:38–42`). React Query 재시도 0회(기본값) |
| 부분 실패(일부 데이터만 실패) | 목록 조회(`useSafetyControls`)는 성공했으나 개별 mutation(해제/복구 평가) 중 일부만 실패. 또는 한 번의 API 응답에서 일부 통제만 실패 상태 | 각 통제 항목별(`renderControl` `:285–349`) `actionError`가 개별적으로 표시 (`:342–346`). `actionError`가 `{controlId, error}`로 관리(`:248`). 목록 전체는 표시되나 일부 항목만 에러 블록 표시. 정상 통제는 해제/복구 평가 계속 가능 | 실패한 통제별 `SafetyControlActionError` 표시. mutation 재시도 버튼은 없음(수동 재시도 = 다시 버튼 클릭). 다른 통제는 정상 동작 | `구현됨` — `actionError`가 `controlId`별로 관리(`:248`). `renderControl`이 `:342–346`에서 개별 에러 표시. 각 mutation(`deactivate.mutate`, `evaluateRecovery.mutate`)의 `onError`가 개별 controlId에 에러 저장(`:265,280`) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J4-SafetyControlsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:138`) [handleActivate] | 관리자 페이지 진입(`AdminRoute.tsx:45`는 `me.isPlatformAdmin` 체크). 범위 선택(`SAFETY_SCOPES`, `:27`), scope_ref(선택, `:126–128`), 사유 입력(필수, `:133–135`) | `useActivateSafetyControl`(`shared-hooks/src/useRiskGate.ts:42`) mutation 호출. 성공 시 `qc.invalidateQueries(["safetyControls"])`(`:46`)로 목록 갱신 | `onError` 콜백(`:109`)이 `setAdminActionError`(`:249`) 호출 → `SafetyControlActionError`(`:358`) 표시. `classifyForbidden`(`:35`)로 403 판정 시 `ForbiddenNotice`, 그 외 `ErrorMessage` | 동일 화면(목록 자동 갱신) |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:179`) [handleEvaluate] | 관리자 페이지 진입. 게이트 종류 선택(`GATE_KINDS`, `:28`), connectionId(선택, `:174–176`) | `useEvaluateRiskGate`(`shared-hooks/src/useRiskGate.ts:50`) mutation 호출. 성공 시 `onSuccess`(`:157`)로 `result` 상태 저장 → `RiskEvaluationSummary`(`:183`) 표시 | `onError` 콜백(`:157`)이 `setAdminActionError` 호출 → 동일 에러 경로(`:358`) | 동일 화면(평가 결과 표시) |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:230`) [handleApprove] | 관리자 페이지 진입. bundleId 입력(필수, `:218–220`), approval_ref 입력(필수, `:225–227`) | `useApproveRuleBundle`(`shared-hooks/src/useRiskGate.ts:56`) mutation 호출. 성공 시 캐시 무효화 없음(`:57–60`) | `onError` 콜백(`:200`)이 `setAdminActionError` 호출 → 동일 에러 경로 | 동일 화면 |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:233`) [handleActivate(ruleBundle)] | 관리자 페이지 진입. bundleId 입력(`:218–220`) | `useActivateRuleBundle`(`shared-hooks/src/useRiskGate.ts:63`) mutation 호출. 성공 시 `onSuccess`(`:207`)로 `bundle` 상태 저장 → `RuleBundleSummary`(`:237`) 표시 | `onError` 콜백(`:209`)이 `setAdminActionError` 호출 → 동일 에러 경로 | 동일 화면(활성화 결과 표시) |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:305`) [handleDeactivate] | 안전 통제 상태가 `ACTIVE`(`:286` — `isActive` 참). 해당 컨트롤 ID | `useDeactivateSafetyControl`(`shared-hooks/src/useRiskGate.ts:19`) mutation 호출. 성공 시 `qc.invalidateQueries(["safetyControls"])`(`:23`)로 목록 갱신. 컨트롤 UI 제거 | `onError` 콜백(`:265`)이 `setActionError`(`:248`) 호출 → `SafetyControlActionError`(`:342–345`) 표시. 동일 에러 라우팅 | 동일 화면(목록에서 해당 컨트롤 제거) |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:331`) [handleEvaluateRecovery] | 안전 통제 상태가 `ACTIVE`. approvalId(숫자, 필수 검증 `:270–273`), evidenceRef(선택, `:326–328`) | `useEvaluateRecovery`(`shared-hooks/src/useRiskGate.ts:27`) mutation 호출. 성공 시 `qc.invalidateQueries(["safetyControls"])`(`:37`) + `onSuccess`(`:279`)로 `recoveryDecisions` 상태 저장 → `RecoveryDecisionSummary`(`:341`) 표시 | validation 실패(approvalId 비숫자): `new Error("승인 요청 ID를 숫자로 입력하세요.")` 직접 발생(`:272`). mutation 실패: `onError`(`:280`)로 `setActionError` 호출 → 동일 에러 경로 | 동일 화면(복구 평가 결과 표시) |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J4-SafetyControlsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당. 본 화면은 안전 통제 개통/해제(상태 변경)와 룰번들 승인/활성화(상태 변경)를 포함 — 위임장 변경·출금은 없음 |
| 금전·위험 행동 목록 | 1) 안전 통제 개통(`SafetyControlsPage.tsx:107-110`, `useRiskGate.ts:42-47`) — `POST /admin/safety-controls/activate`. 2) 안전 통제 해제(`SafetyControlsPage.tsx:265`, `useRiskGate.ts:19-25`) — `POST /safety-controls/:controlId/deactivate`. 3) 룰번들 승인(`SafetyControlsPage.tsx:198-201`, `useRiskGate.ts:56-61`) — `POST /rule-bundles/:bundleId/approve`. 4) 룰번들 활성화(`SafetyControlsPage.tsx:206-209`, `useRiskGate.ts:63-67`) — `POST /rule-bundles/:bundleId/activate`. 평가(Evaluate Risk Gate `:155`, Evaluate Recovery `:276`)는 읽기 전용 판단이며 상태 변경 없음 |
| 확인 절차 | 모든 개통/해제/승인/활성화 mutation은 `onError` 콜백(`SafetyControlsPage.tsx:109`, `:157`, `:200`, `:208`, `:265`)을 통해 `SafetyControlActionError`(`SafetyControlsPage.tsx:34-46`)로 에러 라우팅. `classifyForbidden`(`shared-types/src/forbidden.ts:31`)으로 403 판정 → `ForbiddenNotice`(`:35-36`) 또는 `ErrorMessage`(`:38`). 버튼 `loading` 상태로 `isPending` 표시(`SafetyControlsPage.tsx:138`, `:180`, `:231`, `:234`) |
| 멱등성 | 미구현. 모든 mutation이 `postEnvelope`(일반 POST) 사용 — `httpIdempotent.ts`의 `postEnvelopeIdempotent` 아님. `Idempotency-Key` 헤더 미첨부. 중복 클릭 시 재전송 가능(`handleActivate` `:105-111`, `handleApprove` `:196-202`, `handleDeactivate` `:263-266` 모두 `onError(null)`만 호출하고 기존 요청 취소 없음) |
| 불확실 결과 표시 | 예. 각 버튼에 `loading={activate.isPending}` 등으로 제출 중 상태 표시(`:138`, `:180`, `:231`, `:234`). 평가 결과는 `RiskEvaluationSummary`(`:183`)·`RuleBundleSummary`(`:237`)·`RecoveryDecisionSummary`(`:341`)에서 `<StatusBadge>`로 outcome 표시. 개통/해제/승인 결과는 로컬 상태 저장 없음 — 목록(`useSafetyControls`)이 `invalidateQueries`로 갱신(`:23`, `:46`) |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J4-SafetyControlsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:35`) | 순수 분류기 — 사용자 문구 없음. 403 응답을 "mfa_required"/"tenant_mismatch"/"policy"/"forbidden" 중 하나로 수렴. 문구 생성은 `ForbiddenNotice`가 전담. | — | 아니요. 분류기 자체는 trace_id를 읽지 않음(`frontend/packages/shared-types/src/forbidden.ts:31`) |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:35`) | `getApiErrorMessage()`로 `EXACT_MESSAGES` 매핑 재사용(공통 에러 매핑). MFA 갈래일 때만 `legacy.forbiddenNotice.t1`("step-up 인증" / "Step-up authentication") 버튼 문구. 정책 갈래는 `DenialReasons`가 `describeReasonCode()`로 사유 목록 표시. | mfa_required: "step-up 인증" 버튼 클릭 시 `onStepUp` 호출(이 페이지에서는 prop 전달 안 함 → 버튼 미노출). 나머지 갈래: 메시지만 표시, 재시도 버튼 없음(재시도 시 동일 결과 반복). | 아니요. `ForbiddenNotice`는 trace_id를 렌더링하지 않음(`frontend/apps/web/src/components/ForbiddenNotice.tsx:38-48`) |
| `routeApiError` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:36`) | 순수 라우터 — 사용자 문구 없음. `RoutedApiError` 구조체로 24개 error_code를 갈래별 타입으로 분해. `SafetyControlActionError`에서는 `routed.kind === "backoff_retry"`로 재시도 타이머 여부만 판정. | — | 아니요. 이 호출 행에서는 trace_id를 추출하지 않음(`frontend/packages/shared-types/src/errorRouting.ts:86`). trace_id는 `classifyServerError` 내부에서만 `server_fatal` 갈래에 담김 |
| `ErrorMessage` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:38`) | `getApiErrorMessage(errorCode, message)`로 메시지 결정. 재시도 가능 시: `legacy.errorMessage.t1`("Support code: {{traceId}}"), `legacy.errorMessage.t2`("Retry available in {{remainingSec}}s"), `legacy.errorMessage.t3`("Retry"). | classifyRetry(kind)가 "none" 아닐 때만 재시도 버튼 노출. "backoff"(RATE_LIMIT_EXCEEDED): countdown 후 버튼 활성화. "refetch"(STATE_CONCURRENCY_CONFLICT): 즉시 활성화. 버튼 클릭 시 실제 재시도는 호출부 책임(`frontend/apps/web/src/components/ErrorMessage.tsx:38-40`). | 예. `error instanceof ApiError`일 때 `error.traceId`를 `traceId` prop으로 전달. `traceId`가 truthy이면 "Support code: {traceId}" 표시(`frontend/apps/web/src/components/ErrorMessage.tsx:60`) |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J4-SafetyControlsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J4-SafetyControlsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J4-SafetyControlsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
