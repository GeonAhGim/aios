# J6-AccountDeletionPage — `/settings/account`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/AccountDeletionPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/settings/account` / `frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-AccountDeletionPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 — 개인 트레이더(투자자): `docs/frontend/PERSONAS_AND_JOURNEYS.md` §2. P1의 핵심 과업은 "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독"이며, J6 설정·승인·감사 여정은 P1(+P2)이 담당한다. |
| 목적(한 문장) | 인증된 사용자가 자신의 계정 탈퇴를 요청하고, 위기 상황에 대비해 비상 출금 목적지 화이트리스트를 사전 등록한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:150` — `/settings/account` 경로. `protect()` 함수(`router.tsx:70-72`)가 `<ProtectedRoute>`로 래핑하여 인증된 사용자만 접근 가능하게 한다. |
| 사용자 준비물 | 계정 비밀번호 — 탈퇴 요청 시 `label13` 필드(비밀번호 확인, `AccountDeletionPage.tsx:165`)에 입력해야 하며, WRONG_PASSWORD 응답(400) 시 요청이 거부된다. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-AccountDeletionPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useWhitelistEntries` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:24` | TODO | TODO |
| `useRegisterWhitelistEntry` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:31` | TODO | TODO |
| `useRequestAccountDeletion` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:39` | TODO | TODO |
| `useLogout` | `frontend/packages/shared-hooks/src/useAuth.ts:49` | TODO | TODO |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-AccountDeletionPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-AccountDeletionPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-AccountDeletionPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-AccountDeletionPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:141`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:151`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:175`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-AccountDeletionPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-AccountDeletionPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:26`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:26`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:27`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:27`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:28`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:30`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/settings/AccountDeletionPage.tsx:150`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-AccountDeletionPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-AccountDeletionPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-AccountDeletionPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
