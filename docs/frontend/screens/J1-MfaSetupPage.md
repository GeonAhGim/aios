# J1-MfaSetupPage — `/onboarding/mfa-setup`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MfaSetupPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/mfa-setup` / `frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-MfaSetupPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더). 근거: `docs/frontend/PERSONAS_AND_JOURNEYS.md:22`에 P1 핵심 과업이 "J1~J6 전체"로 명시. 가입 후 MFA 설정은 모든 페르소나 공통 진입이므로 P1로 기록. |
| 목적(한 문장) | 가입 직후 TOTP 인증 앱을 연결하고 6자리 코드로 검증해 계정의 2단계 인증을 켠다. 근거: `frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:34-35`(필수 게이트 주석), `:72`(검증 성공 시 `/onboarding/risk-assessment`로 이동). |
| 진입 조건 | 로그인 토큰 보유 + `me.mfaEnabled === false`. 근거: 라우트 `frontend/apps/web/src/router.tsx:87`, `protect()`로 감싸지며 `router.tsx:70-72` 기준 `ProtectedRoute`를 거친다. 기능 플래그 게이트 없음(플래그는 `router.tsx:93,101,109`의 connect/demo에만 존재). `frontend/apps/web/src/components/ProtectedRoute.tsx:20-22`: 토큰 없으면 `/login`으로 보냄. `ProtectedRoute.tsx:32-34`: `mfaEnabled` false이면 이 경로로 강제 리다이렉트. 가입 성공 후 진입은 `frontend/apps/web/src/routes/auth/SignupPage.tsx:64`. `frontend/apps/web/src/components/layout/navItems.ts:91-93`에 따라 상시 내비게이션 진입점이 아님. |
| 사용자 준비물 | TOTP 인증 앱(Google Authenticator 등, 근거 `MfaSetupPage.tsx:81` 부제 — 하드코딩 문구), 가입 직후의 로그인 세션, 인증 앱에 표시되는 6자리 코드. QR 스캔 불가 시 수동 입력용 코드 사용(`legacy.mfaSetupPage.t4`, `MfaSetupPage.tsx:95`). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-MfaSetupPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useSetupMfa` | `frontend/packages/shared-hooks/src/useAuth.ts:66` | TODO | TODO |
| `useVerifyMfa` | `frontend/packages/shared-hooks/src/useAuth.ts:70` | TODO | TODO |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-MfaSetupPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-MfaSetupPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-MfaSetupPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-MfaSetupPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:109`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-MfaSetupPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-MfaSetupPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:21`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:21`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:22`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:22`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:23`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:25`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-MfaSetupPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-MfaSetupPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-MfaSetupPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
