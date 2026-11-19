# J1-ExchangeManagementPage — `/exchanges`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ExchangeManagementPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/exchanges` / `frontend/apps/web/src/routes/exchanges/ExchangeManagementPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-ExchangeManagementPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:22`(P1 = 개인 트레이더(투자자), 핵심 과업: 전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독). 거래소 연동은 J1("시작·온보딩")의 선행 단계로 P1 전용 [확인] |
| 목적(한 문장) | 거래소 API 인증 정보를 등록·해지하고 연결된 거래소의 포트폴리오 현황을 확인하여 모의/실제 거래를 위한 데이터 경로를 확보한다 |
| 진입 조건 | `frontend/apps/web/src/router.tsx:115` — `/exchanges` 라우트. `protect()`(`router.tsx:70` → `ProtectedRoute`, `ProtectedRoute.tsx:9`) 감싸짐: (1) 인증 토큰 필요(`ProtectedRoute.tsx:20`), (2) `me` 클레임 존재(`ProtectedRoute.tsx:29`), (3) MFA 미활성 시 `/onboarding/mfa-setup`로 리다이렉트(`ProtectedRoute.tsx:32-34`), (4) MFA 활성화且 위험 프로필 미작성 시 `/onboarding/risk-assessment`로 리다이렉트(`ProtectedRoute.tsx:35-41`). 이 조건을 모두 통과한 인증 사용자만 접근 가능 [확인]. 기능 플래그 없음 [확인] |
| 사용자 준비물 | 로그인(토큰 확보), MFA 설정(또는 `/onboarding/mfa-setup` 완료), 위험 프로필 작성(MFA 활성화 시 필요, `ProtectedRoute.tsx:35-41`), 거래소 API 키/시크릿(또는 패스프레이즈 — 거래소별 상이, Bitget은 패스프레이즈 필요 `ExchangeManagementPage.tsx:82`) [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-ExchangeManagementPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useExchangeCredentials` | `frontend/packages/shared-hooks/src/useExchanges.ts:5` | TODO | TODO |
| `useRegisterExchangeCredential` | `frontend/packages/shared-hooks/src/useExchanges.ts:12` | TODO | TODO |
| `useRevokeExchangeCredential` | `frontend/packages/shared-hooks/src/useExchanges.ts:20` | TODO | TODO |
| `useExchangeBalance` | `frontend/packages/shared-hooks/src/useExchanges.ts:28` | TODO | TODO |
| `useFieldErrors` | `frontend/apps/web/src/hooks/useFieldErrors.ts:15` | TODO | TODO |
| `useConflictRetry` | `frontend/apps/web/src/hooks/useConflictRetry.ts:18` | TODO | TODO |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-ExchangeManagementPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-ExchangeManagementPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-ExchangeManagementPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-ExchangeManagementPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| TODO | | | |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-ExchangeManagementPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-ExchangeManagementPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| TODO | | |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-ExchangeManagementPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-ExchangeManagementPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-ExchangeManagementPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
