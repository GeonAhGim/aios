# J3-MandatePage — `/compliance/mandate`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MandatePage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/compliance/mandate` / `frontend/apps/web/src/routes/compliance/MandatePage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-MandatePage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:22` J1~J6 전체 확인, §3: J1~J6 P1 중심. `MandatePage.tsx:173-175` 주석의 '작성자=승인자 400' 규칙(CM-5)과 `MandateActionError` 경로로 볼 때 승인 단계에 제3자가 개입할 수 있으나 보조 페르소나 여부는 미확인 |
| 목적(한 문장) | 컴플라이언스 위임장 규칙(노출 한도·손실 한도·자율성 등)을 초안 작성·개정 제출·활성화·일시정지·재개하고 활성/대기 리비전을 확인한다 (`MandatePage.tsx:18-22` 상단 주석) |
| 진입 조건 | 로그인 필요 — `router.tsx:138` `protect(<MandatePage />)` → `router.tsx:70-72` `<ProtectedRoute>` 감싸는 `protect()` 함수. `ProtectedRoute.tsx:20-21` 토큰 없으면 `/login` 리다이렉트, `ProtectedRoute.tsx:32-33` MFA 미설정 시 `/onboarding/mfa-setup` 리다이렉트, `ProtectedRoute.tsx:35-40` 위험 성향 미설정 시 `/onboarding/risk-assessment` 리다이렉트. 플래그 가드 없음(`router.tsx:93·101·109` 다른 라우트에만 `FeatureFlagGate` 존재). 추가 역할 가드 없음(확인된 범위 안에서). 로그인 후 `AppShell` 렌더 (`MandatePage.tsx:111`) |
| 사용자 준비물 | 로그인 세션 필요, MFA 활성화 필요 (`ProtectedRoute.tsx:32`), 위험 성향 평가 완료 필요 (`ProtectedRoute.tsx:35`), 위임장 규칙 입력값: `MandateRuleForm` 컴포넌트(`frontend/apps/web/src/routes/mandates/MandateRuleForm.tsx:12-20`) — maxTotalExposurePct(총 노출 한도 %), maxSingleInstrumentPct(단일 종목 한도 %), minCashBufferPct(최소 현금 버퍼 %), maxDailyLossPct(일일 손실 한도 %), allowedAutonomy(OBSERVE/PAPER/LIMITED_LIVE), forbiddenAssets(금지 자산 쉼표 구분), 실패 시 '미확인'. 기존 활성 리비전 유무에 따라 초안 작성 또는 개정안 제안 중 하나 (`MandatePage.tsx:61`). 활성화는 작성자가 아닌 다른 사용자가 해야 함 (`MandatePage.tsx:173-175` 주석, CM-5 400 계약 인용, 실제 서버 검사 미확인) |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-MandatePage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useMandateStatus` | `frontend/packages/shared-hooks/src/useMandates.ts:10` | TODO | TODO |
| `useCreateMandateDraft` | `frontend/packages/shared-hooks/src/useMandates.ts:17` | TODO | TODO |
| `useProposeMandateAmendment` | `frontend/packages/shared-hooks/src/useMandates.ts:25` | TODO | TODO |
| `useActivateMandateRevision` | `frontend/packages/shared-hooks/src/useMandates.ts:33` | TODO | TODO |
| `usePauseMandate` | `frontend/packages/shared-hooks/src/useMandates.ts:42` | TODO | TODO |
| `useResumeMandate` | `frontend/packages/shared-hooks/src/useMandates.ts:50` | TODO | TODO |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-MandatePage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-MandatePage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-MandatePage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-MandatePage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:135`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:146`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:169`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-MandatePage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-MandatePage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyStateConflict` (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:74`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-MandatePage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-MandatePage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-MandatePage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
