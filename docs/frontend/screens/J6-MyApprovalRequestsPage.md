# J6-MyApprovalRequestsPage — `/approval-requests`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MyApprovalRequestsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/approval-requests` / `frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-MyApprovalRequestsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P7(기관) — 거버넌스·승인 체인, 승인 요청 처리 [확인: PERSONAS_AND_JOURNEYS.md §2 표 P7, `approvals` 라우트] |
| 목적(한 문장) | 사용자가 직접 생성한 FD-10.1(SOLO/DUAL) 승인 요청을 조회하고, 강제 대기시간 이후 본인이 직접 승인 또는 거절한다. [확인: MyApprovalRequestsPage.tsx:15-17 주석, t4 문구] |
| 진입 조건 | `router.tsx:147` — `/approval-requests` 라우트. `protect()` 래퍼가 `ProtectedRoute` 가드 적용. `ProtectedRoute.tsx:20-21`: 미로그인(`!token`) 시 `/login?next=...` 리다이렉트. `ProtectedRoute.tsx:32-33`: MFA 미설정(`!me.mfaEnabled`) 시 `/onboarding/mfa-setup` 리다이렉트. `ProtectedRoute.tsx:35-41`: 위험 프로필 미설정(`!riskProfile`) 시 `/onboarding/risk-assessment` 리다이렉트. [확인] |
| 사용자 준비물 | 로그인 세션, MFA 활성화, 위험 프로필 설정 [확인: ProtectedRoute.tsx 조건 순서] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-MyApprovalRequestsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useApproveMyRequest` | `frontend/packages/shared-hooks/src/useApprovals.ts:12` | `useMutation` 기반 trigger(OnClick). `onSuccess`에서 `qc.invalidateQueries({ queryKey: ["myApprovalRequests"] })`로 목록 자동 갱신(MyApprovalRequestsPage.tsx:46, 60). `mutateAsync` 사용으로 동기 처리(MyApprovalRequestsPage.tsx:60). | `handle` 함수(MyApprovalRequestsPage.tsx:57-65)에서 `try/catch` 후 `error` state에 저장. `RequestActionError` 컴포넌트(MyApprovalRequestsPage.tsx:79)가 `classifyForbidden`/`routeApiError`로 분류해 `ForbiddenNotice`(403) 또는 `ErrorMessage`(기타) 표시. 409(STATE_INVALID_TRANSITION)는 재시도 버튼 없음. |
| `useRejectMyRequest` | `frontend/packages/shared-hooks/src/useApprovals.ts:20` | `useMutation` 기반 trigger(OnClick). `onSuccess`에서 `qc.invalidateQueries({ queryKey: ["myApprovalRequests"] })`로 목록 자동 갱신(MyApprovalRequestsPage.tsx:47, 61). `mutateAsync` 사용으로 동기 처리(MyApprovalRequestsPage.tsx:61). | approve와 동일 처리(MyApprovalRequestsPage.tsx:62). `error` state 저장 → `RequestActionError` 렌더링. |
| `useMyApprovalRequests` | `frontend/packages/shared-hooks/src/useApprovals.ts:5` | `useQuery` 기반 1회 조회(on mount). `queryKey: ["myApprovalRequests"]`, `queryFn: apiClient.listMyApprovalRequests()`(useApprovals.ts:6-9). 폴링/자동 재조회 옵션 미설정 → mount 시 1회만 조회. 수동 `refetch` 함수 expose(MyApprovalRequestsPage.tsx:104). | `isError`일 때(MyApprovalRequestsPage.tsx:112-113): `RequestActionError`에 `error`와 `onRetry={() => refetch()}` 전달. `refetch`는 목록 재조회. 403이면 `ForbiddenNotice`, 그 외는 `ErrorMessage` 표시. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-MyApprovalRequestsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-MyApprovalRequestsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-MyApprovalRequestsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-MyApprovalRequestsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:81`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:90`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-MyApprovalRequestsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-MyApprovalRequestsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:25`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:25`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:26`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/approvals/MyApprovalRequestsPage.tsx:29`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-MyApprovalRequestsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-MyApprovalRequestsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-MyApprovalRequestsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
