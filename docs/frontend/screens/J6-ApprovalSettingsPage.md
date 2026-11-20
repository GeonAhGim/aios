# J6-ApprovalSettingsPage — `/settings/approval`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ApprovalSettingsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/settings/approval` / `frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-ApprovalSettingsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 — 개인 트레이더(투자자). 여정 J6("설정·승인·감사")의 주체. 승인 방식(SOLO/DUAL)을 직접 설정·변경한다. [확인: PERSONAS_AND_JOURNEYS.md §3 표 — J6는 P1(+P2) 중심] |
| 목적(한 문장) | 거래 실행의 승인 방식을 SOLO(1인 승인, 강제 대기 60초) 또는 DUAL(서로 다른 두 계정 순차 서명) 중 하나로 설정하고, DUAL 모드일 때 2차 승인자 연락처를 등록한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:148` — `/settings/approval` 라우트는 `protect(<ApprovalSettingsPage />)`로 보호됨. `protect()`는 `ProtectedRoute` 컴포넌트(`frontend/apps/web/src/components/ProtectedRoute.tsx`)를 통과해야 함: (1) 로그인 세션 보유(`router.tsx:70-72`), (2) MFA 활성화(`ProtectedRoute.tsx:32-34`), (3) 위험도 평가 완료(`ProtectedRoute.tsx:35-41`). FeatureFlagGate 미적용(`router.tsx:93-111` 중 없음). |
| 사용자 준비물 | 로그인 세션, MFA 설정 완료, 위험도 평가 완료. DUAL 모드 선택 시 2차 승인자의 연락처 정보(이메일 또는 전화번호). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-ApprovalSettingsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useApprovalSettings` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:9` | 마운트 시 1회 조회(`useQuery`, `useAccountSettings.ts:10-13`). refetchInterval 없음(`useAccountSettings.ts:10-13` grep 결과 0건). 이후 재조회는 QueryClient 기본값(`staleTime: 0`, `refetchOnWindowFocus: false`)에 의존하므로 미확인. | 페이지는 `data`와 `isLoading`만 구조분해(`ApprovalSettingsPage.tsx:46`). 훅의 `error`는 화면에 렌더링되지 않음(`:97-98`에서 로딩만 처리). `settings`가 `undefined`이면 조건부 렌더링(`:128`)이 false가 되어 폼 기본값(SOLO)으로 그대로 보임. |
| `useUpdateApprovalSettings` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:16` | mutation(`useMutation`, `useAccountSettings.ts:18`). 성공 시 `onSuccess`에서 `["approvalSettings"]` 무효화(`useAccountSettings.ts:20`). HTTP 메서드는 `apiClient.updateApprovalSettings`를 열어 보기 전까지 미확인. | `attemptUpdate` catch(`ApprovalSettingsPage.tsx:62-86`) 처리. `ApiError` + 위험등급 문구(`t8`) 포함 시 `RiskWarningModal`(`:77-80`). 그 외 `ApiError`는 `setError(err)` → `UpdateSettingsError`(`:30-42`)가 400/403/그 외 분기(`:132`). `ApiError` 아님 → `t9` 메시지(`:83-85`). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-ApprovalSettingsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `isLoading`이 true인 경우 (`useApprovalSettings` 훅이 아직 데이터를 받지 않음). `ApprovalSettingsPage.tsx:46` 구조분해, `:97` 조건부 렌더링. | 헤더(`title1`)와 `TenantSwitcher`는 표시되고, 폼 영역은 `<LoadingState />`로 대체됨(`:98`). `LoadingState`는 회색 스피너 + "불러오는 중..." 문구(`frontend/packages/ui-web/src/Feedback.tsx:31-37`). 저장 버튼은 로딩 중 숨김. | 없음(데이터 수신 대기). | 구현됨(`:97-98`) |
| 빈 상태 | 로딩이 끝났지만 `settings`가 `undefined`인 경우(조회 실패 또는 빈 응답). `ApprovalSettingsPage.tsx:46`에서 `data`가 `undefined`. | 전용 빈 상태 UI나 문구 없음. `settings`가 truthy하지 않아 `:128`의 `{settings && ...}` 줄이 숨겨지고(`t6` 미표시), 폼은 SOLO 기본값으로 그대로 표시됨. | 없음(에러 리트리 또는 페이지 재방문만 가능). | 없음(기본값 노출 — `:48`의 `useState<"SOLO" \| "DUAL">("SOLO")`, `:128` 조건부 렌더링이 `undefined`일 때 폼을 그대로 둠). |
| 정상 | `isLoading`이 false이고 `settings`가 존재하는 경우. `ApprovalSettingsPage.tsx:46`. | (1) 헤더에 `title1`("승인 방식 설정") 표시(`:92`). (2) `TenantSwitcher` 표시(`:93`). (3) `canTrade`가 false일 때 `t2` 경고(`:94-96`). (4) 라디오 버튼: `t3`(SOLO, `:109`), `t4`(DUAL, `:117`). (5) DUAL 선택 시 `label5` 필드 + `Input`(`:119-127`). (6) `t6`에 `mandatoryWaitSeconds` 값 삽입(`:129-130`). (7) 저장 버튼 `t7`(`:133-139`), `canTrade`로 비활성화. (8) `useEffect(:58-60)`가 `settings.mode`를 `mode` 상태에 반영. | (1) SOLO/DUAL 라디오 선택 — `setMode`(`:104`, `:114`). (2) DUAL일 때 `label5` 입력 — `setSecondApproverContact`(`:124`). (3) 저장 버튼 클릭 — `attemptUpdate(false)`(`:135`), `canTrade`가 false면 비활성화(`:137`). (4) 위험등급 경고 시 `RiskWarningModal` — `attemptUpdate(true)`(`:148`). | 구현됨(`:92-139`) |
| 낡은 데이터(신선도 초과) | 캐시된 `settings`가 stale인 경우. 재조회 정책: `useApprovalSettings` 훅에 주기 재조회/만료 시간 옵션 없음(`frontend/packages/shared-hooks/src/useAccountSettings.ts:10-13`). `QueryClient`는 `new QueryClient()`로 기본값만 사용(`frontend/apps/web/src/main.tsx:15`). | 화면에 갱신 시각이나 신선도 표시 없음. 재조회 시 `ApprovalSettingsPage.tsx:58-60`의 `useEffect`가 `settings`를 `mode` 상태에 덮어쓰므로(`:59`), 사용자가 고른 미저장 라디오 값이 서버 값으로 리셋될 수 있음. | 윈도우 포커스 시 재조회 — 기본값 false이므로 발생 안 함. 수동 갱신 버튼 없음. | 없음(신선도 표시 없음). 갭: `:58-60`의 `useEffect`가 재조회 시 미저장 값을 덮어씀(`:59`). 재조회 정책: 기본값 즉시 stale, 포커스 시 재조회 기본 false → 발생 안 함. |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-ApprovalSettingsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-ApprovalSettingsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | `update.mutateAsync`가 429 응답을 받음. API 클라이언트의 `buildApiError`(api-client/src/httpErrors.ts:110)가 `errorCode: "RATE_LIMIT_EXCEEDED"`, `retryAfterSec` 채운 `ApiError`를 던짐. `ApprovalSettingsPage.tsx:71` catch에서 `err instanceof ApiError` true → `setError(err)`(`:82`). `UpdateSettingsError`(`:132`)가 `classifyBadRequest`/`classifyForbidden`/`routeApiError`로 분기. `routeApiError`는 `ErrorMessage`를 렌더링. | `ErrorMessage` 컴포넌트(`api-client/src/clients/envelope.ts:59`의 `retry_after_seconds` 또는 HTTP `Retry-After` 헤더에서 `retryAfterSec` 추출)가 `Alert tone="danger"` 배너에 `getApiErrorMessage("RATE_LIMIT_EXCEEDED", message)`로 한국어 문구 표시. `classifyRetry`가 `backoff`이므로 `remainingSec` 카운트다운(`httpErrors.ts:49-53`의 `useEffect`). 재시도 버튼(`:66`)이 `remainingSec > 0` 동안 비활성화. | 카운트다운 후 재시도 버튼 클릭 — **그러나** `UpdateSettingsError`(`ApprovalSettingsPage.tsx:30-42`)는 재시도 콜백 prop을 받지 않으므로 재시도 버튼이 렌더링되지 않음(`ErrorMessage.tsx:61`: `canRetry && onRetry`). 수동 저장 버튼 재클릭만 가능. | 구현 불완전 — `ErrorMessage`는 retry 배선을 받지만 `UpdateSettingsError`가 재시도 콜백을 전달하지 않음(`:30-42`). 429 시 사용자는 버튼 재클릭으로만 재시도 가능. 423(LockWaitTimeout)은 현재 API 계약에 없음(미확인). |
| 서버 오류(5xx) | `update.mutateAsync`가 500/502/503 응답을 받음. `buildApiError`가 `ApiError(statusCode: 5xx)` 생성. `classifyServerError`(shared-types/src/serverError.ts:39) — 503(`EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY`)는 `kind: "retryable"`, 500/502는 `kind: "fatal"`. `ApprovalSettingsPage.tsx:71-82`에서 `setError(err)`. | `ErrorMessage` 배너에 `getApiErrorMessage(errorCode, message)`로 한국어 문구 표시. 503인 경우 `classifyRetry`가 `backoff`(afterSec: 1 또는 2초)이므로 카운트다운 재시도 버튼 표시. 500/502는 `kind: "none"` — 재시도 버튼 없음. | 버튼 재클릭으로 수동 재시도 가능. **그러나** `UpdateSettingsError`가 재시도 콜백 prop을 받지 않으므로 `ErrorMessage` 내 재시도 버튼은 렌더되지 않음. | 구현 불완전 — 에러 분류와 `ErrorMessage`의 retry UI는 존재하지만 `UpdateSettingsError`가 재시도 콜백을 전달하지 않음. 503의 자동 재시도(1→2초 백오프)는 api-client의 `withGetRetry`(httpErrors.ts:70-82)가 GET에만 적용되므로 PUT에는 작동하지 않음. |
| 네트워크 단절 | HTTP 네트워크 레이어가 `TypeError("Network request failed")` 또는 유사 네트워크 에러를 던짐. `ApprovalSettingsPage.tsx:71` catch에서 `err instanceof ApiError` → false → `:83-85`로 감. | `new Error(t("legacy.approvalSettingsPage.t9"))` 생성 → `setError`(`:84`). `UpdateSettingsError`(`:30-42`)에서 `classifyBadRequest`/`classifyForbidden` 모두 false, `routeApiError`(`:33`)가 `kind: "generic"` 반환 → `ErrorMessage` 렌더(`:34-40`). `ErrorMessage`는 `getApiErrorMessage(undefined, "설정 변경에 실패했습니다.")`로 fallback. | 저장 버튼 재클릭으로 수동 재시도. | 구현됨(`:83-85`, `:132-133`). 네트워크 복구 후 저장 버튼 재클릭으로 재시도 가능. trace_id 없음(네트워크 에러에는 trace_id 불가). |
| 부분 실패(일부 데이터만 실패) | `useApprovalSettings`(GET)는 성공해 `settings`를 받지만, `useUpdateApprovalSettings`(PUT)는 실패. 또는 그 반대. `ApprovalSettingsPage.tsx:46`의 `settings`는 stale 값을 유지. | 페이지는 로드된 상태로 표시됨: 헤더(`title1`), `TenantSwitcher`, 폼(SOLO/DUAL 라디오, `t3`/`t4`), `settings && t6`(대기시간 표시). 실패한 업데이트는 `UpdateSettingsError` 배너로 표시. stale `settings` 데이터가 폼에 표시되므로 사용자가 최신 설정과 다른 값을 볼 수 있음. | 저장 버튼 재클릭으로 재시도. stale 데이터刷新을 위한 명시적 "다시 불러오기" 버튼 없음(`useApprovalSettings`에 refetch 옵션 없음: `shared-hooks/src/useAccountSettings.ts:10-13`). | 부분 구현 — 실패 시 에러 배너는 표시되지만(st-a의 정상 상태 행 참조), stale 데이터刷新 메커니즘이 없음. `useUpdateApprovalSettings`의 `onSuccess`(useAccountSettings.ts:20)가 `["approvalSettings"]` 무효화하므로 QueryClient 캐시가 자동으로 갱신되지만, refetch policy가 `staleTime: 0`이므로 다음 마운트/포커스 시에만 재조회(미확인). |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-ApprovalSettingsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:133`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-ApprovalSettingsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-ApprovalSettingsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:4`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:5`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:7`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:31`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:32`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:35`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:95`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-ApprovalSettingsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-ApprovalSettingsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-ApprovalSettingsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
