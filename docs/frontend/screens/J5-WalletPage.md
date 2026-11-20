# J5-WalletPage — `/wallet`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/WalletPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/wallet` / `frontend/apps/web/src/routes/wallet/WalletPage.tsx`
- 여정 소속: J5


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J5-WalletPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:22` 표 P1 행("J1~J6 전체 [확인]"). 운영자 측 충전 승인(`/admin/wallet-topups`)은 P8 몫(`router.tsx:161`). |
| 목적(한 문장) | 보유 크레딧을 확인하고 충전 금액을 신청하면, 입금 후 관리자 확인으로 크레딧이 반영된다. (`WalletPage.tsx:48-51` 잔액 표시, `:58-84` 신청 폼) |
| 진입 조건 | 1) 라우트 가드: `router.tsx:141`이 `protect(<WalletPage />)`로 `/wallet`를 감싼다. `protect`는 `router.tsx:70-72`에서 `ProtectedRoute`로 감김. 2) 플래그 조건: 없음. 3) 토큰 없으면 `/login?next=...`로 리다이렉트: `ProtectedRoute.tsx:20-21`. 4) MFA 미완료 시 `/onboarding/mfa-setup`로 강제: `ProtectedRoute.tsx:32-34`. 5) 적합성평가 미완료 시 `/onboarding/risk-assessment`로 강제: `ProtectedRoute.tsx:35-41`. |
| 사용자 준비물 | 1) 로그인 세션(토큰 보유). 2) MFA 설정 완료(`ProtectedRoute.tsx:32`). 3) 적합성평가 완료(`ProtectedRoute.tsx:35`). 4) 충전 금액: 정수, 최소 1(`WalletPage.tsx:62`), 기본값 30000(`WalletPage.tsx:16`). 1크레딧 = 1원(`WalletPage.tsx:58`). 5) 실제 계좌 입금: 문구 `WalletPage.tsx:69`("신청 후 실제 계좌로 입금하면 관리자 확인 즉시 크레딧이 반영됩니다"). 입금 계좌 정보 출처: 미확인. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J5-WalletPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useWalletBalance` | `frontend/packages/shared-hooks/src/useWallet.ts:5` | TODO | TODO |
| `useRequestTopup` | `frontend/packages/shared-hooks/src/useWallet.ts:12` | TODO | TODO |
| `useIdempotentSubmit` | `frontend/apps/web/src/hooks/useIdempotentSubmit.ts:30` | TODO | TODO |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J5-WalletPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J5-WalletPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J5-WalletPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-WalletPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/wallet/WalletPage.tsx:83`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-WalletPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-WalletPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`frontend/apps/web/src/routes/wallet/WalletPage.tsx:42`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/wallet/WalletPage.tsx:71`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/wallet/WalletPage.tsx:80`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-WalletPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-WalletPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-WalletPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
