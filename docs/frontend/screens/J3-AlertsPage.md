# J3-AlertsPage — `/alerts`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/AlertsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/alerts` / `frontend/apps/web/src/routes/alerts/AlertsPage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-AlertsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:22`: "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독" |
| 목적(한 문장) | 사용자가 거래소·종목·지표·조건·임계값을 설정해 가격/지표 알림을 생성·관리하고, 발동된 알림을 확인·취소한다 |
| 진입 조건 | `frontend/apps/web/src/router.tsx:144` `/alerts` 경로 접근; `protect()`(`router.tsx:70-72`)가 `ProtectedRoute`(`ProtectedRoute.tsx:9`)로 감싸서 (1) 인증 토큰 존재(`ProtectedRoute.tsx:20`), (2) `useMe()`为用户 정보 존재(`ProtectedRoute.tsx:29`), (3) MFA 활성화 시 MFA 설정 완료(`ProtectedRoute.tsx:32`), (4) MFA 활성화 시 위험도 평가 완료(`ProtectedRoute.tsx:35`) 가드 통과 |
| 사용자 준비물 | 로그인 세션, MFA 설정 완료, 위험도 평가 완료 |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-AlertsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useMyAlerts` | `frontend/packages/shared-hooks/src/useAlerts.ts:5` | TODO | TODO |
| `useIndicators` | `frontend/packages/shared-hooks/src/useStrategyBuilder.ts:10` | TODO | TODO |
| `useCreateAlert` | `frontend/packages/shared-hooks/src/useAlerts.ts:12` | TODO | TODO |
| `useCancelAlert` | `frontend/packages/shared-hooks/src/useAlerts.ts:20` | TODO | TODO |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-AlertsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-AlertsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-AlertsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-AlertsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:222`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:263`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-AlertsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-AlertsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:35`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:35`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:36`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:36`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:37`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/alerts/AlertsPage.tsx:39`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-AlertsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-AlertsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-AlertsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
