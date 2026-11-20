# J6-AuditLogPage — `/admin/audit-log`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/AuditLogPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/admin/audit-log` / `frontend/apps/web/src/routes/admin/AuditLogPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-AuditLogPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P8(운영자) — `admin/*` 라우트 전역 보호(`router.tsx:178`, `protectAdmin`) + P11(감사자·컴플라이언스) — 읽기 전용 감사 기록 조회(`PERSONAS_AND_JOURNEYS.md:32`, `AUDITOR` 역할) |
| 목적(한 문장) | Break-Glass 그랜트 ID로 인증한 후 시스템 작업 기록(액션 타입·대상·실행자·시간)을 필터·검색·페이지네이션으로 조회 |
| 진입 조건 | `router.tsx:178` — `/admin/audit-log` 라우트는 `protectAdmin(<AuditLogPage />)`으로 보호; `protectAdmin` 은 `router.tsx:74~80` 정의, `ProtectedRoute` → `AdminRoute`(`AdminRoute.tsx:11`) 중첩 가드. AdminRoute 통과 = 플랫폼 관리자 또는 AUDITOR 역할. |
| 사용자 준비물 | UUID 형식의 Break-Glass 그랜트 ID(`AuditLogPage.tsx:75~80`, `placeholder2`), `useAuditLog` 훅(`shared-hooks/src/useAdmin.ts:116`)이 `breakGlassGrantId` 비어 있음 체크(`enabled: !!breakGlassGrantId`, `useAdmin.ts:129`) |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-AuditLogPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useAuditLog` | `frontend/packages/shared-hooks/src/useAdmin.ts:116` | `submittedGrantId` 변경 시 `queryKey` 재작성(`useAdmin.ts:127`), `refetch()` 수동 갱신(`AuditLogPage.tsx:117`) | `routeApiError`로 분류(`AuditLogPage.tsx:27`): 403→`ForbiddenNotice`, 409→`ErrorMessage`, backoff_retry→retry 버튼 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-AuditLogPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `submittedGrantId` 있음 + `isLoading === true` (`AuditLogPage.tsx:118~119`) | `<LoadingState />` (`AuditLogPage.tsx:119`) | 대기 | 구현됨 |
| 빈 상태(그랜트 미입력) | `!submittedGrantId` (`AuditLogPage.tsx:114~115`) | `<EmptyState>` + `"그랜트 ID를 입력하고 조회하세요."` (`AuditLogPage.tsx:115`, `t7`) | 그랜트 ID 입력 후 조회 | 구현됨 |
| 빈 상태(데이터 없음) | `submittedGrantId` 있음 + `isError=false` + `data.items.length === 0` (`AuditLogPage.tsx:142~143`) | `<EmptyState>` + `"감사 로그가 없습니다."` (`AuditLogPage.tsx:143`, `t10`) | 필터 변경 후 재조회 | 구현됨 |
| 정상 | `data` 있고 `data.items.length > 0` (`AuditLogPage.tsx:120~141`) | 로그 항목 목록(`<ul>`, `AuditLogPage.tsx:121`), 각 항목: actionType, createdAt, actorAgent+userId, targetType+targetId | 페이지네이션(`<Pagination>`, `AuditLogPage.tsx:146~148`) | 구현됨 |
| 낡은 데이터(신선도 초과) | React Query 기본: staleTime=0 (undefined) → 즉시 stale | TODO | `refetch()`로 갱신 가능(`AuditLogPage.tsx:117`) | staleTime 미설정 — 즉시 낡음으로 간주 |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-AuditLogPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 서버가 400 반환 시(그랜트 ID 형식 불만 등) | `routeApiError`가 `"other"` → `<ErrorMessage>` (`AuditLogPage.tsx:27~35`) | `routeApiError`가 retry/backoff 정보를 담으면 retry 버튼 표시 | 구현됨 (routeApiError 분기) |
| 인증 필요/세션 만료(401) | `ProtectedRoute`(`router.tsx:70~72`)가 401 감지 시 `/login` 리다이렉트 | 로그인 페이지로 이동 | 로그인 후 재진입 | 구현됨 (ProtectedRoute 중첩) |
| 권한 없음/MFA 필요(403) | `classifyForbidden(error)` 참 (`AuditLogPage.tsx:26`) | `<ForbiddenNotice error={error} />` (`AuditLogPage.tsx:26`) | 권한 요청 또는 그랜트 ID 변경 | 구현됨 |
| 충돌/상태 불일치(409) | `BreakGlassInvalidStateError` → `STATE_INVALID_TRANSITION(409)` (`AuditLogPage.tsx:21~24` 주석) | `routeApiError`가 `"other"` → `<ErrorMessage>` (`AuditLogPage.tsx:27~35`) | 그랜트 ID 재발급 후 재조회 | 구현됨 |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-AuditLogPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 서버가 423/429 반환 | `routeApiError`가 `"backoff_retry"` → `retryAfterSec` + retry 버튼 (`AuditLogPage.tsx:33`, `AuditLogPage.tsx:62`) | `retryAfterSec` 초 대기 후 retry | 구현됨 |
| 서버 오류(5xx) | `isError` 참, `routeApiError.kind !== "backoff_retry"` | `<ErrorMessage>` (`AuditLogPage.tsx:29~35`) | `ErrorMessage`의 retry 또는 수동 재조회 | 구현됨 |
| 네트워크 단절 | React Query: `networkStatus` 실패 | `routeApiError`가 `"other"` → `<ErrorMessage>` | 네트워크 복구 후 수동 `refetch()` | 부분 구현 (React Query 재시도 자동, but UI 안내 없음) |
| 부분 실패(일부 데이터만 실패) | `data.items` 일부만 실패 | TODO | TODO | 해당 없음 — API 전체 페이지네이션 응답(`ApiResponsePageMeta`, `AuditLogPage.tsx:64~66`) 단위 실패 |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-AuditLogPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`AuditLogPage.tsx:103`) — "조회" | `handleSearch()` 호출(`AuditLogPage.tsx:56~59`): `page=1` 리셋 + `submittedGrantId=grantIdInput.trim()` | `useAuditLog` `queryKey` 재작성(`useAdmin.ts:127`), `isLoading=true → data 수신 → 목록/빈 상태 렌더링` | `isError=true` → `AuditLogError` (`AuditLogPage.tsx:117`): 403→ForbiddenNotice, 409→ErrorMessage, backoff→retry 버튼 | 동일 화면(필터 변경 시) |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-AuditLogPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음 — 읽기 전용 감사 로그 조회 화면. 금액·주문·이체 등 금전 행동 없음. Break-Glass 그랜트 ID 요구(`AuditLogPage.tsx:75~80`, `useAdmin.ts:129`)는 권한 검증(접근 제어)용이지 금전 흐름 아님. |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-AuditLogPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`AuditLogPage.tsx:26`) | — (판정 로직, 문구 아님) | `ForbiddenNotice` 렌더링으로 전이 | trace_id 미노출 (`ForbiddenNotice` 내부 구현 확인 필요) |
| `ForbiddenNotice` (`AuditLogPage.tsx:26`) | — (컴포넌트, 문구 아님) | 권한 요청 또는 그랜트 ID 변경 후 재시도 | trace_id 미노출 |
| `routeApiError` (`AuditLogPage.tsx:27`) | — (분류 로직, 문구 아님) | 403→ForbiddenNotice, backoff→retry 버튼, 그 외→ErrorMessage | `ApiError.traceId`가 `ErrorMessage`에 전달(`AuditLogPage.tsx:32`) |
| `ErrorMessage` (`AuditLogPage.tsx:29`) | — (컴포넌트, 문구 아님) | `errorCode`, `message`, `traceId` 표시. `retryAfterSec` 있으면 countdown retry 버튼 | `traceId` 전달됨(`AuditLogPage.tsx:32`) |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-AuditLogPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | 그랜트 ID Input에 `aria-label`(`AuditLogPage.tsx:76`). 나머지 3 Input(label3~5)에 aria-label 존재(`AuditLogPage.tsx:83,90,97`). Button에 label은 i18n(`t6`="조회", `AuditLogPage.tsx:110`). | `data-testid="audit-log-list"`(`AuditLogPage.tsx:121`)는 있으나 `LoadingState`/`EmptyState`에 testid 없음. |
| i18n/지역화 | 모든 사용자 문구 `t("legacy.auditLogPage.*")` 키 사용(`AuditLogPage.tsx:72~143`, 총 13개 키). `new Date().toLocaleString()`(`AuditLogPage.tsx:126`)은 브라우저 locale 의존. | 날짜 포맷이 locale 의존적(`AuditLogPage.tsx:126`). `toLocaleString()` 대신 i18n 또는 ISO 문자열 권장. |
| 성능 예산 | `DEFAULT_PAGE_SIZE = 20`(`AuditLogPage.tsx:14`). 페이지당 20항목 렌더링. React Query 기본 `staleTime=0` → 즉시 stale. | staleTime 미설정 — 매 렌더 시마다 stale 재검사로 불필요 리페치 가능성. |
| 텔레메트리 | TODO | TODO | 확인한 텔레메트리(로깅·애널리틱스·RUM) 없음. `AuditLogPage.tsx` 전체에 telemetry 호출 없음. |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-AuditLogPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | `/admin/audit-log` 라우트에 접근 시 `ProtectedRoute` + `AdminRoute`가 적용되어 비관리자/비AUDITOR는 리다이렉트됨 | `router.tsx:178` 가드 구조 확인 + `protectAdmin` 정의(`router.tsx:74~80`) |
| A2 | Break-Glass 그랜트 ID를 입력하지 않은 상태에서는 EmptyState("그랜트 ID를 입력하고 조회하세요.")가 표시되고 조회 버튼 클릭 시 API 호출이 발생하지 않음 | `useAuditLog.enabled: !!breakGlassGrantId`(`useAdmin.ts:129`) + `!submittedGrantId` 조건(`AuditLogPage.tsx:114`) |
| A3 | 403 오류 발생 시 `ForbiddenNotice`가 표시되고, 409 충돌/기타 오류는 `ErrorMessage`로 분리되며, backoff_retry 응답은 retry倒计时 버튼을 표시함 | `AuditLogError` 컴포넌트(`AuditLogPage.tsx:25~37`)의 `classifyForbidden`/`routeApiError` 분기 |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-AuditLogPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | 날짜 포맷이 locale 독립적이어야 함(WCAG i18n) | `new Date(entry.createdAt).toLocaleString()`(`AuditLogPage.tsx:126`) — 브라우저 locale 의존 | 중 | i18n 키 또는 ISO 8601 문자열로 교체 |
| G2 | `LoadingState`/`EmptyState`에 `data-testid` 미부여 | `audit-log-list`만(testid, `AuditLogPage.tsx:121`) | 경 | 테스트 커버리지를 위해 `loading-state`/`empty-state` testid 추가 |
| G3 | React Query `staleTime` 명시 부재 | 기본값 사용(`staleTime=0` → 즉시 stale) | 경 | 명시적 `staleTime` 설정으로 불필요 리페치 방지 |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
