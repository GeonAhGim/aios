# J1-DashboardPage — `/dashboard`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/DashboardPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/dashboard` / `frontend/apps/web/src/routes/dashboard/DashboardPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-DashboardPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 개인 트레이더(투자자). 근거: `docs/frontend/PERSONAS_AND_JOURNEYS.md:22` 행 "P1 | 개인 트레이더(투자자) | 전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독 | J1~J6 전체 [확인]". §1 기준선 "개인 시스템 트레이더/리서처 1명" (§9 `docs/specs/UX_JOURNEYS.md` §0). 인용 `DashboardPage.tsx:49-51` 주석 "비관리자(§0 페르소나)"가 긴급 정지를 쓴다는 설명. |
| 목적(한 문장) | 대시보드에서 포트폴리오 요약·실행 중인 전략·최근 알림을 한눈에 보고, 필요하면 내 운용 전부를 즉시 정지하거나 재개한다. 근거: `DashboardPage.tsx:49-55`(긴급 정지 패널 설명), `DashboardPage.tsx:133-148`(데이터 훅 호출 — `useRiskProfile`, `usePortfolio`, `useExecutions`, `useNotificationHistory`), `facts/DashboardPage.facts.md` 화면 문구 목록("포트폴리오 요약", "실행 중인 전략", "최근 알림", "긴급 정지"). |
| 진입 조건 | `frontend/apps/web/src/router.tsx:114` `{ path: "/dashboard", element: protect(<DashboardPage />) }`. `protect` 함수(`router.tsx:70-72`)가 `<ProtectedRoute>`로 감쌈. `ProtectedRoute.tsx:20-21`: `token` 없으면 `/login?next=/dashboard`로 리다이렉트. `ProtectedRoute.tsx:29-30`: `me` 없으면 동일 리다이렉트. `ProtectedRoute.tsx:32-33`: `mfaEnabled` 없으면 `/onboarding/mfa-setup`로. `ProtectedRoute.tsx:35-40`: `mfaEnabled`이고 `riskProfile` 없으면 `/onboarding/risk-assessment`로. `/` 진입 시 `/dashboard`로 리다이렉트(`router.tsx:84`). `AdminRoute`는 이 라우트에 적용되지 않음(`protectAdmin`은 다른 라우트에서 쓰임, `router.tsx:74-80`). 온보딩 라우트(`router.tsx:87-89`)로의 강제 리다이렉트 조건은 `ProtectedRoute`에 따르므로 "미확인(ProtectedRoute 가드 참조)". |
| 사용자 준비물 | 로그인된 세션(인증 필요, `ProtectedRoute.tsx:20-21` 근거). 보유 운용(실행)이 있어야 긴급 정지·실행 중인 전략 카드가 채워진다(`DashboardPage.tsx:64` `RUNNING` 필터, `DashboardPage.tsx:114` 빈 상태 문구 "현재 실행 중인 운용이 없습니다."). 리스크 프로필(`useRiskProfile`, `DashboardPage.tsx:135`) 값이 있으면 "위험등급"이 표시됨(`facts/DashboardPage.facts.md` "위험등급 {{riskProfile}}", `DashboardPage.tsx:155`). 위험 평가 완료 여부가 이 화면의 전제 조건인지는 확인하지 못했으므로 "미확인". 빈 상태 시 온보딩 시작 버튼(`onboarding.emptyStateCta`, `DashboardPage.tsx:231`)은 `/onboarding/first-run`으로 이동(`DashboardPage.tsx:229`). |
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
