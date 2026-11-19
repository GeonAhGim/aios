# J3-AlertsPage — `/alerts`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/AlertsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/alerts` / `frontend/apps/web/src/routes/alerts/AlertsPage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-AlertsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:22`: "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독" |
| 목적(한 문장) | 가격/지표 알림을 생성·관리하고, 발동 상태를 확인하여 모니터링 조건을 자동화한다 |
| 진입 조건 | `frontend/apps/web/src/router.tsx:144` — `/alerts` 경로는 `protect(<AlertsPage />)`로 ProtectedRoute 감싸져 있음(인증 필요) |
| 사용자 준비물 | 로그인 상태, 최소 1개 거래소 연동(bitget/kis), 모니터링할 종목/심볼 |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-AlertsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useMyAlerts` | `frontend/packages/shared-hooks/src/useAlerts.ts:5` | 1회 조회(`useQuery`에 폴링 설정 없음, `frontend/packages/shared-hooks/src/useAlerts.ts:6-9`). `useCreateAlert.onSuccess`/`useCancelAlert.onSuccess`에서 `qc.invalidateQueries({ queryKey: ["myAlerts"] })`(`frontend/packages/shared-hooks/src/useAlerts.ts:16`, `frontend/packages/shared-hooks/src/useAlerts.ts:24`)로 무효화 후 자동 재조회 | react-query 기본: 로딩/에러 상태 설정. AlertsPage:77에서 `isLoading` 분기, `LoadingState` 표시(`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:132`) |
| `useIndicators` | `frontend/packages/shared-hooks/src/useStrategyBuilder.ts:10` | 1회 조회(`useQuery`에 폴링 설정 없음, `frontend/packages/shared-hooks/src/useStrategyBuilder.ts:11`) | `indicatorList?.indicators ?? ["RSI", "SMA", "EMA"]`로 기본값 폴백(`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:104`) |
| `useCreateAlert` | `frontend/packages/shared-hooks/src/useAlerts.ts:12` | 버튼 클릭(`AlertsPage.tsx:222`) 시 1회 POST(`apiClient.createAlert`, `frontend/packages/shared-hooks/src/useAlerts.ts:15`). 성공 시 `myAlerts` 쿼리 무효화(`frontend/packages/shared-hooks/src/useAlerts.ts:16`) | `setError(err)`(`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:127`) → `CreateAlertError` 컴포넌트가 `classifyBadRequest`(`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:35`) → `BadRequestNotice`, `classifyForbidden`(`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:36`) → `ForbiddenNotice`, 그 외 `routeApiError`(`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:37`) → `ErrorMessage`(`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:39`)로 라우팅 |
| `useCancelAlert` | `frontend/packages/shared-hooks/src/useAlerts.ts:20` | 버튼 클릭(`AlertsPage.tsx:263`) 시 1회 PATCH(`apiClient.cancelAlert`, `frontend/packages/shared-hooks/src/useAlerts.ts:23`). 성공 시 `myAlerts` 쿼리 무효화(`frontend/packages/shared-hooks/src/useAlerts.ts:24`) | mutation error 상태가 설정되나 AlertsPage에서는 `loading={cancelAlert.isPending}`(`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:267`) 외 별도 오류 표시 없음. 실패 시 버튼이 비활성화되지 않고 원래 상태로 복귀 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-AlertsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `isLoading` true (`AlertsPage.tsx:77`, `:231`) | `<LoadingState />` — 회전 스피너 + "불러오는 중..." (`Feedback.tsx:31-38`) | 없음(대기) | `구현됨` (`AlertsPage.tsx:231-232`, `Feedback.tsx:31-38`) |
| 빈 상태 | `alerts`가 `undefined` 또는 빈 배열 (`AlertsPage.tsx:233` 조건 `alerts && alerts.length > 0`) | "등록된 알림이 없습니다." (`:278`, `legacy.alertsPage.t17`) | 위 폼으로 알림 등록 (`:227`, `t14`) | `구현됨` (`AlertsPage.tsx:277-278`). **갭**: `useMyAlerts`의 `error`를 읽지 않음(`:77` 구조분해 시 `error` 미포함). 목록 조회 실패 시에도 같은 빈 문구를 보여줘서 조회 오류와 빈 목록을 구분하지 않음. |
| 정상 | `alerts.length > 0` (`AlertsPage.tsx:233`) | 행마다 `{symbol} · {indicator} {operator} {threshold}` (`:244`), 거래소/타임프레임/생성 시각 (`:250-252`), 상태 배지(`STATUS_LABEL`: "감시 중"/"발동됨"/"취소됨", `:69-73`). `TRIGGERED`이면 "발동값 {{triggeredValue}} (" + 발동 시각 (`:254-260`, `t15`). | `ACTIVE`일 때 "취소" 버튼 (`:262-271`, `t16`), 페이지 이동 (`:275`, `goToPage` `:98-102`), 새 알림 등록(폼). | `구현됨` (`AlertsPage.tsx:233-276`). 시트 미등재 문구(`STATUS_LABEL` 값, 괄호 닫기 `)` `:258`)는 코드에 하드코딩. |
| 낡은 데이터(신선도 초과) | 마지막 조회 이후 경과 시간이 임계치를 넘음 | 없음(낡음을 알리는 문구 없음) | 알림 생성/취소 후 `myAlerts` 자동 무효화(`useAlerts.ts:16`, `:24`)로 재조회되나 신선도 임계값/경과 시간 표시 없음. | `없음`. `useAlerts.ts:5-9`에 폴링 옵션 및 데이터 신선도 제어 설정 없음. `QueryClient` 기본 설정은 `frontend/apps/web/src` 검색으로 확인 불가(미확인). |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-AlertsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 서버가 400 반환(`AlertsPage.tsx:127` catch), `statusCode: 400` (`shared-types/badRequest.ts:35`). 클라이언트 검증: `symbol` `required`(`:155`), `period`/`threshold` `type="number"`(`:177`, `:213`), `threshold` `Number()` 변환(`:125`). 서버 400은 `classifyBadRequest`(`:35`) → `BadRequestNotice`(`:35`) 경로. | `BadRequestNotice`(`BadRequestNotice.tsx:40`)가 `classifyBadRequest`(`shared-types/badRequest.ts:34-43`) 판정 결과로 갈림: `VALIDATION_INVALID_FIELD` → `"field"`(필드맵 비어 있으면 `getApiErrorMessage`(`shared-types/apiError.ts:80`) 문구 배너 표시, `BadRequestNotice.tsx:54`). `VALIDATION_IDEMPOTENCY_KEY_REQUIRED` → `"idempotency_key_required"`(재시도 불가, 버튼 없음). `AUTH_MFA_INVALID` → `"mfa_invalid"`(`badRequest.ts:41`, `BadRequestNotice.tsx:73-77` 재입력 버튼 `legacy.badRequestNotice.t2`). 그 외 `"unknown"`(배너 표시). | 필드 오류: 다시 입력. `disclosure_retired`: "최신 데이터로 새로고침" 버튼(`BadRequestNotice.tsx:67-71`, `legacy.badRequestNotice.t1`). `mfa_invalid`: "MFA 코드 입력으로 이동" 버튼(`BadRequestNotice.tsx:73-77`). 나머지: 재시도 불가(같은 오류 반복). | `구현됨` (`AlertsPage.tsx:35`, `BadRequestNotice.tsx:40-81`). `classifyBadRequest` 판정 조건: `statusCode === 400`(`badRequest.ts:35`). 클라이언트 검증: HTML5 `required`/`type="number"`만, 추가 JS 검증 없음(`AlertsPage.tsx:155`, `:177`, `:213`). |
| 인증 필요/세션 만료(401) | 서버가 401 반환. `CreateAlertError`(`:34-46`)에 401 전용 분기 없음. `classifyBadRequest`(400만, `badRequest.ts:35`), `classifyForbidden`(403만, `forbidden.ts:32`) 모두 통과. `routeApiError`(`:37`)에서 `isSessionExpiredErrorCode`(`shared-types/apiError.ts:124-126`: `errorCode.startsWith("AUTH_") && errorCode !== "AUTH_TENANT_MISMATCH"`) → `{ kind: "auth_required" }`(`errorRouting.ts:128-129`). 전역 처리: `http.ts:103-115` `handleAuthFailure` — `AUTH_TOKEN_EXPIRED`는 `refreshAccessToken`(`auth.ts`) 재시도, 실패 시 `notifyUnauthorized`(`httpHeaders.ts:71-75`) → `unauthorizedHandler` 콜백으로 로그인 리다이렉트. `AUTH_TOKEN_INVALID`/`AUTH_SESSION_REVOKED`는 refresh 없이 바로 `notifyUnauthorized`. | `ErrorMessage`(`:37-44`)가 렌더링: `errorCode`/`message`/`traceId` 전달(`:40-43`). `classifyRetry`(`shared-types/retryable.ts`)가 `"none"`(401은 재시도 아님) → 재시도 버튼 없음(`ErrorMessage.tsx:39`). `getApiErrorMessage`(`shared-types/apiError.ts:80`)로 오류 문구 표시. 전역 `notifyUnauthorized`(`httpHeaders.ts:71-75`)가 로그인 리다이렉트 처리(화면 내 별도 안내 없음). | 로그인 리다이렉트(전역 `http.ts` 처리). 화면 내 재시도 버튼 없음(`ErrorMessage.tsx:39`: `canRetry` false). | `구현됨`(전역). `ErrorMessage`는 화면에 표시(`:37-44`). `isSessionExpiredErrorCode` 판정: `errorCode.startsWith("AUTH_") && errorCode !== "AUTH_TENANT_MISMATCH"`(`apiError.ts:124-126`). 전역 401 처리: `http.ts:103-115` `handleAuthFailure` — `AUTH_TOKEN_EXPIRED`만 refresh 재시도(`http.ts:106-108`), 나머지는 `notifyUnauthorized`(`http.ts:111-112`) → 로그인 리다이렉트(`httpHeaders.ts:71-75`). 화면에 401 전용 분기 없음(`AlertsPage.tsx:34-46`). |
| 권한 없음/MFA 필요(403) | 서버가 403 반환. `CreateAlertError`(`:36`)에서 `classifyForbidden`(`:36`) → `ForbiddenNotice`(`:36`) 경로. | `ForbiddenNotice`(`ForbiddenNotice.tsx:31-50`)가 `classifyForbidden`(`forbidden.ts:31-41`) 판정으로 갈림: `AUTH_MFA_REQUIRED` → `"mfa_required"`(`forbidden.ts:35`, `ForbiddenNotice.tsx:42-46` "Step-up" 버튼 `legacy.forbiddenNotice.t1`). `AUTH_TENANT_MISMATCH` → `"tenant_mismatch"`(`forbidden.ts:36`, 배너 문구만). `POLICY_*/RISK_*` → `"policy"`(`forbidden.ts:37-38`, `DenialReasons` 컴포넌트(`ForbiddenNotice.tsx:41`)로 상세 사유 표시). 그 외 403 → `"forbidden"`(`forbidden.ts:40`, `getApiErrorMessage` 문구 배너). | `mfa_required`: "Step-up 인증" 버튼(`ForbiddenNotice.tsx:44-45`). 나머지: 재시도 불가(같은 결과 반복, 버튼 없음). | `구현됨` (`AlertsPage.tsx:36`, `ForbiddenNotice.tsx:31-50`). `classifyForbidden` 판정 조건: `statusCode === 403`(`forbidden.ts:32`). 갈래: `AUTH_MFA_REQUIRED`(`:35`), `AUTH_TENANT_MISMATCH`(`:36`), `POLICY_*/RISK_*` 접두사(`:37-38`), `"forbidden"` 폴백(`:40`). |
| 충돌/상태 불일치(409) | 서버가 409 반환. `CreateAlertError`(`:34-46`)에 409 전용 분기 없음. `classifyBadRequest`(400만), `classifyForbidden`(403만) 모두 통과. `routeApiError`(`:37`)에서 `classifyStateConflict`(`shared-types/stateConflict.ts`)가 409 처리: `INTEGRITY_WALLET_BALANCE_DRIFT`(`errorRouting.ts:107-109`) → `{ kind: "wallet_balance_drift" }`, `DATA_COVERAGE_MISSING`(`:104-106`) → `{ kind: "data_coverage_missing" }`, 그 외 409 → `"invalid_transition"`(`errorRouting.ts:113`). | `ErrorMessage`(`:37-44`)가 렌더링: `errorCode`/`message`/`traceId` 전달(`:40-43`). `classifyRetry`가 `"none"`(409는 재시도 아님) → 재시도 버튼 없음(`ErrorMessage.tsx:39`). `getApiErrorMessage`로 오류 문구 표시. 409 전용 안내 문구 없음. | 재시도 버튼 없음(`ErrorMessage.tsx:39`). 사용자가 입력 수정 후 재제출(`AlertsPage.tsx:222`). | `ErrorMessage`로 떨어짐(`AlertsPage.tsx:37-44`). 409 전용 UI 없음. `routeApiError`(`errorRouting.ts:111-113`)에서 `classifyStateConflict`가 409 판정. `INTEGRITY_WALLET_BALANCE_DRIFT`(`errorRouting.ts:107-109`), `DATA_COVERAGE_MISSING`(`:104-106`) 별도 갈래, 그 외 `"invalid_transition"`. |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-AlertsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 알림 생성 시 서버가 423(`AUTH_ACCOUNT_LOCKED`) 또는 429(`RATE_LIMIT_EXCEEDED`) 반환 | 429(`RATE_LIMIT_EXCEEDED`)는 `errorRouting.ts:119-121`에서 `backoff_retry`로 분류되어 `retryAfterSec`가 `AlertsPage.tsx:43`에 전달되고, `ErrorMessage`(`frontend/apps/web/src/components/ErrorMessage.tsx:38-43`)가 카운트다운을 보여줌. 카운트 0 시 "다시 시도" 버튼 활성화(`frontend/apps/web/src/components/ErrorMessage.tsx:66`). 423(`AUTH_ACCOUNT_LOCKED`)은 `errorRouting.ts:125-126`에서 `account_locked`로 분류되므로 `routed.kind === "backoff_retry"` 체크(`AlertsPage.tsx:43`)에 걸려 `retryAfterSec`가 전달되지 않음. 423 기본 카운트다운은 60초(`frontend/packages/shared-types/src/accountLockout.ts:14`). | "다시 시도" 버튼 클릭(`frontend/apps/web/src/components/ErrorMessage.tsx:66`, `onRetry` props). 단, `CreateAlertError`(`AlertsPage.tsx:38-44`)가 `onRetry`를 전달하지 않으므로 버튼이 렌더링되지 않음(재시도 수단 없음). 423은 `account_locked` 분류로 `retryAfterSec` 자체도 전달되지 않음. | `구현됨(표시)` — 429 분류와 카운트다운 표시는 `errorRouting.ts:119-121`, `ErrorMessage`(`frontend/apps/web/src/components/ErrorMessage.tsx:38-43`)에서 처리. 423 분류는 `errorRouting.ts:125-126`에서 `account_locked`. **갭**: `CreateAlertError`(`AlertsPage.tsx:38-44`)가 `onRetry`를 전달하지 않아 재시도 불가. 423은 `backoff_retry`가 아니므로 `retryAfterSec` 전달 자체도 누락(`AlertsPage.tsx:43`). 목록 조회(423/429)는 `useMyAlerts.error`를 읽지 않아 미처리(`AlertsPage.tsx:77`). |
| 서버 오류(5xx) | 알림 생성 시 서버가 502/503/500 반환 | `ErrorMessage`(`frontend/apps/web/src/components/ErrorMessage.tsx`)가 `getApiErrorMessage`(`frontend/packages/shared-types/src/apiError.ts:106-118`) 매핑으로 5xx errorCode별 메시지 표시(예: `INTERNAL_ERROR` → "일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.", `apiError.ts:83`). `traceId` 노출(`frontend/apps/web/src/components/ErrorMessage.tsx:60`). 재시도 버튼 없음 — `errorRouting.ts:115-117`에서 5xx는 `classifyServerError`가 `server_fatal`로 분류(`classifyRetry`가 아닌 `classifyServerError`가 5xx를 먼저 처리). | 없음(수동 새로고침만). | `구현됨(생성 경로)` — 5xx는 `errorRouting.ts:115-117`에서 `classifyServerError` → `server_fatal` 분류, `ErrorMessage` 표시. **갭**: 목록 조회(5xx)는 `useMyAlerts.error` 미처리 — 빈 상태로 표시될 뿐. |
| 네트워크 단절 | fetch 실패, timeout, DNS 실패 등 | 알림 생성: `ApiError`가 아닌 일반 `Error` 포착 → `AlertsPage.tsx:128`에서 `new Error(t("legacy.alertsPage.t18"))`(`"알림 생성에 실패했습니다."`)로 감싸서 `setError`. `ErrorMessage`(`frontend/apps/web/src/components/ErrorMessage.tsx`)는 `getApiErrorMessage`(`frontend/packages/shared-types/src/apiError.ts:106-118`)에서 `errorCode`가 없으면 `fallbackMessage`(여기서 `error.message` = `"알림 생성에 실패했습니다."`)를 반환. 네트워크 오류이므로 `traceId` 없음(`AlertsPage.tsx:42`에서 `ApiError` 아님). | 없음(수동 재시도만). | `구현됨(생성 경로)` — 일반 `Error` 객체는 `AlertsPage.tsx:128`에서 `new Error(...)`로 감싸서 `ErrorMessage`에 전달(`AlertsPage.tsx:39-44`). `ErrorMessage`(`frontend/apps/web/src/components/ErrorMessage.tsx:36`)는 `getApiErrorMessage`(`frontend/packages/shared-types/src/apiError.ts:117`)에서 `errorCode` 없이 `fallbackMessage`만 있으면 기본 문구 반환. **갭**: 목록 조회 실패(`useMyAlerts` 네트워크 오류)는 `error` 구조분해 미포함(`AlertsPage.tsx:77`) — 빈 상태 문구("등록된 알림이 없습니다.")와 구분 불가. |
| 부분 실패(일부 데이터만 실패) | 페이지네이션 중 일부 페이지만 실패, 또는 일부 alert 행만 데이터 불완전 | 없음(부분 실패를 알리는 메커니즘 없음). `useMyAlerts`는 단일 조회로 all-or-nothing. 클라이언트 측 페이지네이션(`derivePageState:92`)은 전체 데이터 수신 후 슬라이싱 — 조회 자체는 성공/실패 이분법. | 없음. | `없음` — 아키텍처상 부분 실패 시나리오가 존재하지 않음. 단일 `useQuery` 조회 + 클라이언트 슬라이싱으로 부분 실패가 발생할 수 없음. |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-AlertsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:222`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:263`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-AlertsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-AlertsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:35`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:35`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:36`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:36`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:37`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:39`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-AlertsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-AlertsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-AlertsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
