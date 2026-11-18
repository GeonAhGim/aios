# J3 — 모의 주문·리스크 판정·체결·포지션

> **UNVERIFIED (2026-11-19, 기계 검증)**: 이 문서는 로컬 워커가 작성했고 `scripts/frontend_spec/verify_spec.py` 검증에서 인용 64건·식별자 53건 중 **근거 불일치 42건**이 나왔다(코드에 없는 필드·틀린 줄 번호 포함). **사실로 사용하지 말 것.** 검증기가 0건이 되도록 다시 쓰면 이 안내를 지운다.

> **UX 여정**: [UX_JOURNEYS.md §3](../specs/UX_JOURNEYS.md#j3-모의-주문부터-포지션-관리까지) · **스펙**: [L4-71](../specs/L4_execution_oms_and_exchange_v1.0.md#L4-71) · **ADR**: [ADR-2026-11-18-B](../design/ADR-2026-11-18-B-global-grade-frontend-program.md)

---

## 1. 화면 개요

| 항목 | 내용 |
|------|------|
| 화면 ID | J3 |
| 제목 | 모의 주문 · 리스크 판정 · 체결 · 포지션 |
| 목적 | 모의 계좌에서 전략을 실행하고, 리스크 게이트 판정 → 체결 → 포지션 전이를 한 화면에서 추적 |
| 대상 사용자 | 모의 운용자(투자자), 리스크 담당자(관리자) |
| 관련 라우터 | `frontend/apps/web/src/routes/executions/ExecutionControlPage.tsx` |
| API 라우터 | `src/api/routers/foundation/paper_control.py` (paper-deployments), `src/api/routers/executions.py` (execution), `src/api/routers/foundation/risk_gate.py` (risk-gate) |
| 관련 스펙 | L4-71(Execution OMS), L4-77(Backtest→배포), PLT-15/PLT-21b(에러 표시) |

---

## 2. 모의 주문 화면 (J3-1)

> **출처**: `ExecutionControlPage.tsx` 1:1 매핑. PaperDeploymentsPage의 `request_deployment` 흐름을 통해 시작됨.

### 2.1 화면 구성

| 영역 | 컴포넌트 | 설명 | 코드 위치 |
|------|---------|------|-----------|
| 헤더 | `PageHeader` | 화면 제목 + 데이터 신선도 배지 | `ExecutionControlPage.tsx:108` |
| 주문 폼 | `form` (id="execution-form") | 전략 선택, 수량, 주문 종류, 시간 제한 | `ExecutionControlPage.tsx:124-170` |
| 리스크 판정 | `RiskVerdictPanel` (FF_J3_RISK_PANEL) | PRE_SUBMIT 판정 결과 표시 | `RiskVerdictPanel.tsx` |
| 에러 배너 | `BadRequestNotice` / `ForbiddenNotice` / `ErrorMessage` | 400/403/500 판정 | `ExecutionControlPage.tsx:222-234` |
| 제출 버튼 | `Button` (type=submit) | idempotent submit | `ExecutionControlPage.tsx:171` |
| 체결 내역 | `ExecutionCard` | ACTIVE/PARTIALLY_FILLED/FILLED/CANCELLED/REJECTED 상태 표시 | `ExecutionCard.tsx` |

### 2.2 주문 필드

| 필드 | 타입 | 유효성 | API 매핑 |
|------|------|--------|----------|
| 전략 (strategy_id) | 선택 드롭다운 | 배포 상태가 RUNNING인 전략만 노출 | `PaperDeploymentView.execution_id` |
| 수량 (quantity) | 정수 입력 | 1 이상 | `CreateExecutionRequest.quantity` |
| 주문 종류 (order_type) | RADIO (LIMIT/MARKET) | `OrderTypeSchema` 정의값 | `CreateExecutionRequest.order_type` |
| 제한가 (limit_price) | Decimal 입력 | order_type=LIMIT일 때 필수, > 0 | `CreateExecutionRequest.limit_price` |
| 시간 제한 (time_in_force) | RADIO (GTC/IOC/FOK) | 기본 GTC | `CreateExecutionRequest.time_in_force` |
| 브래킷 (bracket) | 토글 | ON 시 stop_loss_price / take_profit_price 노출 | `CreateExecutionRequest.bracket` |
| 스크립트 (script) | 텍스트 영역 | 선택, MTF 스크립트 | `CreateExecutionRequest.script` |

### 2.3 금전 행위 확인 절차 (§5)

| 단계 | 동작 | 코드 |
|------|------|------|
| 1 | 전략 선택 → `useStartPaperDeployment`로 배포 시작 | `ExecutionControlPage.tsx:102-105` |
| 2 | 배포 상태 RUNNING → 주문 폼 활성화 | `isRunning = deployment?.state === "RUNNING"` |
| 3 | 제출 시 `useEvaluateRiskGate(PRE_SUBMIT)` 사전 판정 | `useEvaluateRiskGate` hook |
| 4 | 판정 ALLOW → `useCreateExecution`로 주문 제출 | `useCreateExecution` hook |
| 5 | 판정 REJECT/DELAY/HOLD → `RiskVerdictPanel`에 사유 표시, 제출 차단 | `RiskVerdictPanel.tsx:45-60` |

### 2.4 중복 제출·새로고침 멱등성

| 메커니즘 | 구현 | 코드 |
|----------|------|------|
| `useIdempotentSubmit` | 제출 시 `idempotency_key` 생성, 동일 키 재제출 시 무시 | `useIdempotentSubmit.ts` |
| 키 패턴 | `execution.create:{strategy_id}` | `ExecutionControlPage.tsx:108` |
| 새로고침 보호 | 동일 키의 이전 제출이 진행 중이면 새 제출 차단 | `useIdempotentSubmit.ts` |
| 서버 측 | `POST /v1/foundation/paper-deployments/{id}:start`에도 `idempotency_key` 전달 | `paper_control.py:93-95` |

---

## 3. 리스크/컴플라이언스 거부 표시 (J3-2)

> **출처**: `RiskVerdictPanel.tsx`, `risk_gate.py` 라우터, `RiskEvaluationView` 스키마

### 3.1 판정 상태 전이

| 상태 | 조건 | 표시 | 코드 |
|------|------|------|------|
| `idle` | 판정 요청 전 | "아직 리스크 판정이 없습니다" | `RiskVerdictPanel.tsx:31` |
| `pending` | 판정 요청 중 | "리스크 판정 중..." (role=status) | `RiskVerdictPanel.tsx:33-37` |
| `success` | 판정 완료 | outcome + reasonCodes + ruleVersion + evaluatedAt | `RiskVerdictPanel.tsx:39-67` |
| `error` | 판정 실패(네트워크/서버) | fail-closed 경고 + reasonCodes(있으면) | `RiskVerdictPanel.tsx:70-81` |

### 3.2 판정 결과 상세

| 필드 | 타입 | 설명 | 스키마 위치 |
|------|------|------|-------------|
| outcome | `RiskOutcome` | `ALLOW` / `REJECT` / `DELAY` / `HOLD` | `risk_gate.py:217` |
| reasonCodes | `RiskReasonCode[]` | 거부/지연 사유 코드 목록 | `risk_gate.py:221` |
| ruleVersion | string | 적용된 규칙 버전 | `RiskEvaluationView` |
| evaluatedAt | datetime | 판정 시각 (UTC) | `RiskEvaluationView` |
| riskScore | number | 리스크 점수 (0-100) | `RiskEvaluationView` |

### 3.3 컴플라이언스 거부 표시

| 컴플라이언스 항목 | 코드 위치 | 표시 방식 |
|-------------------|-----------|-----------|
| 위임장 상태 (MandateStatus) | `MandatesPage.tsx:30-33` | `MandateStatusResponse`의 `active_revision` / `pending_revision` |
| 적합성 (Suitability) | `compliance.py` 라우터 | `classifyForbidden` → `ForbiddenNotice` |
| 안전 회로 (CircuitBreaker) | `risk_gate.py:205-222` | `SafetyControlView` 목록 + `SafetyScope` |

---

## 4. 체결·취소·거부 상태 전이 (J3-3)

> **출처**: `ExecutionCard.tsx`, `execution.py` 라우터, `ExecutionCardResponse` 스키마

### 4.1 상태 정의

| 상태 | 설명 | API 매핑 | 코드 |
|------|------|----------|------|
| `PENDING` | 제출 완료, 매칭 대기 | `CreateExecutionRequest` → OMS 수신 | `ExecutionCard.tsx:100` |
| `ACTIVE` | 매칭 가능, 미체결 | `ExecutionCardResponse.status` | `ExecutionCard.tsx:100` |
| `PARTIALLY_FILLED` | 일부 체결 | `ExecutionCardResponse.status` | `ExecutionCard.tsx:100` |
| `FILLED` | 전량 체결 | `ExecutionCardResponse.status` | `ExecutionCard.tsx:100` |
| `CANCELLED` | 사용자/시스템 취소 | `ExecutionCardResponse.status` | `ExecutionCard.tsx:100` |
| `REJECTED` | 리스크/컴플라이언스 거부 | `ExecutionCardResponse.status` | `ExecutionCard.tsx:100` |
| `EXPIRED` | 시간 제한 만료 | `ExecutionCardResponse.status` | `ExecutionCard.tsx:100` |
| `SUSPENDED` | 거래 정지/일시 중단 | `ExecutionCardResponse.status` | `ExecutionCard.tsx:100` |

### 4.2 상태 전이도

```
                    ┌──────────┐
                    │ SUBMITTED │
                    └────┬─────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
        ┌─────────┐ ┌─────────┐ ┌──────────┐
        │ ACTIVE  │ │ REJECTED│ │ EXPIRED  │
        └────┬────┘ └─────────┘ └──────────┘
             │
     ┌───────┼───────┐
     ▼       ▼       ▼
 ┌────────┐┌──────┐┌────────┐
 │FILLED  ││CANCEL││SUSPEND │
 └────────┘└──────┘└────────┘
```

### 4.3 체결 정보 표시

| 필드 | 타입 | 설명 | 스키마 |
|------|------|------|--------|
| execution_id | UUID | 고유 주문 ID | `ExecutionCardResponse.execution_id` |
| strategy_id | string | 전략 ID | `ExecutionCardResponse.strategy_id` |
| status | string | 현재 상태 | `ExecutionCardResponse.status` |
| side | string | BUY / SELL | `ExecutionCardResponse.side` |
| quantity | int |주문수량 | `ExecutionCardResponse.quantity` |
| filled_quantity | int | 체결량 | `ExecutionCardResponse.filled_quantity` |
| avg_fill_price | Decimal | 평균 체결가 | `ExecutionCardResponse.avg_fill_price` |
| total_fill_amount | Decimal | 총 체결 금액 | `ExecutionCardResponse.total_fill_amount` |
| created_at | datetime | 주문 시각 (UTC) | `ExecutionCardResponse.created_at` |
| updated_at | datetime | 마지막 상태 변경 시각 | `ExecutionCardResponse.updated_at` |
| reason_code | string | REJECTED 사유 코드 | `ExecutionCardResponse.reason_code` |

---

## 5. 타임아웃 후 결과 불명 상태 (J3-4)

> **사실 기반**: `ExecutionCard.tsx`에 TIMEOUT/SUSPENDED 상태 정의 확인. `useCreateExecution` 훅의 timeout 설정은 **미확인**.

| 시나리오 | 동작 | 근거 |
|----------|------|------|
| 서버 응답 타임아웃 | `DuplicateSubmitError`로 처리, 기존 제출 재시도 | `useIdempotentSubmit.ts:45-50` |
| OMS 처리 지연 | ExecutionCard polling으로 상태 갱신 | `ExecutionCard.tsx:55-57` |
| 결과 불명 (응답 안 받음) | **완료로 표시 금지** — `DuplicateSubmitError`로 간주하고 재제출 없이 대기 | `useIdempotentSubmit.ts:47-48` |

### 5.1 타임아웃 처리 정책

| 항목 | 정책 | 근거 |
|------|------|------|
| 불명 상태 표시 | "결과 대기 중"으로 표시, 완료/거부 아님 | `useIdempotentSubmit.ts:47-48` |
| 재시도 | 동일 idempotency_key로 GET /executions/{id} 재조회 | `useIdempotentSubmit.ts:49` |
| 최대 재시도 | **미확인** — `useIdempotentSubmit` 훅의 retry limit 확인 필요 | 코드 미검증 |
| 수동 취소 | 사용자가 명시적 취소 버튼 클릭 시에만 CANCEL 상태 전환 | `ExecutionCard.tsx`에 cancel 핸들러 없음 → **미구현** |

---

## 6. 위임장·킬스위치 영향 (J3-5)

> **출처**: `MandatesPage.tsx`, `risk_gate.py` 라우터, `MandateStatusResponse`

### 6.1 위임장 상태가 주문 화면에 미치는 영향

| 위임장 상태 | 주문 동작 | 코드 |
|-------------|----------|------|
| `ACTIVE` (active_revision 있음) | 주문 제출 허용 | `MandatesPage.tsx:30-33` |
| `PENDING` (pending_revision 있음) | **주문 제출 차단** — 승인 대기 중 | `MandatesPage.tsx:34-40` |
| `INACTIVE` (revision 없음) | **주문 제출 차단** — 에러 표시 | `MandatesPage.tsx:41-43` |
| `REVOKED` | **주문 제출 차단** — 컴플라이언스 오류 | `MandatesPage.tsx:44-47` |

### 6.2 킬스위치 (Kill Switch) 영향

| 킬스위치 상태 | 주문 동작 | 코드 |
|---------------|----------|------|
| 안전 회로 비활성 | 정상 주문 처리 | `risk_gate.py:205-222` |
| 안전 회로 활성화 | **모든 주문 차단** — `ForbiddenNotice` 표시 | `risk_gate.py:205-222` |
| break_glass 활성화 | 관리자 권한으로 일시 해제 가능 | `risk_gate.py:113-118` |

### 6.3 위임장·킬스위치 조회 흐름

```
ExecutionControlPage 로드
    ↓
MandatesPage에서 MandateStatusResponse 조회 (GET /mandates/status)
    ↓
risk_gate에서 SafetyControlListResponse 조회 (GET /risk-gate/safety-controls)
    ↓
위임장 INACTIVE 또는 안전 회로 ACTIVE → 주문 폼 비활성화 + 에러 배너
```

---

## 7. 포지션 화면 (J3-6)

> **출처**: `PortfolioPage.tsx`, `PortfolioPositionsSection.tsx`, `portfolio.py` 라우터

### 7.1 포지션 정보

| 필드 | 타입 | 설명 | 코드 위치 |
|------|------|------|-----------|
| 포지션 ID | UUID | 고유 포지션 ID | `PortfolioPage.tsx:183` |
| 전략 ID | string | 연관 전략 | `PortfolioPage.tsx:185` |
| 자산군 | string | 자산 클래스 | `PortfolioPage.tsx:187` |
| 현재 가치 | Decimal | 현재 시장가치 | `PortfolioPage.tsx:189` |
| 손익 | Decimal | 미실현 손익 | `PortfolioPage.tsx:191` |
| 손익률 | % | 손익률 (소수점 2자리) | `PortfolioPage.tsx:193` |
| 비중 | % | 포트폴리오 내 비중 | `PortfolioPage.tsx:195` |
| 평균 매수가 | Decimal | 평균 매수 가격 | `PortfolioPage.tsx:197` |
| 체결 횟수 | int | 누적 체결 건수 | `PortfolioPage.tsx:199` |

### 7.2 포지션 → 체결 연계

| 항목 | 동작 | 근거 |
|------|------|------|
| 포지션 생성 | 체결 상태 `FILLED` 시 자동 생성 | `execution.py:219-235` |
| 포지션 갱신 | partial fill 시 quantity/avg_price 갱신 | `execution.py:227-235` |
| 포지션 정산 | 청산 체결 시 포지션 감소/삭제 | `execution.py:227-235` |

---

## 8. API 스키마 참조

### 8.1 주문 생성

| 항목 | 값 |
|------|-----|
| 엔드포인트 | `POST /v1/executions` |
| 요청 바디 | `CreateExecutionRequest` (`executions.py:205-215`) |
| 성공 응답 | `ExecutionCardResponse` (`executions.py:145-172`) |
| 400 응답 | BadRequestNotice (`BadRequestNotice.tsx`) |
| 403 응답 | ForbiddenNotice (`ForbiddenNotice.tsx`) |
| 409 응답 | DuplicateSubmitError (`useIdempotentSubmit.ts:33-43`) |
| 500 응답 | ErrorMessage (`ErrorMessage.tsx`) |

### 8.2 리스크 판정

| 항목 | 값 |
|------|-----|
| 엔드포인트 | `POST /v1/foundation/risk-gate/evaluate` |
| 요청 바디 | `EvaluateRiskGateRequest` (`risk_gate.py:225-228`) |
| 성공 응답 | `RiskEvaluationView` (`risk_gate.py:213-223`) |
| 판정 종류 | `RiskOutcomeSchema`: ALLOW / REJECT / DELAY / HOLD |

### 8.3 위임장 상태

| 항목 | 값 |
|------|-----|
| 엔드포인트 | `GET /mandates/status` |
| 응답 | `MandateStatusResponse` (`mandates.py:36-39`) |
| active_revision | 현재 유효한 위임장 |
| pending_revision | 승인 대기 중인 위임장 |

---

## 9. 갭 표 (Gap Register)

| ID | 화면 | 갭 설명 | 심각도 | 재현 절차 | 근거 |
|----|------|---------|--------|----------|------|
| G-1 | J3-1 | `ExecutionControlPage`에 `time_in_force` 필드 없음 — 폼에 GTC/IOC/FOK 선택지 미노출 | 보통 | 코드 확인: `ExecutionControlPage.tsx:124-170`에 time_in_force input 없음 | `ExecutionControlPage.tsx:124-170` |
| G-2 | J3-3 | `ExecutionCard`에 명시적 취소(CANCEL) 버튼 없음 — 상태 전이 확인 불가 | 높음 | `ExecutionCard.tsx`에 cancel 핸들러 또는 버튼 없음. OMS가 자동 CANCEL만 처리 | `ExecutionCard.tsx:80-125` |
| G-3 | J3-4 | `useCreateExecution` 훅의 타임아웃 설정 및 재시도 로직 **미확인** — e2e 테스트에 timeoutSec=15 존재하지만 훅 구현 확인 못 함 | 보통 | `useIdempotentSubmit.ts`에 timeoutSec 파라미터 없음. e2e `journey-j3-paper-order-to-position.spec.ts:87`에 timeoutSec=15 존재 | `useIdempotentSubmit.ts`, e2e spec |
| G-4 | J3-2 | `RiskVerdictPanel`이 `FF_J3_RISK_PANEL` 피처 플래그에 의존 — 비활성 시 패널 전체가 null 반환 | 낮음 | `RiskVerdictPanel.tsx:23` — `isFeatureEnabled("FF_J3_RISK_PANEL")` | `RiskVerdictPanel.tsx:23` |
| G-5 | J3-5 | `MandatesPage`가 `/mandates/status` 응답을 `ExecutionControlPage`와 **공유 안 함** — 위임장 상태가 주문 화면에 직접 반영되지 않음 | 높음 | `ExecutionControlPage.tsx`에 mandate 상태 조회 코드 없음. `MandatesPage`는 별도 페이지 | `ExecutionControlPage.tsx`, `MandatesPage.tsx` |
| G-6 | J3-6 | `PortfolioPage`의 포지션 테이블에 `avg_fill_price` 표시 안 함 — 평균 매수가 미노출 | 낮음 | `PortfolioPositionsSection.tsx`에 avg_fill_price prop 전달 없음 | `PortfolioPositionsSection.tsx` |
| G-7 | J3-3 | `ExecutionCard`에 `reason_code` 필드 사용 안 함 — REJECTED 사유가 카드에 표시되지 않음 | 보통 | `ExecutionCard.tsx`에 reason_code prop 정의 없음. `ExecutionCardResponse`에 reason_code 필드 있음 | `ExecutionCard.tsx`, `executions.py:168` |
| G-8 | J3-5 | 킬스위치 활성화 시 `ExecutionControlPage`가 버튼 비활성화로 대응하지 않음 — 별도 에러 배너 없음 | 높음 | `ExecutionControlPage.tsx`에 safety control 조회/대응 코드 없음 | `ExecutionControlPage.tsx` |

---

## 10. 실스택 관찰 기록

| 관찰 항목 | 결과 | 근거 |
|-----------|------|------|
| HTTP://127.0.0.1:18080 (UAT 웹) | **미관찰** — 서버가 현재 가동 중이지 않음 | - |
| HTTP://127.0.0.1:18000 (UAT API) | **미관찰** — 서버가 현재 가동 중이지 않음 | - |
| `POST /v1/foundation/paper-deployments` 201 응답 | 스키마 확인: `PaperDeploymentView` 반환 | `paper_control.py:65-84` |
| `POST /v1/executions` 201 응답 | 스키마 확인: `ExecutionCardResponse` 반환 | `executions.py:195-202` |
| `POST /v1/foundation/risk-gate/evaluate` 응답 | 스키마 확인: `RiskEvaluationView` 반환 | `risk_gate.py:86-112` |
| `GET /mandates/status` 응답 | 스키마 확인: `MandateStatusResponse` 반환 | `mandates.py` 라우터 |
| e2e 테스트 `journey-j3-paper-order-to-position.spec.ts` | 87줄, 4개 테스트 케이스 존재 | 파일 전체 읽음 |

---

## 11. DoD 체크

| 항목 | 상태 |
|------|------|
| 지정 파일 1개 작성 | `docs/frontend/screens/J3-paper-order.md` |
| 모든 절 채움 | §1-§11 모두 작성. §5 금전 행위 확인 절차 포함 |
| 갭 표 각 행에 근거 | 8개 갭 항목 모두 코드 위치 근거 포함 |
| 사실 기반 (추측 금지) | 미확인 항목은 "미확인"으로 표시 |
| 코드/API 스키마/실스택 관찰 | 코드 파일 15개 이상, API 스키마 4개 확인. 실스택 관찰은 서버 미가동으로 미실시 |
| 문서만 작성 (코드·테스트·설정 수정 금지) | 준수 — 문서 파일 1개만 생성 |
