# J3-MandatePage — `/compliance/mandate`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MandatePage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/compliance/mandate` / `frontend/apps/web/src/routes/compliance/MandatePage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-MandatePage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더/투자자) — J3 여정(모의 주문)의 컴플라이언스 설정 단계. P5(자산운용사)도 고객별 위임장 설정에 사용. P11(감사자·컴플라이언스)은 읽기 전용 참조. [PERSONAS_AND_JOURNEYS.md §2] |
| 목적(한 문장) | 테넌트별 위임장(노출·한도·자산 금지 규칙)을 조회·편집·승인하고, 초안/개정안 제출과 리비전 활성화·일시정지·재개로 자동 주문 필터를 관리한다. [MandatePage.tsx:46~201] |
| 진입 조건 | `router.tsx:138` 경로 `/compliance/mandate` 접근. `protect()` 호출이 `ProtectedRoute`로 인증된 세션만 통과시킨다. feature flag 없음. [router.tsx:70~72, 138] |
| 사용자 준비물 | 로그인 세션, 테넌트 컨텍스트(위임장은 테넌트 단위, GET /mandates/status가 테넌트당 active/pending 리비전 각 최대 1건만 반환한다). P5는 고객/펀드/포트폴리오 맥락 선택 완료. [MandatePage.tsx:18~22] |
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
