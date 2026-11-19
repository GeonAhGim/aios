# J1-DashboardPage — `/dashboard`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/DashboardPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/dashboard` / `frontend/apps/web/src/routes/dashboard/DashboardPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-DashboardPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 — 개인 트레이더(투자자). 여정 J1~J6 전체에 관여(MVP-1) [PERSONAS_AND_JOURNEYS.md §2] |
| 목적(한 문장) | 로그인 후 첫 진입 화면으로, 위험 등급·포트폴리오 요약·실행 중인 전략·긴급 정지·최근 알림을 한눈에 조망하여 운용 상태를 모니터링하고 필요시 즉시 개입한다. |
| 진입 조건 | `ProtectedRoute` 통과(인증 완료) — `router.tsx:114` `{ path: "/dashboard", element: protect(<DashboardPage />) }`. 루트 `/`에서 자동 리다이렉트 대상(`router.tsx:84`). |
| 사용자 준비물 | 계정 로그인 완료, 위험도 평가 완료(`useRiskProfile`가 `riskProfile` 값 반환, `useSuitability.ts:6`), 포트폴리오 존재(`usePortfolio`, `usePortfolio.ts:11`), 최소 1개 연결된 거래소 세션(실행 목록 조회용, `useExecutions`, `usePortfolio.ts:37`) |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-DashboardPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `usePauseExecution` | `frontend/packages/shared-hooks/src/usePortfolio.ts:111` | TODO | TODO |
| `useStartExecution` | `frontend/packages/shared-hooks/src/usePortfolio.ts:60` | TODO | TODO |
| `useRiskProfile` | `frontend/packages/shared-hooks/src/useSuitability.ts:6` | TODO | TODO |
| `usePortfolio` | `frontend/packages/shared-hooks/src/usePortfolio.ts:11` | TODO | TODO |
| `useExecutions` | `frontend/packages/shared-hooks/src/usePortfolio.ts:37` | TODO | TODO |
| `useNotificationHistory` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:45` | TODO | TODO |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-DashboardPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-DashboardPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-DashboardPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-DashboardPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:99`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:118`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:230`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-DashboardPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-DashboardPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:37`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:37`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:38`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/dashboard/DashboardPage.tsx:40`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-DashboardPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-DashboardPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-DashboardPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
