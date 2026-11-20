# J3-NotificationCenterPage — `/notifications`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/NotificationCenterPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/notifications` / `frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-NotificationCenterPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더) — 여정 J3(모의 주문/감시) 소속, 알림을 확인해 전략 실행·모의 운용 상태를 모니터링 |
| 목적(한 문장) | 채널별 알림 이력을 날짜별 다이제스트로 확인하고, 발송/실패 상태를 한눈에 감시한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:145` — `/notifications` 라우트, `protect(<NotificationCenterPage />)`으로 `ProtectedRoute` 필요 (로그인 세션 유효) |
| 사용자 준비물 | 로그인 세션, `getNotificationHistory` API 접근 권한 |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-NotificationCenterPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useNotificationHistory` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:45` | `useQuery` 기본 설정 — 1회 조회. `queryKey: ["notificationHistory", eventType]` (`shared-hooks/src/useAccountSettings.ts:47`). 폴링 옵션 미설정(shared-hooks/src/useAccountSettings.ts:46-49), stale/gc 시간 모두 React Query 기본값(0/5분). `refetchOnWindowFocus: undefined`(기본값 true) — 창 포커스 시 자동 재조회. `channel` 필터는 클라이언트 측 `useMemo`로 처리(`apps/web/src/routes/notifications/NotificationCenterPage.tsx:41-44`), 서버 재요청 없음. | `isError` 상태 확인 후 `HistoryError` 컴포넌트 렌더(`apps/web/src/routes/notifications/NotificationCenterPage.tsx:70-71`). `HistoryError`는 `classifyForbidden(error)`로 403 판별 시 `ForbiddenNotice` 표시(`apps/web/src/routes/notifications/NotificationCenterPage.tsx:19`), 그 외 `routeApiError(error)`로 분류: `refetch_retry`/`backoff_retry`면 `ErrorMessage`에 retry 버튼 노출(`apps/web/src/routes/notifications/NotificationCenterPage.tsx:21-28`). `onRetry={() => refetch()}`로 수동 재시도 지원(`apps/web/src/routes/notifications/NotificationCenterPage.tsx:71`). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-NotificationCenterPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `history`가 로드되지 않은 상태(`!history`, `:72-73`) | `LoadingState` 컴포넌트 표시. 별도 문구 없음. | 채널 필터 변경(`:57`) — 필터는 동작함. | 구현됨 (`NotificationCenterPage.tsx:72-73`) |
| 빈 상태 | `history`는 로드됐지만 `digest.length === 0`(`:74`). 채널 필터 결과가 비어도 같은 분기. | "표시할 알림 이력이 없습니다." (`notificationCenter.empty`, `:75`) | 채널 필터 변경(`:57`) — 다른 채널 선택 시 목록 변경 가능. | 구현됨 (`:74-75`) |
| 정상 | `digest.length > 0`(`:76`) | 날짜별 카드: 날짜 제목(`group.date`, 날짜 미상 시 "날짜 미상" `notificationCenter.unknownDate`, `:82`), "{{count}}건" (`notificationCenter.totalCount`, `:85`), 통계 3개(전체 `stat.total` / 발송됨 `stat.sent` / 실패 `stat.failed`, `:89-91`), 항목 목록(eventType, 채널 라벨, `StatusBadge`, `:94-102`) | 채널 필터 변경(`:57`) | 구현됨 (`:76-105`). 채널 옵션 라벨 `notificationCenter.channel.${c}`(`:63`)과 `StatusBadge` 문구는 facts 시트에 없어 미확인. |
| 낡은 데이터(신선도 초과) | `useNotificationHistory`(`frontend/packages/shared-hooks/src/useAccountSettings.ts:46-49`)는 `useQuery` 기본 설정만 사용 — 폴링/스탤 옵션 미설정. `main.tsx:15`에서 `QueryClient`도 옵션 없이 생성. | 없음 | 없음 | 없음 — 폴링·자동 갱신·신선도 표시 모두 미구현. 창 포커스 시 재조회만 기본 동작. |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-NotificationCenterPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `routeApiError(error)`가 `{kind:"field_errors"}` 또는 그 외 400(`badRequest.ts:34`) — `classifyForbidden` null(`forbidden.ts:32`), `classifyStateConflict` null(`stateConflict.ts:34`), `classifyServerError` non-null 아니면 `classifyRetry` non-null 아니면 `isResourceNotFound` true 아니면 `deriveLockout` locked 아니면 `isSessionExpiredErrorCode` false(`errorRouting.ts:96-132`) | `ErrorMessage` 컴포넌트 렌더(`:23-29`). `ErrorMessage.tsx:36`에서 `getApiErrorMessage(errorCode, message)` 호출 — errorCode가 있으면 `apiError.ts:106-116`의 EXACT_MESSAGES/PREFIX_MESSAGES 매핑, 없으면 서버 message 원문(`:117`). 필드별 오류(`field_errors` kind)는 `ErrorMessage`가 `fieldErrors` prop을 받지만 페이지가 전달하지 않음(`:23-28` — `fieldErrors` 인자 없음). | 재시도 버튼: `routed.kind`가 `refetch_retry` 또는 `backoff_retry`일 때만 노출(`:21`). 400 field_errors는 재시도 대상 아님. | 없음(필드별 오류 표시). 페이지가 `ErrorMessage`에 `message`(`:25`)만 전달하고 `fieldErrors` prop을 전달하지 않음(`:23-28`). 사용자가 보는 것: `getApiErrorMessage`의 매핑 문구 — `미확인`(문구 키 확인 불가: `getApiErrorMessage`가 코드 매핑 사용). |
| 인증 필요/세션 만료(401) | `classifyForbidden(error)`가 null(`forbidden.ts:32`: statusCode !== 403). `routeApiError`로 넘어감(`:20`). 401 전용 분기(`errorRouting.ts:96-132`) 확인: `classifyForbidden`(null) → `classifyStateConflict`(null, statusCode !== 409) → `classifyServerError`(502/503/500 전용, 401 아님) → `classifyRetry`(RATE_LIMIT 전용) → `isResourceNotFound`(404 전용) → `deriveLockout`(423 전용) → `isSessionExpiredErrorCode`( errorCode.startsWith("AUTH_") && !== "AUTH_TENANT_MISMATCH", `apiError.ts:125`) — 401 errorCode가 AUTH_ 접두이면 `auth_required` 반환. 전용 재시도 분기 없음. | `ErrorMessage` 렌더(`:23-29`). errorCode가 AUTH_ 계열이면 `apiError.ts`의 `EXACT_MESSAGES` 또는 `PREFIX_MESSAGES["AUTH_", ...]` 매핑. 재시도 버튼: `routed.kind`가 `refetch_retry`/`backoff_retry`일 때만(`:21`). 401 `auth_required`는 재시도 대상 아님(`errorRouting.ts:129`). | 없음(401 전용 재시도 버튼 없음). `ErrorMessage`에 retry 핸들러 전달 안 됨(`:21` 조건 불만족). | 구현됨(`:23-29`). 사용자가 보는 것: `ErrorMessage` 배너 + `getApiErrorMessage` 문구 — `미확인`(문구 키 확인 불가). 재시도: 없음. 근거: `errorRouting.ts:129`(`auth_required` 반환), `:21`(재시도 조건에 포함 안 됨). |
| 권한 없음/MFA 필요(403) | `classifyForbidden(error)`가 non-null(`forbidden.ts:31-40`: statusCode 403이면 항상 "mfa_required"/"tenant_mismatch"/"policy"/"forbidden" 중 하나 반환). 페이지는 하위 종류를 나누지 않고 모두 `ForbiddenNotice`로 처리(`:19`). | `ForbiddenNotice` 컴포넌트 렌더(`:19`). `ForbiddenNotice.tsx:36`에서 `getApiErrorMessage(extractErrorCode(error), extractMessage(error))` 호출 — `apiError.ts:106-116` 매핑. MFA 갈래(`mfa_required`)일 때만 "Step up" 버튼 노출(`ForbiddenNotice.tsx:42-46`, 키 `legacy.forbiddenNotice.t1`). 나머지 갈래는 버튼 없음. | MFA 갈래: `onStepUp` 버튼 클릭 시 MFA 인증 단계 유도(페이지 외부 동작). 나머지 갈래: 재시도 버튼 없음(같은 결과 반복). | 구현됨(`:19`). 사용자가 보는 것: `ForbiddenNotice` + `getApiErrorMessage` 문구 — `미확인`(문구 키 확인 불가: `getApiErrorMessage`가 EXACT_MESSAGES/PREFIX_MESSAGES 매핑). MFA 버튼 키: `legacy.forbiddenNotice.t1`(`ForbiddenNotice.tsx:45`). 근거: `:19`, `forbidden.ts:31-40`, `ForbiddenNotice.tsx:31-48`. |
| 충돌/상태 불일치(409) | `classifyForbidden` null(`forbidden.ts:32`: statusCode !== 403). `errorRouting.ts:104-109`에서 `DATA_COVERAGE_MISSING`/`WALLET_BALANCE_DRIFT` 체크 후, `classifyStateConflict(err)`(`stateConflict.ts:33-39`). `STATE_CONCURRENCY_CONFLICT` → `"refetch_retry"`, 그 외 409 → `"invalid_transition"`. | `ErrorMessage` 렌더(`:23-29`). `routed.kind`가 `refetch_retry`이면 재시도 버튼 노출(`:21`). `invalid_transition`이면 재시도 버튼 없음. | `refetch_retry`(STATE_CONCURRENCY_CONFLICT): 재시도 버튼 클릭 시 `refetch()`(`:71`). `invalid_transition`: 재시도 무의미, 버튼 없음. | 구현됨(`:21,71`). 사용자가 보는 것: `ErrorMessage` + 재시도 버튼(if refetch_retry) — 문구 키 `미확인`. 재시도: `refetch_retry`일 때만. 근거: `:21`(재시도 조건), `:71`(`onRetry={() => refetch()}`), `stateConflict.ts:37`(`refetch_retry` 반환), `errorRouting.ts:112`. |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-NotificationCenterPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 423: `deriveLockout(error)` locked=true(`accountLockout.ts:41-49`) — statusCode=423 && errorCode=AUTH_ACCOUNT_LOCKED. `routeApiError`에서 `classifyRetry` 이후(`errorRouting.ts:125-126`). 429: `classifyRetry(error)`가 `kind: "backoff"`(`retryable.ts:45-49`) — errorCode=RATE_LIMIT_EXCEEDED. `routeApiError`에서 `classifyServerError` 이후(`errorRouting.ts:119-121`). | 423: `ErrorMessage` 렌더(`:23-28`). `getApiErrorMessage("AUTH_ACCOUNT_LOCKED", message)` → `apiError.ts:64`의 "계정이 잠겼습니다. 잠시 후 다시 시도해주세요." 429: `ErrorMessage` 렌더. `apiError.ts:78`의 "요청이 너무 많습니다. 잠시 후 다시 시도해주세요." | 423: `classifyRetry` → `kind: "none"` → 재시도 버튼 안 나옴. 429: `classifyRetry` → `kind: "backoff"`, `afterSec` = 서버 값. ErrorMessage가 `retryAfterSec` 받아서 카운트다운(`ErrorMessage.tsx:41-53`). 종료 시 retry 버튼 활성화(`:66`). | 423: 부분 구현 — ErrorMessage가 일반 오류 문구로 표시(`:20-28`), 잠금 전용 UI(카운트다운, retryAfterSec 표시) 없음. `routeApiError`가 `{kind: "account_locked", retryAfterSec}`를 반환하지만 오류 처리 함수(`:18-31`)가 특별 처리 안 함(`:20-28`). 429: 구현됨 (`:20-28`, `ErrorMessage.tsx:41-67`). |
| 서버 오류(5xx) | `classifyServerError(error)`(`serverError.ts:39-52`). retryable(503): EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY → `kind: "retryable"` → `backoff_retry`(`errorRouting.ts:115-116`). fatal(502/500): EXCHANGE_FATAL/INTERNAL_ERROR → `kind: "fatal"` → `server_fatal`(`errorRouting.ts:117`). | retryable: `apiError.ts:79`("거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요.") / `apiError.ts:81`("서비스가 준비 중입니다. 잠시 후 다시 시도해주세요."). fatal: `apiError.ts:80`("거래소 자격증명를 확인해주세요.") / `apiError.ts:83`("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."). | retryable: ErrorMessage 카운트다운 + retry 버튼. fatal: ErrorMessage traceId 노출(`:26`), 재시도 버튼 없음(`kind: "none"`). | 구현됨. retryable: `serverError.ts:42-44`, `ErrorMessage.tsx:41-67`. fatal: `serverError.ts:47-48`, traceId 전달(`:26`). |
| 네트워크 단절 | fetch 실패, DNS 실패, 연결 끊김 — ApiError가 아닌 DOMException/FetchError. `classifyForbidden(non-ApiError)` → null(`forbidden.ts:32`). `routeApiError`에서 `isErrorCodeLike` false → `classifyServerError`/`classifyRetry`/`isResourceNotFound`/`deriveLockout` 모두 false → `{kind: "unknown"}`(`errorRouting.ts:132`). | `ErrorMessage` 렌더. errorCode undefined → `getApiErrorMessage(undefined, message)` → message 있으면 message, 없으면 `DEFAULT_API_ERROR_MESSAGE`("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.")(`apiError.ts:117`). | 재시도 버튼 없음(`kind: "unknown"` → `classifyRetry` → "none"). | 부분 구현 — ErrorMessage가 기본 문구로 표시(`:20-28`), 네트워크 전용 문구/아이콘 없음. |
| 부분 실패(일부 데이터만 실패) | 날짜별 그룹화(`digest.ts:42-43`) 내 일부 항목의 `status`가 "FAILED". `group.failedCount > 0 && group.sentCount > 0`. | 날짜별 카드에서 `stat.failed` 통계(`:91`), `StatusBadge`(`StatusBadge.tsx:60`)로 각 항목 상태 표시(`:99`). 실패 항목은 빨간색 뱃지. | 필터 변경으로 실패 항목만 확인 가능. 개별 재시도 없음(발송은 백엔드 역할). | 구현됨 (`:89-91`, `:99`). digest.ts:42-43에서 SENT/FAILED 구분 집계. |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-NotificationCenterPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| 행 A: 채널 `<Select>` | `CHANNEL_FILTERS`(:12)가 `"ALL","EMAIL","PUSH","IN_APP"` — 네트워크 호출 없이 즉시 렌더. Select는 조건부 분기(:70-107) 바깥(:52-68)에 있어 로딩·에러 상태에서도 항상 보인다. | `onChange`(:57)가 `setChannel`을 호출. `filtered`(:41-44)가 `channel` 값으로 재계산(`useMemo`), `digest`(:45)도 재계산되어 목록이 바뀐다. | 네트워크 호출이 없으므로 실패 경로 없음. 에러 상태(`:70`)에서는 `HistoryError`가 렌더되어 Select 아래에 에러 메시지가 보인다. | 없음(같은 페이지 안에서 필터링). |
| 행 B: 재시도 버튼(`ErrorMessage` 내부) | `isError`(`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:70`)이고 `routed.kind`가 `refetch_retry` 또는 `backoff_retry`(`shared-types/src/errorRouting.ts:1-50` 참조, `HistoryError`(`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:18-31`). `ErrorMessage.tsx:61-66`에서 `canRetry && onRetry`가 true, `remainingSec`이 0일 때 버튼 활성. 429는 `retryAfterSec`이 0이 되면 활성. | `onRetry={() => refetch()}`(`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:71`)가 `useNotificationHistory`(`shared-hooks/src/useAccountSettings.ts:45-50`)의 `refetch()`를 호출. 성공하면 `isError`가 false가 되어 정상(`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:76-107`) 또는 빈 상태(`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:74-75`)가 렌더된다. | 재조회가 실패하면 `isError`가 true로 남아 `HistoryError`가 다시 렌더된다(`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:70-71`). 버튼은 같은 조건으로 재평가된다. | 없음. |
| 행 C: ForbiddenNotice의 'Step up' 버튼 | 403이어서 `classifyForbidden`이 non-null(:19). | 이 페이지는 `<ForbiddenNotice error={error} />`으로 `onStepUp`을 넘기지 않는다(:19). `ForbiddenNotice.tsx:42`의 `onStepUp &&` 조건이 false라 'Step up' 버튼이 렌더되지 않는다. 행동 없음. | — | 없음. st-b 행의 'Step up' 주장은 이 페이지 코드와 불일치 — `ForbiddenNotice`가 `onStepUp` prop을 받지만 호출부가 전달하지 않아 버튼이 표시되지 않는다. |
| 행 D(간접): 창 포커스 복귀 자동 재조회 | `useNotificationHistory`(`shared-hooks/src/useAccountSettings.ts:45-50`)는 `useQuery` 기본 설정만 사용 — refetchOnWindowFocus 명시 없음. 전역 `QueryClient`는 `main.tsx:15`에서 옵션 없이 생성되어 기본값 true 적용. | 창 포커스 시 useQuery 기본 refetchOnWindowFocus: true로 자동 재조회. | — | 없음. |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-NotificationCenterPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-NotificationCenterPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:19`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:19`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:20`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/notifications/NotificationCenterPage.tsx:23`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-NotificationCenterPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-NotificationCenterPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-NotificationCenterPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
