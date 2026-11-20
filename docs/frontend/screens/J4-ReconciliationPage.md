# J4-ReconciliationPage — `/admin/reconciliation`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ReconciliationPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/admin/reconciliation` / `frontend/apps/web/src/routes/admin/ReconciliationPage.tsx`
- 여정 소속: J4


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J4-ReconciliationPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P8(운영자) — 가입 승인·검증 대기열·분쟁·입출금 승인·안전장치·정합성 점검 (`PERSONAS_AND_JOURNEYS.md` §2) |
| 목적(한 문장) | 대사(Reconciliation) 불일치 상태를 조회하고, 해소 사유를 입력하여 불일치를 해결한다. |
| 진입 조건 | `/admin/reconciliation` 경로 접근; `router.tsx:175`의 `protectAdmin(<ReconciliationPage />)` 가드 — `ProtectedRoute`(로그인) + `AdminRoute`(관리자 권한) 동시 통과 |
| 사용자 준비물 | 관리자 권한을 가진 활성 세션 |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J4-ReconciliationPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useReconciliationStates` | `frontend/packages/shared-hooks/src/useReconciliation.ts:10` | `useQuery` 1회 조회 (`queryKey: ["reconciliationStates"]`, `queryFn: apiClient.listReconciliationStates()`). `refetchInterval` 미설정 → 폴링 없음(`useReconciliation.ts:11-14`, 참조 `frontend/packages/shared-hooks/src/usePortfolio.ts:42`). 마운트 시 1회 GET `/reconciliation/list`(route: `reconciliation.list`, `frontend/packages/api-client/src/clients/reconciliation.ts:17`). window focus 시 자동 refetch (react-query 기본값). | `useQuery`의 `isError`/`error` 상태 발생 → 페이지 렌더링(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:114-115`)에서 `<ReconciliationActionError error={error} onRetry={() => refetch()} />` 표시. 에러 분류: `classifyBadRequest`(400)(`frontend/packages/shared-types/src/badRequest.ts:34`) → `BadRequestNotice`(`frontend/apps/web/src/components/BadRequestNotice.tsx:40`), `classifyForbidden`(403)(`frontend/packages/shared-types/src/forbidden.ts:31`) → `ForbiddenNotice`(`frontend/apps/web/src/components/ForbiddenNotice.tsx:31`), 그 외 → `ErrorMessage`(trace_id, retryAfterSec 포함, `frontend/apps/web/src/components/ErrorMessage.tsx:27`). retry 버튼 클릭 시 `refetch()` 재시도. |
| `useResolveReconciliation` | `frontend/packages/shared-hooks/src/useReconciliation.ts:17` | `useMutation` — 사용자가 `<Button>`(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:91`) 클릭 시 수동 호출. `mutationFn`: POST `/reconciliation/resolve/:targetRef`(route: `reconciliation.resolve`, `frontend/packages/api-client/src/clients/reconciliation.ts:24`). `onSuccess`: `qc.invalidateQueries({ queryKey: RECONCILIATION_STATES_QUERY_KEY })` → `useReconciliationStates` 자동 refetch(`useReconciliation.ts:18-22`). | 페이지 59-62행(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:59-62`)에서 `onError: (err) => setActionError({ targetRef, error: err })` → `actionError` 상태(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:46`) 설정 → 101-105행(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:101-105`)에서 `<ReconciliationActionError error={actionError.error} />` 표시. 에러 분류는 `ReconciliationActionError` 컴포넌트(`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:25-38`)에서 동일 3-way 패턴(classifyBadRequest → BadRequestNotice, classifyForbidden → ForbiddenNotice, 그 외 → ErrorMessage). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J4-ReconciliationPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J4-ReconciliationPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J4-ReconciliationPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J4-ReconciliationPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:91`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J4-ReconciliationPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J4-ReconciliationPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:4`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:5`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:6`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:26`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:27`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/admin/ReconciliationPage.tsx:30`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J4-ReconciliationPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J4-ReconciliationPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J4-ReconciliationPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
