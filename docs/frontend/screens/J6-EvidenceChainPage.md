# J6-EvidenceChainPage — `/admin/evidence-chain`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/EvidenceChainPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/admin/evidence-chain` / `frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-EvidenceChainPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P8(운영자) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:29` P8: 가입 승인·분쟁·안전장치·정합성 점검. P11(감사자·컴플라이언스)도 읽기 전용 접근. [확인: `:29`(P8), `:32`(P11), `router.tsx:176` admin 라우트] |
| 목적(한 문장) | 증빙 감사 체인의 무결성을 검증(AUD-003)하고, 타임라인 이벤트를 페이지네이션으로 조회한다. [확인: `EvidenceChainPage.tsx:65-68` title/t2, `useEvidence.ts:15,22`] |
| 진입 조건 | `/admin/evidence-chain` 경로에 접근. `router.tsx:176`에서 `protectAdmin(<EvidenceChainPage />)` 가드: `ProtectedRoute`(로그인 필수) + `AdminRoute`(관리자 권한 필수) 이중 적용. [확인: `router.tsx:74-79`, `:176`] |
| 사용자 준비물 | 테넌트 ID (선택사항, 비워두면 system 이벤트만 검증). `placeholder4` 입력 필드. [확인: `EvidenceChainPage.tsx:72-74`, `t3` 설명 문구] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-EvidenceChainPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useCursorPage` | `frontend/apps/web/src/hooks/useCursorPage.ts:25` | 네비게이션 상태 훅(비 네트워크). `meta`(next_cursor)를 props로 받아 `next()`/`prev()`/`reset()` 콜백 제공. `meta`가 null이면 `next()`는 no-op(line 33). `hasNext`는 `meta != null && meta.next_cursor != null`로 결정(line 54). EvidenceChainPage.tsx:42-43에서 `committed` 상태의 `nextCursor`를 `meta`로 전달. | 네트워크 호출 없음. `meta` 누락 시 `hasNext=false`, `next()` no-op. |
| `useAuditTimeline` | `frontend/packages/shared-hooks/src/useEvidence.ts:15` | React Query `useQuery`. `queryKey: ["auditTimeline", params]`(line 17). 폴링 설정 없음 — 마운트 시 1회 조회. `cursor`(pager.cursor) + `limit: 20` 파라미터(line 44). | `timeline.isError` 시 페이지에 별도 렌더 없음 — `timeline.data`가 undefined이므로 `items=[]` → `EmptyState` 표시(EvidenceChainPage.tsx:60, 119). `EvidenceActionError`는 `verify` 훅 전용(line 89). 403 처리가 누락됨. |
| `useVerifyAuditChain` | `frontend/packages/shared-hooks/src/useEvidence.ts:22` | React Query `useMutation`(line 23). `mutationFn: (tenantId) => apiClient.verifyAuditChain(tenantId)`. 사용자 버튼 클릭 시 `verify.mutate(tenantId.trim() || undefined)`로 수동 트리거(EvidenceChainPage.tsx:57). `isPending`으로 버튼 로딩 표시(line 79). | `verify.isError` 시 `EvidenceActionError(verify.error)` 렌더(EvidenceChainPage.tsx:87-91). 403 → `ForbiddenNotice`, 그 외 → `ErrorMessage`(traceId+retryAfterSec). 409 ChainIntegrityError는 페이지에서 재계산하지 않음(주석: line 36-38). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-EvidenceChainPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `timeline.isLoading === true` (초次回수 중, `timeline.data` 없음). `useAuditTimeline` 훅이 마운트 직후 `queryFn` 실행 중(EvidenceChainPage.tsx:44, shared-hooks/src/useEvidence.ts:16-18). | `PageHeader`에 `t("legacy.evidenceChainPage.title1")` 증빙(Evidence) 감사 체인 검증(EvidenceChainPage.tsx:65). 설명 `t("legacy.evidenceChainPage.t2")` 감사 체인 무결성 검증(AUD-003)과 `t("legacy.evidenceChainPage.t3")` 테넌트 ID 설명(EvidenceChainPage.tsx:68-70). 입력 필드 `placeholder4` 테넌트 ID(선택)(EvidenceChainPage.tsx:74). 검증 버튼 `t("legacy.evidenceChainPage.t5")` 체인 검증, `loading={verify.isPending}`(EvidenceChainPage.tsx:79). 타임라인 영역은 `items=[]` → `EmptyState`에 `t("legacy.evidenceChainPage.t9")` 타임라인 이벤트가 없습니다.(EvidenceChainPage.tsx:60, 119). prev/next 버튼 비활성화(`timeline.isFetching`이 true이므로, EvidenceChainPage.tsx:128, 136). | 테넌트 ID 입력 후 `t("legacy.evidenceChainPage.t5")` 버튼 클릭 가능(버튼은 `loading` 표시). 페이지네이션 불가. | 구현됨: 로딩 중에도 전체 UI가 렌더되며, 빈 상태(`EmptyState`)가 대체 표시됨. EvidenceChainPage.tsx:60, 119. 버튼 로딩 인디케이터 `loading={verify.isPending}`(EvidenceChainPage.tsx:79). 타임라인 버튼 비활성화 `disabled={!pager.hasPrev \|\| timeline.isFetching}`(EvidenceChainPage.tsx:128, 136). 전역 로딩 스피너 없음 — 빈 상태로 대체. |
| 빈 상태 | `timeline.data`가 있고 `timeline.data.items.length === 0`. 데이터가 로드되었으나 해당 조건(테넌트 ID 등)에 맞는 타임라인 이벤트가 없음. | `PageHeader`와 설명, 입력 필드, 검증 버튼은 동일하게 표시. 타임라인 영역에 `EmptyState`에 `t("legacy.evidenceChainPage.t9")` 타임라인 이벤트가 없습니다.(EvidenceChainPage.tsx:119). prev/next 버튼 표시되나 `hasPrev`/`hasNext` 모두 false이므로 비활성화(EvidenceChainPage.tsx:123-138). | 테넌트 ID를 변경하거나 비우고 `t("legacy.evidenceChainPage.t5")` 버튼으로 재검증. | 구현됨: `items = timeline.data?.items ?? []`(EvidenceChainPage.tsx:60). `items.length === 0`일 때 `<EmptyState>{t("legacy.evidenceChainPage.t9")}</EmptyState>`(EvidenceChainPage.tsx:119). |
| 정상 | `timeline.data?.items.length > 0`이고 `verify.isSuccess`. 타임라인 데이터가 페이지네이션되어 표시됨. | `PageHeader`에 `t("legacy.evidenceChainPage.title1")`(EvidenceChainPage.tsx:65). 검증 결과 문구 `t("legacy.evidenceChainPage.t6")` 검증 결과: + `StatusBadge`(EvidenceChainPage.tsx:82-86). 타임라인 헤더 `t("legacy.evidenceChainPage.t7")` 감사 타임라인(EvidenceChainPage.tsx:95). 이벤트 리스트: 각 이벤트에 `event.aggregateType · event.action`, `StatusBadge status={event.outcome}`, `seq {event.sequenceNo} · {occurredAt.toLocaleString()}`, `{event.tenantId && ' · tenant ' + event.tenantId}`, `t("legacy.evidenceChainPage.t8", { eventHash })` 해시 {eventHash}(EvidenceChainPage.tsx:102-116). prev/next 버튼 활성. | `t("legacy.evidenceChainPage.t10")` 이전(EvidenceChainPage.tsx:130) / `t("legacy.evidenceChainPage.t11")` 다음(EvidenceChainPage.tsx:138) 버튼으로 페이지네이션. 테넌트 ID 변경 후 재검증. | 구현됨: 이벤트 렌더링 `items.map((event) => <li key={event.id}>...` (EvidenceChainPage.tsx:102-116). 페이지네이션 `pager.prev`/`pager.next`(EvidenceChainPage.tsx:127, 135). 검증 결과 렌더 `verify.isSuccess && verify.data` (EvidenceChainPage.tsx:82-86). |
| 낡은 데이터(신선도 초과) | `timeline.data`가 캐시에서 존재하지만 `timeline.isFetching === true` (React Query가 백그라운드에서 갱신 중). `useAuditTimeline` 훅의 `useQuery`가 staleTime 기본값(0)으로 인해 마운트/재마운트 시 매번 refetch(EvidenceChainPage.tsx:44, shared-hooks/src/useEvidence.ts:16-18). | 캐시된 데이터가 그대로 표시됨(이벤트 리스트, 검증 결과). prev/next 버튼이 `timeline.isFetching`으로 비활성화(EvidenceChainPage.tsx:128, 136). 로딩 인디케이터(스피너 등)는 없음. | 페이지네이션 불가(버튼 비활성화). 테넌트 ID 변경 후 재검증 가능. 백그라운드 refetch 완료 후 버튼 재활성화. | 구현됨: prev/next 버튼 비활성화 `disabled={!pager.hasPrev \|\| timeline.isFetching}`(EvidenceChainPage.tsx:128, 136). 캐시된 데이터 표시는 React Query 기본 동작. staleTime 명시 없음(기본 0 → 항상 refetch). 전역 로딩 인디케이터 없음. |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-EvidenceChainPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `verify.mutate()`가 바디 없이 `tenantId`를 query 파라미터로만 보내므로 400 검증 실패가 발생하지 않음. timeline은 GET 쿼리뿐(`EvidenceChainPage.tsx:13-16` 주석 — chain:verify는 바디가 없다). | 없음(400 갈래가 설계상 존재하지 않음) | 없음 | 없음 |
| 인증 필요/세션 만료(401) | `isSessionExpiredErrorCode`(`errorRouting.ts:128-129`)가 401 중 AUTH_*를 걸러 `auth_required`로 라우팅. `classifyForbidden`(`EvidenceChainPage.tsx:18`)은 403만 보므로 401은 403 분기를 타지 않음. `EvidenceActionError`(`EvidenceChainPage.tsx:17-29`)는 `auth_required` kind를 특별히 처리하지 않고 `ErrorMessage`(`EvidenceChainPage.tsx:21`)로 흘려보냄. `ErrorMessage`는 `errorCode`가 null이므로 `getApiErrorMessage`(`apiError.ts:106-118`)가 `DEFAULT_API_ERROR_MESSAGE`(정적 문자열) 반환. | `미확인` (공통 `routeApiError`(`errorRouting.ts:85-133`)가 401을 `auth_required`로 매핑하는지는 `errorRouting.ts:128-129`에서 확인했지만, `ErrorMessage`가 이를 표시하는 경로는 없음) | 없음 | 없음 |
| 권한 없음/MFA 필요(403) | `verify.isError` 시 `EvidenceActionError` 렌더(`EvidenceChainPage.tsx:87-91`). `classifyForbidden(error)`(`EvidenceChainPage.tsx:18`, `forbidden.ts:31-41`)가 `mfa_required`/`policy`/`tenant_mismatch`/`forbidden` 중 하나면 `ForbiddenNotice`(`EvidenceChainPage.tsx:18`, `ForbiddenNotice.tsx:31-50`) 렌더. timeline 실패 시에도 동일 경로(`EvidenceChainPage.tsx:96-97`). | `mfa_required`: `getApiErrorMessage("AUTH_MFA_REQUIRED")` → `"추가 인증이 필요합니다."` (`apiError.ts:65`). `ForbiddenNotice`가 `onStepUp` 버튼 표시(`ForbiddenNotice.tsx:42-46`). 그 외 갈래: `getApiErrorMessage` 반환 문구. | `mfa_required`: `onStepUp` 버튼 클릭(현재 `EvidenceActionError`에 `onRetry` 미전달). | 구현됨 |
| 충돌/상태 불일치(409) | `ChainIntegrityError`(409)는 페이지에서 재계산하지 않음(`EvidenceChainPage.tsx:36-38` 주석 — 서버 판정이 권위). 일반 경로 `EvidenceActionError`(`EvidenceChainPage.tsx:17-29`) → `ErrorMessage`(`EvidenceChainPage.tsx:21`)로 흐름. `routeApiError`(`errorRouting.ts:85-133`)에서 `INTEGRITY_IDEMPOTENCY_CONFLICT`는 `idempotency_*` 갈래, `INTEGRITY_WALLET_BALANCE_DRIFT`(`errorRouting.ts:107-109`)는 `wallet_balance_drift` 갈래로 가지만, `ChainIntegrityError`의 errorCode는 미정. 그 외 미지 409는 `classifyStateConflict`(`errorRouting.ts:111-113`)가 `invalid_transition`을 반환. | `미확인` (`ErrorMessage`(`ErrorMessage.tsx:27-72`)가 `invalid_transition` kind를 특별히 처리하지 않으므로, `getApiErrorMessage`(`apiError.ts:106-118`)가 errorCode를 받아서 매핑 — errorCode가 `INTEGRITY_WALLET_BALANCE_DRIFT`면 `"지갑 잔액 정합성을 확인하고 있습니다. 잠시 후 다시 시도해주세요."`(apiError.ts:77), 그 외 `DEFAULT_API_ERROR_MESSAGE`(apiError.ts:45-46) | 없음 | 없음 |
| [모순 메모] 데이터 표(data, `:28`)의 "403 처리가 누락됨"은 사실과 다름. timeline 실패 시(`:97`)에도 `EvidenceActionError`가 쓰이므로 `classifyForbidden` → `ForbiddenNotice` 403 처리가 누락되지 않음. 이 메모는 st-b 블록 안에서만 참조할 것. `:28` 셀은 원문 그대로 둔다. | — | — | — | — |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-EvidenceChainPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | **timeline(423 AUTH_ACCOUNT_LOCKED):** `useAuditTimeline`(`shared-hooks/src/useEvidence.ts:15`)의 React Query `useQuery`가 423 응답 → `isError=true`, error 객체에 `statusCode:423`, `errorCode:"AUTH_ACCOUNT_LOCKED"`, `retryAfterSec` 포함. **timeline(429 RATE_LIMIT_EXCEEDED):** 동일 훅이 429 응답 → `isError=true`, error 객체에 `statusCode:429`, `errorCode:"RATE_LIMIT_EXCEEDED"`, `retryAfterSec`. **verify(423/429):** `useVerifyAuditChain`(`shared-hooks/src/useEvidence.ts:22`)의 React Query `useMutation`이 423/429 응답 → `isError=true`, error 동일 구조. | **timeline 423:** 사용자 문구 없음 — `timeline.data`가 undefined이므로 `items=[]` → `EmptyState`에 `t("legacy.evidenceChainPage.t9")` 타임라인 이벤트가 없습니다.(`EvidenceChainPage.tsx:60,119`). 에러 배너 없음. **timeline 429:** 동일 — EmptyState만 표시. **verify 423:** `EvidenceActionError`(`EvidenceChainPage.tsx:89`) → `classifyForbidden`(`:18`) null(403 아님) → `routeApiError`(`:19`) → `deriveLockout`(`accountLockout.ts:41`) `{locked:true, retryAfterSec}` → account_locked 라우트 → `ErrorMessage`(`:21`)에 `retryAfterSec` 전달. `classifyRetry`(`shared-types/src/retryable.ts:40`)에서 AUTH_ACCOUNT_LOCKED은 BACKOFF 코드 집합에 없음 → 재시도 불가 → 재시도 버튼 안 나옴. **verify 429:** `routeApiError` → `classifyServerError`(`shared-types/src/serverError.ts:10`) not_applicable → `classifyRetry`(`shared-types/src/retryable.ts:40`)에서 RATE_LIMIT_EXCEEDED가 BACKOFF에 있음 → backoff_retry → `ErrorMessage`에 `retryAfterSec` 전달 → 카운트다운 후 재시도 버튼 활성화. | **timeline 423/429:** 재시도 버튼 없음 — `timeline.isFetching`이 false이므로 prev/next 버튼(`EvidenceChainPage.tsx:128,136`)은 활성화되나 클릭 시 재요청해도 같은 에러 반복. **verify 423:** 재시도 버튼 없음(잠금이므로). **verify 429:** 재시도 버튼 있음 — 카운트다운 후 활성화(`ErrorMessage.tsx:66`). | **timeline 423/429: 구현 누락.** `timeline.isError` 조건문이 없음(`EvidenceChainPage.tsx:87`은 `verify.isError`만 체크). 423은 lockout 안내, 429는 속도 제한 안내가 필요하지만 EmptyState만 보여줌. **verify 423: 구현됨.** `ErrorMessage`가 lockout 문구(`apiError.ts EXACT_MESSAGES`) + `retryAfterSec` 카운트다운 표시. 재시도 버튼 없음(적절). **verify 429: 구현됨.** `ErrorMessage`가 backoff 문구 + 카운트다운 재시도 버튼. |
| 서버 오류(5xx) | **timeline(500/502/503):** `useAuditTimeline`(`shared-hooks/src/useEvidence.ts:15`)의 React Query `useQuery`가 5xx 응답 → `isError=true`, error 객체에 `statusCode:500~503`, `errorCode:"INTERNAL_ERROR"` 또는 `"EXCHANGE_FATAL"` 또는 `"EXCHANGE_UNAVAILABLE"` 또는 `"DEPENDENCY_NOT_READY"`. **verify(5xx):** `useVerifyAuditChain`(`shared-hooks/src/useEvidence.ts:22`)의 React Query `useMutation`이 5xx 응답 → `isError=true`, 동일 구조. | **timeline 5xx:** 사용자 문구 없음 — EmptyState만 표시(`EvidenceChainPage.tsx:119`). 에러 배너 없음. **verify 500/502(EXCHANGE_FATAL/INTERNAL_ERROR):** `routeApiError` → `classifyServerError`(`shared-types/src/serverError.ts:10`) → fatal 라우트 → server_fatal → `ErrorMessage`에 `traceId` 전달. `classifyRetry`(`shared-types/src/retryable.ts:40`) → 재시도 불가 → 재시도 버튼 없음. **verify 503(EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY):** `routeApiError` → `classifyServerError`(`shared-types/src/serverError.ts:10`) → retryable → backoff_retry → `ErrorMessage`에 `retryAfterSec` 전달 → 카운트다운 재시도. | **timeline 5xx:** 재시도 없음 — 페이지가 `timeline.isError`를 체크하지 않음. **verify 500/502:** 재시도 버튼 없음(치명적 오류이므로 적절). **verify 503:** 재시도 버튼 있음(backoff). | **timeline 5xx: 구현 누락.** `timeline.isError` 조건문 없음. 5xx에서도 EmptyState만 보여서 "서버 오류"와 "데이터 없음" 구분 불가. **verify 5xx: 구현됨.** `ErrorMessage`가 서버 오류 분류에 따라 trace_id 표시(500/502) 또는 backoff 재시도(503). |
| 네트워크 단절 | **timeline:** fetch 실패(DNS 실패, 연결 끊김) → `useAuditTimeline`(`shared-hooks/src/useEvidence.ts:15`)의 React Query `useQuery`가 Error 객체 던짐(statusCode 필드 없음) → `isError=true`, error는 `Error` 인스턴스. **verify:** 동일 — `useVerifyAuditChain`(`shared-hooks/src/useEvidence.ts:22`)의 `useMutation`이 fetch 실패 → `isError=true`, error는 `Error` 인스턴스. | **timeline:** `items=[]` → EmptyState(`EvidenceChainPage.tsx:119`). 에러 배너 없음. **verify:** `EvidenceActionError`(`:89`) → `classifyForbidden`(`:18`) null(statusCode 없음) → `routeApiError`(`:19`) → 미지 코드 → unknown → `ErrorMessage`(`:21`)에 `error instanceof Error ? error.message` 전달. traceId 없음. `classifyRetry`(`shared-types/src/retryable.ts:40`) → 재시도 불가 → 재시도 버튼 없음. | **timeline:** 재시도 없음. **verify:** 재시도 버튼 없음(`onRetry`가 `EvidenceActionError`에 전달되지 않음 — `EvidenceChainPage.tsx:89`). | **timeline: 구현 누락.** 네트워크 실패 시 EmptyState와 구분 불가. **verify: 기본 구현.** `ErrorMessage`가 `error.message`를 표시하나 i18n 키 없음(네트워크 오류는 `apiError.ts EXACT_MESSAGES`에 없음). 재시도 버튼이 없음 — `EvidenceActionError`가 `onRetry`를 전달하지 않음(`EvidenceChainPage.tsx:89`). 네트워크 복구 후 수동 새로고침 필요. |
| 부분 실패(일부 데이터만 실패) | **pagination 중 한 페이지 실패:** `pager.next()` 클릭 시 `useAuditTimeline({cursor: nextCursor, limit: 20})` 재실행 → 실패 시 `isError=true`, data는 이전 페이지 데이터 유지. **한 페이지 성공/다른 페이지 실패:** cursor가 바뀌어 `timeline.data`가 새 실패 응답으로 교체 → `items=[]` → EmptyState. | **pagination 실패:** 이전 페이지 데이터가 `timeline.data`에 캐시되어 유지되면 `items`에 이전 이벤트 표시. 실패 시 EmptyState. prev/next 버튼은 `timeline.isFetching`이 false이므로 활성화(`EvidenceChainPage.tsx:128,136`). | **pagination 실패:** prev/next 클릭 시 재요청. 같은 cursor로 재시도하면 같은 에러 반복. | **구현됨(기본적).** `useCursorPage`(`hooks/useCursorPage.ts:25`)가 cursor 상태를 관리하고, `useAuditTimeline`이 cursor 변경 시 재조회. 실패 시 이전 데이터 유지(React Query initialData 아님 — `timeline.data?.items ?? []`가 undefined이므로 `[]`). prev/next 버튼은 활성화되나 재시도해도 같은 에러. 부분 실패에 대한 별도 복구 UI 없음. |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-EvidenceChainPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:79`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:123`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:131`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-EvidenceChainPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-EvidenceChainPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:18`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:18`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:19`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:21`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-EvidenceChainPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-EvidenceChainPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-EvidenceChainPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
