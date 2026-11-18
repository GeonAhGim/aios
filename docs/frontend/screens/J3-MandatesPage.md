# J3-MandatesPage — `/mandates`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MandatesPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/mandates` / `frontend/apps/web/src/routes/mandates/MandatesPage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-MandatesPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — `PERSONAS_AND_JOURNEYS.md:22` "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독". 위임장은 P1이 자신의 주문 행위에 규칙을 부여하는 도구이다. [확인] |
| 목적(한 문장) | 활성 위임장 리비전의 한도 규칙을 확인하고, 초안 작성·개정안 제안·리비전 활성화로 규칙을 갱신하며, 위임장을 일시정지/재개하여 주문 경로를 제어한다. |
| 진입 조건 | `router.tsx:136` — `/mandates` 라우트. `protect(<MandatesPage />)` 호출은 `ProtectedRoute`로 감싸 로그인 세션(인증)을 요구한다. 기능 플래그 게이트 없음. [확인: `router.tsx:70-72` protect → ProtectedRoute] |
| 사용자 준비물 | 로그인 세션. 실주문을 위한 위임장 설정(초기에는 `activeRevision === null` → "위임장 미설정(주문 차단)" 안내, `MandatesPage.tsx:58`). 백테스트/검증 결과(선택, 규칙 설정의 참고 자료). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-MandatesPage:data -->
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

<!-- BLOCK:J3-MandatesPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-MandatesPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-MandatesPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-MandatesPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:66`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:70`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:169`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-MandatesPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-MandatesPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyStateConflict` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:116`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-MandatesPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-MandatesPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-MandatesPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
