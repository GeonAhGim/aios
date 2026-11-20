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
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J4-SafetyControlsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J4-SafetyControlsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J4-SafetyControlsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:138`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:179`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:230`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:233`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:305`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:331`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J4-SafetyControlsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J4-SafetyControlsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:35`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:35`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:36`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/admin/SafetyControlsPage.tsx:38`) | TODO | TODO | TODO |
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
