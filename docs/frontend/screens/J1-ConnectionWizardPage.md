# J1-ConnectionWizardPage — `/onboarding/connect`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ConnectionWizardPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/connect` / `frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-ConnectionWizardPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더). 근거 `PERSONAS_AND_JOURNEYS.md:40` — 시작·온보딩(J1) 주 페르소나는 P1만 있음. |
| 목적(한 문장) | 공급자 코드와 계정 키로 공급자 계정을 연결하고, 읽기 전용 권한(READ_BALANCE/READ_POSITION/READ_ACTIVITY) 중 선택해 연결 요청을 보내는 3단계 마법사 (`ConnectionWizardPage.tsx:12-14,96-198`). 성공 시 "연결 요청을 보냈습니다. 설정 > 연결에서 상태를 확인하세요." (`:87`). |
| 진입 조건 | `router.tsx:91-97` — `/onboarding/connect`는 `protect()`(`router.tsx:70-72`, `ProtectedRoute`로 로그인·MFA·적합성평가 필수)와 `FeatureFlagGate flag="onboarding_connection_wizard"`로 감겨 있음. `FeatureFlagGate` 구현 확인: 플래그 꺼짐 시 `EmptyState` + `common.featureDisabled` 표시 (`FeatureFlagGate.tsx:16-17`). `navItems.ts:14`에 메뉴 링크 있으나 메뉴 노출이 플래그에 묶였는지는 미확인. |
| 사용자 준비물 | 공급자 코드(자유 입력, `ConnectionWizardPage.tsx:99-100`, 목록 선택 없음), 공급자가 발급한 계정 키(opaque_account_ref, `ConnectionWizardPage.tsx:102-110`, password 타입 입력), 요청할 권한 1개 이상(`READ_BALANCE`/`READ_POSITION`/`READ_ACTIVITY`, `:12, :56`). 키 발급 절차와 MFA 필요 여부는 미확인. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-ConnectionWizardPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useBeginConnection` | `frontend/packages/shared-hooks/src/useConnections.ts:20` | TODO | TODO |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-ConnectionWizardPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-ConnectionWizardPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-ConnectionWizardPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-ConnectionWizardPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:89`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:131`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:159`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:162`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:190`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:193`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-ConnectionWizardPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-ConnectionWizardPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:20`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:20`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:21`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:23`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/onboarding/ConnectionWizardPage.tsx:86`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-ConnectionWizardPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-ConnectionWizardPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-ConnectionWizardPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
