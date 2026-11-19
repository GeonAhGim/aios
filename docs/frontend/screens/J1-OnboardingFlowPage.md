# J1-OnboardingFlowPage — `/onboarding/first-run`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/OnboardingFlowPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/first-run` / `frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-OnboardingFlowPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더) — PERSONAS_AND_JOURNEYS.md §3: "시작·온보딩(J1) | P1" [확인] |
| 목적(한 문장) | 거래소 연결 → 전략 생성 → 페이퍼 실행의 3단계 온보딩 흐름에서 현재 진행 상황을 보여주고 다음 할 일을 안내하여 첫 실행 완주까지 유도 |
| 진입 조건 | `frontend/apps/web/src/router.tsx:89` — `/onboarding/first-run` 라우트. `protect()`로 ProtectedRoute 감싸짐: (1) 인증 토큰 필요, (2) MFA 활성화 필요(`/onboarding/mfa-setup` 통과 시), (3) 위험 프로필 작성 필요(`/onboarding/risk-assessment` 통과 시). 이 세 가드를 모두 통과한authenticated 사용자만 접근 가능 [확인] |
| 사용자 준비물 | 로그인, MFA 활성화, 위험 프로필 작성 완료(ProtectedRoute 가드 통과) [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-OnboardingFlowPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useExchangeCredentials` | `frontend/packages/shared-hooks/src/useExchanges.ts:5` | 1회 조회 (react-query default `staleTime: 0`, `refetchOnWindowFocus: true`). Mutation(`useRegisterExchangeCredential:16`, `useRevokeExchangeCredential:24`) 호출 시 `onSuccess` 콜백이 `qc.invalidateQueries({ queryKey: ["exchangeCredentials"] })`로 수동 무효화·재조회 (useExchanges.ts:16, 24) | react-query default `retry: 3`. 훅 내부 에러 처리 없음. `credentials.isError` → `deriveOnboardingProgress` (onboardingProgress.ts:65)에서 fail-closed(`false` 반환). 화면 에러 메시지·토스트·재시도 버튼 미표시 (OnboardingFlowPage.tsx:50-54) |
| `useMyStrategies` | `frontend/packages/shared-hooks/src/useStrategyBuilder.ts:14` | 1회 조회 (react-query default `staleTime: 0`, `refetchOnWindowFocus: true`). 전략 생성 Mutation(`useCreateStrategy:31`, `useGenerateWizardStrategy:51`, `useGenerateFromPrompt:57`)은 mutation 성공 시 `["myStrategies"]` 키 무효화 콜백이 없어 — 수동 갱신 필요 | react-query default `retry: 3`. 훅 내부 에러 처리 없음. 훅 반환값 `isError` (OnboardingFlowPage.tsx:53) → fail-closed. 화면 에러 메시지 미표시. 전략 생성 후 목록이 자동 갱신되지 않음 (gap) |
| `usePaperDeployments` | `frontend/packages/shared-hooks/src/usePaperControl.ts:7` | 1회 조회 (react-query default `staleTime: 0`, `refetchOnWindowFocus: true`). Mutation(`useRequestPaperDeployment:24`, `useDeploymentCommand:40`) 호출 시 `onSuccess` 콜백이 `qc.invalidateQueries({ queryKey: PAPER_DEPLOYMENTS_KEY })`로 수동 무효화·재조회 | react-query default `retry: 3`. 훅 내부 에러 처리 없음. 훅 반환값 `isError` (OnboardingFlowPage.tsx:54) → fail-closed. 화면 에러 메시지 미표시 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-OnboardingFlowPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-OnboardingFlowPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-OnboardingFlowPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-OnboardingFlowPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx:69`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx:95`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-OnboardingFlowPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-OnboardingFlowPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| TODO | | |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-OnboardingFlowPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-OnboardingFlowPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-OnboardingFlowPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
