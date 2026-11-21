# J6-MembersPage — `/settings/members`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MembersPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/settings/members` / `frontend/apps/web/src/routes/settings/MembersPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-MembersPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더/투자자) — J1~J6 여정이 P1 중심 (§3 여정 지도). P8(운영자)도 `/admin/*`에서 멤버 관리를 할 수 있으나 이 화면은 P1의 조직/가구 테넌트 관리용 |
| 목적(한 문장) | 활성 테넌트(조직/가구)의 멤버 목록을 확인하고, 멤버를 초대·정지·폐기하여 테넌트 접근 권한을 관리한다 |
| 진입 조건 | `router.tsx:152` — `/settings/members` 라우트, `protect()` 게이트 통과(=`ProtectedRoute`로 인증 완료). `useTenant().activeTenantId`가 null이 아님(테넌트 선택 완료) |
| 사용자 준비물 | 로그인 세션, 활성 테넌트(조직/가구) 선택, 테넌트에서 `canManageMembers=true` 역할(OWNER 또는 ACTIVE+ADMIN) |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-MembersPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useTenant` | `frontend/apps/web/src/hooks/useTenant.ts:60` | Zustand 스토어(모듈 로드 시 1회 `createTenantStore()` → `activeTenantId` 초기화). 사용자 테넌트 전환 시 `setActiveTenant()` 호출로 반응성 갱신(`useTenantStore` subscribe). 폴링 없음. | 스토어 초기화 실패 시 `activeTenantId=null` → 화면에서 `EmptyState` 표시(`MembersPage.tsx:218-227`). 403 AUTH_TENANT_MISMATCH 시 `handleForbidden()`이 `TenantMismatchFallback`를 반환하고 `activeTenantId`를 personal(null)로 리셋(`useTenant.ts:39-43`). 호출부는 fallback 값을 활용해 안내 배너 표시 가능. |
| `useMe` | `frontend/packages/shared-hooks/src/useAuth.ts:10` | react-query `useQuery` — `queryKey: ["me"]`, `queryFn: () => apiClient.getMe()`, `enabled: !!token`(useAuthStore.token 존재 시 1회 조회). `retry: false`(재시도 없음). 폴링 없음(반응 옵션에 refetch 관련 설정 없음). 로그인/로그아웃/Signup 시 `qc.invalidateQueries({ queryKey: ["me"] })`로 캐시 무효화 후 자동 재조회(`useAuth.ts:27,39,74`). | `retry: false`이므로 첫 조회 실패 시 즉시 `isError=true`. 호출부(`MembersPage.tsx:151`)는 `data: me`만 destruct하고 error 상태를 무시 — 컴포넌트 레벨에서 오류 UI 미표시. `me`가 undefined면 `resolvedCurrentUserId`가 `null`이 되어 `canManageMembers=false`(최소권한). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-MembersPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-MembersPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 폼 제출 시 서버 400 반환. 브라우저 `required` 검증(`MembersPage.tsx:267`)은 클라이언트 측만. 서버 400 전용 분기 없음. | `handleGrant` catch(`MembersPage.tsx:193-195`) → `setActionError(err)` → `MutationErrorBanner`(`MembersPage.tsx:98`) → `classifyMembershipError(err)`(`MembersPage.tsx:99`) → `last_owner_denied` 아님 → `classifyForbidden(err)`(`MembersPage.tsx:101`) → null(400는 403 아님) → `ErrorMessage(message=describeMembershipError(...))`(`MembersPage.tsx:102`). 서버 400 전용 UI 분기는 없고 고정 문구로만 표시. | 없음. `refresh()` 미호출. 버튼 다시 활성화. | 부분 |
| 인증 필요/세션 만료(401) | API 호출 시 토큰 만료로 401 반환. 토큰은 `useAuthStore.getState().token`(`MembersPage.tsx:162`) 공급. 로그인 리다이렉트 코드는 이 파일에 없음. | 목록 조회 실패: `ListErrorBanner`(`MembersPage.tsx:83-96`) → `classifyForbidden` null → `routeApiError`(`MembersPage.tsx:24,85`) → auth_required kind → `ErrorMessage`(`MembersPage.tsx:88-94`). 뮤테이션 실패: `MutationErrorBanner`(`MembersPage.tsx:98-102`) → `classifyMembershipError` → unknown_denied → `ErrorMessage(message=describeMembershipError("unknown_denied"))` → "요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요." | 없음. `ErrorMessage`는 auth_required에 대해 재시도 버튼 안 봄(retryable 분류 "none"). | 부분 |
| 권한 없음/MFA 필요(403) | API 호출 시 403 반환. `classifyForbidden`(`MembersPage.tsx:19,84,101`)이 네 갈래(mfa_required/tenant_mismatch/policy/forbidden)로 분류. 버튼 `title` `NO_PERMISSION_TITLE`(`MembersPage.tsx:64,129,138,290`)는 403 응답이 아니라 `canManage=false` 사전 차단. 마지막 소유자 거부 403(`POLICY_*`/`RISK_*`)은 `classifyMembershipError`(`MembersPage.tsx:99-100`)가 `last_owner_denied`로 먼저 잡음. | 목록 조회 실패: `ListErrorBanner`(`MembersPage.tsx:84`) → `classifyForbidden` 참 → `ForbiddenNotice`(`MembersPage.tsx:84`). 뮤테이션 실패: `MutationErrorBanner`(`MembersPage.tsx:101`) → `classifyForbidden` 참 → `ForbiddenNotice`(`MembersPage.tsx:101`). `ForbiddenNotice`(`ForbiddenNotice.tsx:36`)는 `getApiErrorMessage`로 문구 표시(예: AUTHZ_FORBIDDEN → "이 작업을 수행할 권한이 없습니다.", `POLICY_*` → "정책에 의해 거부된 요청입니다. 세부 사유를 확인해주세요."). MFA일 때 `ForbiddenNotice`(`ForbiddenNotice.tsx:42-46`)가 `onStepUp` 버튼 표시하지만 MembersPage는 `onStepUp`을 전달하지 않음. | MFA일 때 `onStepUp` 가능하지만 MembersPage 미전달. 나머지 재시도 없음. | 부분 |
| 충돌/상태 불일치(409) | grant/suspend/revoke 중 409 반환. `classifyMembershipError`(`MembersPage.tsx:99`) → `routeApiError`가 invalid_transition(409) → `last_owner_denied`. | `ErrorMessage(message=describeMembershipError("last_owner_denied"))`(`MembersPage.tsx:100`) → "테넌트에는 활성 소유자(OWNER)가 최소 1명 있어야 합니다." | 없음. `refresh()` 호출 안 됨(`MembersPage.tsx:181,192,205`는 성공 경로에만 있음). traceId 전달 안 됨(`MembersPage.tsx:100`에 `traceId` 없음, `ListErrorBanner`의 `traceId`(`MembersPage.tsx:88-91`)와 대비). | 부분 |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-MembersPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 423(ACCOUNT_LOCKED) 또는 429(RATE_LIMIT_EXCEEDED) 응답. `routeApiError`(`MembersPage.tsx:24,85`)가 423→잠김(kind:account_locked, retryAfterSec 포함), 429→속도제한(kind:backoff_retry, afterSec 포함)으로 분류. `ListErrorBanner`(`MembersPage.tsx:83-95`)에서 `routed.kind==="backoff_retry"`이면 `canRetry=true`, `retryAfterSec` 설정. 423은 잠김 kinds이므로 `canRetry=false`. | 429: `ErrorMessage`(`MembersPage.tsx:88-94`)가 속도 제한 문구(`getApiErrorMessage`로 `shared-types/apiError.ts` EXACT_MESSAGES 참조) + 초 단위 카운트다운(`ErrorMessage.tsx:49-53`) + 재시도 버튼(남은 시간 0 이하일 때 활성화). 423: `ErrorMessage`가 잠김 문구 + traceId 노출(있는 경우) — 재시도 버튼 없음. | 429: 재시도 버튼 클릭 시 `query.refetch()`(`MembersPage.tsx:234`) 호출로 목록 재조회. 423: 재시도 버튼 없음(계정 잠김 해제 전까지 같은 결과 반복). | 구현 |
| 서버 오류(5xx) | 500/502/503 중 서버 분류기가 아는 코드. `routeApiError`(`MembersPage.tsx:24,85-86`)가 502/503 중 retryable 코드→`backoff_retry`, 500 등 non-retryable→서버치명(kind:server_fatal). `ListErrorBanner`(`MembersPage.tsx:86`)에서 `canRetry` 판정. | 5xx retryable(502/503): `ErrorMessage`가 서버 오류 문구 + `backoff_retry` 카운트다운 + 재시도 버튼. 5xx fatal(500 등): `ErrorMessage`가 서버 오류 문구 + traceId 노출(`ErrorMessage.tsx:60`) — 재시도 버튼 없음. | 5xx retryable: 재시도 버튼 클릭 시 `query.refetch()`. 5xx fatal: 재시도 버튼 없음(서버 복구 필요). | 구현 |
| 네트워크 단절 | `fetch` 등 HTTP 호출 자체가 실패(DNS 실패, 연결 거부, 타임아웃). `ApiError`가 아님 — `isErrorCodeLike`(`errorRouting.ts:80-82`) false → `routeApiError`가 `{kind:"unknown"}` 반환. `ListErrorBanner`(`MembersPage.tsx:86`)에서 `canRetry=false`. | `ErrorMessage`(`MembersPage.tsx:88-94`)가 네트워크 오류 메시지(예: "Network Error") — errorCode/tracedId 없음(`ErrorMessage.tsx:60` traceId 조건 false). 재시도 버튼 없음(`canRetry=false`). | 없음. `ErrorMessage`는 retryable 분류 "none"이므로 재시도 버튼 안 봄. | 부분 |
| 부분 실패(일부 데이터만 실패) | `fetchMembers`가 일부 유효·무효 혼합 데이터 배열 반환. `rows.map`(`MembersPage.tsx:172`)에서 `parseMembershipView(raw)`(`MembersPage.tsx:23`)가 null 반환하는 행 존재. `query.isError=false`이므로 목록 렌더링 계속. | 유효 행: `MemberRow`(`MembersPage.tsx:243-251`) 정상 렌더링. 무효 행: `<Alert tone="danger">{t("legacy.membersPage.t7")}</Alert>`(`MembersPage.tsx:254`) — "멤버십 정보를 해석할 수 없습니다." | 없음. 호출부가 파싱 실패 행을 필터링하거나 재요청하지 않음. | 부분 |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-MembersPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (정지, `frontend/apps/web/src/routes/settings/MembersPage.tsx:124`) | `canManage=true`(deriveCapabilities(myMembership).canManageMembers, `MembersPage.tsx:178`), `pending=false`(pendingId !== view.membershipId, `MembersPage.tsx:248`), `view.state==="ACTIVE"`(`MembersPage.tsx:128`) | `onSuspend`(`MembersPage.tsx:130`) → `runOnMembership(view.membershipId, () => client.suspend(view.membershipId))`(`MembersPage.tsx:249`) → `setPendingId`(`MembersPage.tsx:202`) → `client.suspend()` 호출 → `refresh()`(`MembersPage.tsx:205`) → 목록 갱신, 상태 "정지" 표시, `setPendingId(null)`(`MembersPage.tsx:209`) | `setActionError(err)`(`MembersPage.tsx:207`) → `MutationErrorBanner`(`MembersPage.tsx:98-102`) → `classifyMembershipError`(`MembersPage.tsx:99`) → `last_owner_denied`면 `ErrorMessage(describeMembershipError("last_owner_denied"))`(`MembersPage.tsx:100`) → 그 외 `classifyForbidden` 참이면 `ForbiddenNotice`(`MembersPage.tsx:101`) → 그 외 `ErrorMessage`(`MembersPage.tsx:102`) → `setPendingId(null)`(`MembersPage.tsx:209`) | 동일 화면 (이 파일에 navigate/router.push/redirect import 또는 호출 없음) |
| `<Button>` (폐기, `frontend/apps/web/src/routes/settings/MembersPage.tsx:133`) | `canManage=true`, `pending=false`, `view.state!=="REVOKED"`(`MembersPage.tsx:137`), `window.confirm(t("legacy.membersPage.t12"))`(`MembersPage.tsx:214`) 확인 | `onRevoke`(`MembersPage.tsx:139`) → `handleRevoke(view)`(`MembersPage.tsx:250`) → `window.confirm` 확인 시 `runOnMembership(view.membershipId, () => client.revoke(view.membershipId))`(`MembersPage.tsx:215`) → `setPendingId` → `client.revoke()` 호출 → `refresh()` → 목록 갱신, 상태 "폐기" 표시, `setPendingId(null)` | `window.confirm` 취소 시 즉시 return(`MembersPage.tsx:214`). 그 외 실패 시 `setActionError` → `MutationErrorBanner` → `classifyMembershipError` → `last_owner_denied`면 고정 문구, `classifyForbidden` 참이면 `ForbiddenNotice`, 그 외 `ErrorMessage` → `setPendingId(null)` | 동일 화면 |
| `<Button>` (초대, `frontend/apps/web/src/routes/settings/MembersPage.tsx:286`) | `canManageMembers=true`(`MembersPage.tsx:289`), 폼 `required` 검증 통과(`MembersPage.tsx:267`), `subjectId` 비어 않음(`MembersPage.tsx:268`), `grantPending=false`(`MembersPage.tsx:288`) | `handleGrant`(`MembersPage.tsx:184-198`) → `e.preventDefault()`(`MembersPage.tsx:185`) → `client.grant({subjectId, role})`(`MembersPage.tsx:189`) → `setSubjectId("")`(`MembersPage.tsx:190`) · `setRole("MEMBER")`(`MembersPage.tsx:191`) → `refresh()`(`MembersPage.tsx:192`) → 새 멤버 목록에 반영 | `setActionError(err)`(`MembersPage.tsx:194`) → `MutationErrorBanner` → `classifyMembershipError` → `last_owner_denied`면 고정 문구, `classifyForbidden` 참이면 `ForbiddenNotice`, 그 외 `ErrorMessage` → `setGrantPending(false)`(`MembersPage.tsx:196`) → 폼 값 유지(초기화 안 함) | 동일 화면 |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-MembersPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-MembersPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/MembersPage.tsx:19`) | `getApiErrorMessage()` → shared-types의 errorCode 매핑 테이블(`shared-types/src/apiError.ts:55-84`). errorCode별: AUTH_MFA_REQUIRED→"추가 인증이 필요합니다.", AUTH_TENANT_MISMATCH→"이 리소스에 접근할 권한이 없습니다.", AUTHZ_FORBIDDEN→"이 작업을 수행할 권한이 없습니다.", POLICY_DENIED→"정책에 의해 거부된 요청입니다. 세부 사유를 확인해주세요.", RISK_DENIED→"위험 관리 기준에 의해 거부된 요청입니다. 세부 사유를 확인해주세요.". 미지 코드는 shared-types의 접두(prefix) 매칭(`apiError.ts:89-100`) → 기본 메시지("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.") | 네 갈래 중 다음 행동 결정: mfa_required→ForbiddenNotice가 onStepUp 버튼 표시, tenant_mismatch→테넌트 재선택 유도, policy→DenialReasons 컴포넌트로 세부 사유 목록 표시, forbidden→재시도 버튼 없이 권한 부족 안내만 표시. 모든 갈래에서 재시도 버튼 미제공(같은 403이 반복됨) | 노출 없음. classifyForbidden은 { statusCode, errorCode } 모양만 검사하고 traceId 필드를 읽지 않음(forbidden.ts:31-41). 호출부 ForbiddenNotice도 traceId prop을 받지 않음(ForbiddenNotice.tsx:14-17) |
| `classifyMembershipError` (`frontend/apps/web/src/routes/settings/MembersPage.tsx:20`) | describeMembershipError() 고정 한국어 문구(`shared-types/src/membershipMutation.ts:50-56`). last_owner_denied→"테넌트에는 활성 소유자(OWNER)가 최소 1명 있어야 합니다.", cross_tenant_denied→"다른 테넌트의 멤버십에는 접근할 수 없습니다.", action_forbidden→"이 멤버십에 대해 이 작업을 수행할 권한이 없습니다.", already_revoked→"이미 폐기되었거나 존재하지 않는 멤버십입니다.", unknown_denied→"요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요." | last_owner_denied→소유자 추가 필요(관리자 문의), cross_tenant_denied→올바른 테넌트 전환, action_forbidden→역할 확인 후 재시도, already_revoked→목록 새로고침, unknown_denied→잠시 후 재시도. MutationErrorBanner(MembersPage.tsx:98-103)에서 last_owner_denied는 ErrorMessage로, 나머지는 ForbiddenNotice(policy/forbidden 갈래 시) 또는 ErrorMessage로 분기 | 노출 없음. classifyMembershipError는 routeApiError 결과를 멤버십 도메인 reason 5갈래로 좁히는 순수 함수이며, traceId를 읽거나 전달하지 않음(membershipMutation.ts:32-48). describeMembershipError도 고정 문자열만 반환(membershipMutation.ts:61-63) |
| `routeApiError` (`frontend/apps/web/src/routes/settings/MembersPage.tsx:24`) | ErrorMessage 컴포넌트 내부 getApiErrorMessage()(`ErrorMessage.tsx:36`) → errorCode 매핑 테이블 1:1 매핑 또는 접두(prefix) 폴백 또는 기본 메시지. 목록 조회 실패 시(ListErrorBanner, MembersPage.tsx:85): routed.kind에 따라 ErrorMessage가 errorCode/message/traceId를 props로 받음. 구체적: auth_required→세션 만료/"세션이 만료되었습니다. 다시 로그인해주세요.", server_fatal→일시 오류+"일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.", backoff_retry→요청 과부하+"요청이 너무 많습니다. 잠시 후 다시 시도해주세요." | refetch_retry→대기 없이 즉시 재시도 버튼 활성화, backoff_retry→retryAfterSec 카운트다운 후 버튼 활성화, auth_required→재시도 버튼 미제공(로그인 리다이렉트 필요), server_fatal→traceId와 함께 문의 안내, 그 외→재시도 버튼 없음 | server_fatal kind일 때만 노출(`ErrorMessage.tsx:60`). ListErrorBanner(MembersPage.tsx:91)에서 `error instanceof ApiError ? error.traceId : undefined`로 traceId를 ErrorMessage.traceId prop에 전달. ErrorMessage가 {traceId} 조건부 렌더링으로 legacy.errorMessage.t1 키로 표시(TRACE_ID: {traceId}) |
| `Alert` (`frontend/apps/web/src/routes/settings/MembersPage.tsx:31`) | legacy.membersPage.t7 → "멤버십 정보를 해석할 수 없습니다." (docs/frontend/facts/MembersPage.facts.md:19, MembersPage.tsx:254) | 페이지 새로고침 또는 테넌트 재선택. MemberRow(MembersPage.tsx:253-256)에서 parseMembershipView(view)가 null을 반환한 경우(즉, 서버 응답이 MembershipView 스키마와 맞지 않는 경우) Alert(tone="danger")로 표시. 사용자는 버튼을 통해 목록을 새로고침할 수 없음(별도 재시도 버튼 없음) | 노출 없음. Alert 컴포넌트(ui-web/Feedback.tsx:4-19)는 tone과 children만 받고 traceId prop이 없음. 표시 문구는 i18n 키 하나로 고정 |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/MembersPage.tsx:84`) | getApiErrorMessage(extractErrorCode(error), extractMessage(error)) → errorCode 매핑 테이블. classifyForbidden이 null이 아니면(=403 응답이면) 항상 Alert(tone="danger")로 감싸서 표시. errorCode별 문구: AUTH_MFA_REQUIRED→"추가 인증이 필요합니다.", AUTH_TENANT_MISMATCH→"이 리소스에 접근할 권한이 없습니다.", POLICY_DENIED→"정책에 의해 거부된 요청입니다. 세부 사유를 확인해주세요.", RISK_DENIED→"위험 관리 기준에 의해 거부된 요청입니다. 세부 사유를 확인해주세요.", 그 외 403→"이 작업을 수행할 권한이 없습니다." | mfa_required일 때만 onStepUp 버튼 표시(legacy.forbiddenNotice.t1). policy 갈래일 때 DenialReasons 컴포넌트로 reason_codes 목록을 불릿 포인트로 상세 표시. 나머지 갈래(tenant_mismatch, forbidden)는 추가 액션 버튼 없이 문구만 표시. 재시도 버튼 미제공 | 노출 없음. ForbiddenNotice 컴포넌트(ForbiddenNotice.tsx:31-50)는 error와 선택적 onStepUp만 받고, extractErrorCode/extractMessage로 errorCode와 message만 추출. traceId를 읽거나 전달하지 않음 |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/MembersPage.tsx:88`) | getApiErrorMessage(errorCode, message)(`ErrorMessage.tsx:36`) → errorCode 매핑 테이블 또는 접두(prefix) 폴백 또는 기본 메시지. 목록 조회 시 errorCode는 `error instanceof ApiError ? error.errorCode : undefined`(MembersPage.tsx:89). 뮤테이션 시 errorCode는 전달되지 않음(message=describeMembershipError(...)만, MembersPage.tsx:100, 102). message prop이 있으면 errorCode 매핑 실패 시 fallback으로 사용 | classifyRetry 기반 재시도 판단: refetch→즉시 재시도 버튼 활성화, backoff→retryAfterSec 카운트다운, none→재시도 버튼 미표시. fieldErrors prop이 있고 키가 있으면 배너 전체를 렌더링하지 않음(중복 안내 방지, ErrorMessage.tsx:55). 재시도 버튼 클릭 시 실제 재시도는 호출부 책임(ErrorMessage.tsx:14 주석 참조) | 조건부 노출(`ErrorMessage.tsx:60`). traceId prop이 truthy일 때 `<p className="mt-1 text-xs text-fg-muted">{t("legacy.errorMessage.t1", { traceId })}</p>` 렌더링. i18n 키: legacy.errorMessage.t1. MembersPage.tsx:91에서 `error instanceof ApiError ? error.traceId : undefined`로 전달. traceId가 없는 경우(뮤테이션 에러, Non-ApiError) 표시 안 함 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-MembersPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-MembersPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-MembersPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
