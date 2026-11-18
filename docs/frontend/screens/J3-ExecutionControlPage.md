# J3-ExecutionControlPage — `/executions`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ExecutionControlPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/executions` / `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-ExecutionControlPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독" (PERSONAS_AND_JOURNEYS.md §2) |
| 목적(한 문장) | 전략 ID, 버전, 배분 자본, 거래소, 모드(PAPER/LIVE)를 입력해 새 실행(execution)을 생성하고, 생성된 실행 목록을 확인·제어한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:127` — `/executions` 라우트는 `protect(<ExecutionControlPage />)`로 `ProtectedRoute`가 적용됨. 로그인한 사용자만 접근 가능. |
| 사용자 준비물 | 전략 ID(`label4`, `:245`), 전략 버전(`label5`, `:248`), 배분 자본 금액 USDT(`label6`, `:255`), 거래소 선택(`label7`, `:263`, 현재 bitget 고정), 모드 선택 PAPER/LIVE(`label8`, `:268`). LIVE 모드 선택 시 `LiveOrderConfirmDialog`(`:90`)에서 1회 확인 단계(`step: confirm`, `:170`)를 거침. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-ExecutionControlPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useExecutions` | `frontend/packages/shared-hooks/src/usePortfolio.ts:37` | TODO | TODO |
| `useCreateExecution` | `frontend/packages/shared-hooks/src/usePortfolio.ts:46` | TODO | TODO |
| `useEvaluateRiskGate` | `frontend/packages/shared-hooks/src/useRiskGate.ts:50` | TODO | TODO |
| `useIdempotentSubmit` | `frontend/apps/web/src/hooks/useIdempotentSubmit.ts:30` | TODO | TODO |
| `useConflictRetry` | `frontend/apps/web/src/hooks/useConflictRetry.ts:18` | TODO | TODO |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-ExecutionControlPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-ExecutionControlPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-ExecutionControlPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-ExecutionControlPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:134`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:136`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:279`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-ExecutionControlPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-ExecutionControlPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:4`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:5`) | TODO | TODO | TODO |
| `classifyServerError` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:6`) | TODO | TODO | TODO |
| `isResourceNotFound` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:7`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:8`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:11`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:44`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:45`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:49`) | TODO | TODO | TODO |
| `NotFoundState` (`frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx:66`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-ExecutionControlPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-ExecutionControlPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-ExecutionControlPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
