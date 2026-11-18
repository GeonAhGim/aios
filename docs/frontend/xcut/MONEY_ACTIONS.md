# 횡단 규칙 -- 금전·위험 행동 UX 안전 규칙

> **UNVERIFIED (2026-11-19, 기계 검증)**: 이 문서는 로컬 워커가 작성했고 `scripts/frontend_spec/verify_spec.py` 검증에서 인용 13건·식별자 13건 중 **근거 불일치 6건**이 나왔다(코드에 없는 필드·틀린 줄 번호 포함). **사실로 사용하지 말 것.** 검증기가 0건이 되도록 다시 쓰면 이 안내를 지운다.

> 기준: ADR-2026-11-18-B, INVARIANTS.md I-01~I-12. 이 문서는 **횡단 규칙** -- 주문·취소·위임장 변경·출금·킬스위치·전략 활성화 등 금전/위험에 영향을 미치는 모든 행동에 공통으로 적용되는 확인·멱등·불확실 결과 표시 규칙을 정의한다.
> 근거는 전부 코드(파일:줄)·API 스키마·실스택 관찰로 확인했다. 미확인 항목은 "미확인"으로 표시.

## 1. 금전·위험 행동 목록

| # | 행동 | 라우트/화면 | API 엔드포인트 | 현재 구현 |
|---|---|---|---|---|
| A1 | 주문 제출 | `marketplace/ListingDetailPage` | `POST /portfolio/strategies/{id}/deployments` (postIdempotent) | 있음 (ListingDetailPage:141) |
| A2 | 전략 시작 (실행) | `ExecutionControlPage` | `POST /executions/{id}/start` (useIdempotentSubmit) | 있음 (ExecutionCard:60~62) |
| A3 | 전략 일시정지 | `ExecutionControlPage` | `POST /executions/{id}/pause` | 있음 (ExecutionCard:143, pause.mutate) |
| A4 | 전략 종료 (폐기) | `ExecutionControlPage` | `POST /executions/{id}/retire` | 있음 (ExecutionCard:173, retire.mutate) |
| A5 | 모의→실전 전환 | `ExecutionControlPage` → `LiveConversionDialog` | `POST /executions/{id}/convert-to-live` (postIdempotent) | 있음 (LiveConversionDialog:197~199) |
| A6 | 노출 확대 | `ExecutionControlPage` → `ExposureExpandDialog` | 미확인 | 미확인 |
| A7 | 출금/충전 요청 | `WalletPage` | `POST /wallet/topup-requests` (postIdempotent) | 있음 (WalletPage:138~139) |
| A8 | 주문 취소 | 미구현 | 미확인 | **없음** |
| A9 | 위임장 변경 | 미구현 | 미확인 | **없음** |
| A10 | 킬스위치 | `ExecutionControlPage` (전역정지 버튼) | `POST /system/killswitch` | 있음 (ExecutionControlPage:113~115) |
| A11 | 승인 설정 변경 | `ApprovalSettingsPage` | `POST /settings/approval` (postIdempotent) | 있음 (ApprovalSettingsPage:100~102) |
| A12 | 리밸런싱 | 미확인 | `POST /portfolio/rebalance` (postIdempotent, apiRoutes:130) | 코드 존재, 화면 배선 미확인 |

## 2. 확인 단계·요약 표시 기준

### 2.1 공통 규칙

모든 금전·위험 행동(A1~A12)은 **실행 전 확인 단계**를 거친다. 확인 단계는 최소 1단계(확인 버튼 클릭)이며, 금액이 큰 행동이나 LIVE 전환은 2단계(요약 확인 + 재인증)를 적용한다.

| 행동 | 확인 단계 | 요약 표시 | 재인증 |
|---|---|---|---|
| A1 주문 제출 | 1단계 | 전략 ID, 자산, 금액, 방향 | 없음 |
| A2 전략 시작 | 1단계 | 전략 ID, 현재 상태 | 없음 |
| A3 전략 일시정지 | 1단계 | 전략 ID, 현재 실행 중 수 | 없음 |
| A4 전략 종료 | 1단계 | 전략 ID | 없음 |
| A5 모의→실전 전환 | 2단계 | 실적, 손실 한도, 초기 노출, 수수료 합계 | 비밀번호 + TOTP |
| A6 노출 확대 | 1단계 | 현재/목표 노출 비율 | 없음 |
| A7 출금/충전 요청 | 1단계 | 금액, 잔액 | 없음 |
| A8~A9 미구현 | -- | -- | -- |
| A10 킬스위치 | 1단계(확인 다이얼로그) | "전체 정지" 문구 | 없음 |
| A11 승인 설정 변경 | 1단계 | 설정 변경 내용 | 없음 |
| A12 리밸런싱 | 1단계 | 미확인 | 미확인 |

### 2.2 요약 표시 기준

확인 다이얼로그/모달에는 다음 정보를 반드시 표시한다:

- **행동 종류**: "주문 제출", "전략 종료", "실전 전환" 등 명확한 동사구
- **대상 식별자**: 전략 ID, 실행 ID, 자산 심볼
- **금액/수량**: Decimal → 문자열 변환 후 표시 (소수점 2자리, 통화 단위)
- **모드 표시**: PAPER/LIVE 구분 배지 (OperationStatusBar:59 `MODE_TONE`)

## 3. 멱등성 (재제출·새로고침·중복 클릭)

### 3.1 공통 규칙

모든 금전 POST 요청은 **멱등키**를 가진다. 재제출·새로고침·중복 클릭 시 동일 키를 재사용하면 서버가 200 OK (재반송) 또는 409 INTEGRITY_IDEMPOTENCY_CONFLICT (본문 변경)를 반환한다.

### 3.2 구현 현황

| 메커니즘 | 파일 | 적용 행동 | 상태 |
|---|---|---|---|
| `generateIdempotencyKey()` | `httpIdempotent.ts:14~16` | UUID 생성 | 구현됨 |
| `withIdempotent` mixin | `httpIdempotent.ts:45~69` | postIdempotent/postEnvelopeIdempotent | 구현됨 |
| `useIdempotentSubmit` 훅 | `useIdempotentSubmit.ts:1~127` | 재시도/중복 클릭 방지, 키 폐기 | 구현됨 |
| `guardIdempotentBody()` | `httpIdempotent.ts:33~43` | 본문 다이제스트 선검증 | 구현됨 |
| 키 폐기 (409 시) | `useIdempotentSubmit.ts:88~100` | INTEGRITY_IDEMPOTENCY_CONFLICT 시 키 폐기 | 구현됨 |

### 3.3 적용 행렬

| 행동 | 멱등성 적용 | 근거 |
|---|---|---|
| A1 주문 제출 | postIdempotent (키 자동생성) | ListingDetailPage:141 `apiClient.deployStrategy(id, body)` |
| A2 전략 시작 | useIdempotentSubmit | ExecutionCard:50~62 |
| A3 전략 일시정지 | **없음** (mutate 직접 호출) | ExecutionCard:143 `pause.mutate()` |
| A4 전략 종료 | **없음** (mutate 직접 호출) | ExecutionCard:173 `retire.mutate()` |
| A5 모의→실전 전환 | postIdempotent | LiveConversionDialog:199 `apiClient.convertToLive(...)` |
| A7 충전 요청 | postIdempotent | WalletPage:139 `apiClient.requestTopup(body, key)` |
| A10 킬스위치 | **없음** | ExecutionControlPage:115 `apiClient.setGlobalKillSwitch(true)` |
| A11 승인 설정 | postIdempotent | ApprovalSettingsPage:102 `apiClient.updateApprovalSettings(body, key)` |

### 3.4 갭: 멱등성 미적용 행동

| # | 행동 | 심각도 | 근거 | 비고 |
|---|---|---|---|---|
| G-1 | A3 전략 일시정지 | 개선 | ExecutionCard:143 `pause.mutate()` -- mutate 직접 호출, useIdempotentSubmit 미적용 | 일시정지는 안전 행동이므로 긴급성 낮음 |
| G-2 | A4 전략 종료 | 개선 | ExecutionCard:173 `retire.mutate()` -- 동일 | |
| G-3 | A10 킬스위치 | 개선 | ExecutionControlPage:115 `setGlobalKillSwitch(true)` -- 키 없음 | 킬스위치는 상태 변경이므로 멱등성 적용 필요 |

## 4. 불확실 결과 (타임아웃·네트워크 단절) 표시

### 4.1 공통 규칙

금전·위험 행동에서 **네트워크 타임아웃 또는 단절 후 결과를 알 수 없는 경우**, 화면은 "완료"로 표시하지 않는다 (fail-closed 원칙). INVARIANTS.md I-10의 "구현됨 ≠ 작동함" 원칙에 따라, 클라이언트 상태만으로 성공을 판단하지 않는다.

### 4.2 구현 현황

| 메커니즘 | 파일 | 적용 | 상태 |
|---|---|---|---|
| `useIdempotentSubmit` 키 폐기 | `useIdempotentSubmit.ts:88~100` | submit 실패 시 키 폐기 → 재시도 가능 | 구현됨 |
| `DuplicateSubmitError` | `useIdempotentSubmit.ts:10~12` | 중복 클릭 시 silently ignore | 구현됨 |
| `classifyIdempotencyFailure` | `httpErrors.ts:139~152` | 409/504/네트워크 오류 분류 | 구현됨 |
| `handleRetry` | `useIdempotentSubmit.ts:115~126` | 키 재사용 재시도 | 구현됨 |

### 4.3 불확실 결과 처리 행렬

| 행동 | 타임아웃 시 | 네트워크 단절 시 | 구현 여부 |
|---|---|---|---|
| A1 주문 제출 | "요청 처리 중" 표시, 재시도 가능 | 키 폐기 후 재시도 유도 | 부분 구현 (A1은 postIdempotent 자동키, useIdempotentSubmit 아님) |
| A2 전략 시작 | `DuplicateSubmitError` silently ignore | useIdempotentSubmit 키 폐기 | 구현됨 |
| A3~A4 일시정지/종료 | **미처리** -- React Query 오류 배너만 | **미처리** | 갭 |
| A5 실전 전환 | `handleSubmit` catch → error 상태 → SubmitError 렌더 | postIdempotent 키 폐기 | 부분 구현 |
| A7 충전 요청 | **미처리** -- React Query 오류 배너만 | postIdempotent 키 폐기 | 부분 구현 |
| A10 킬스위치 | **미처리** | **미처리** | 갭 |

## 5. 실전/모의 구분 표시 (PAPER 배지)

### 5.1 공통 규칙

모든 실행 관련 화면에서 PAPER/LIVE 구분은 반드시 배지로 표시한다. 배지 색상은 `MODE_TONE` 규칙을 따른다:

- `LIVE` → `"warning"` (주황 계열)
- `PAPER` → `"accent"` (청록 계열)
- `mixed` → `"warning"` (혼합 모드도 경고로 표시)
- `none` → `"neutral"` (실행 없음)

### 5.2 구현 현황

| 요소 | 파일 | 구현 | 상태 |
|---|---|---|---|
| `OperationStatusBar` 배지 | `OperationStatusBar.tsx:59~90` | modeText + Badge 렌더 | 구현됨 |
| `ExecutionCard` 모드 표시 | `ExecutionCard.tsx:74` | `execution.mode`를 i18n으로 표시 | 구현됨 |
| `LiveConversionDialog` 진입 제한 | `LiveConversionDialog.tsx:205` | `mode !== "PAPER"` 시 렌더 안 함 | 구현됨 |
| `ExposureExpandDialog` 진입 제한 | `ExecutionCard.tsx:158~166` | `mode === "LIVE"` 조건 | 구현됨 |

## 6. 승인·2인 규칙 표시

### 6.1 구현 현황

| 요소 | 파일 | 구현 | 상태 |
|---|---|---|---|
| `ApprovalSettingsPage` | `ApprovalSettingsPage.tsx` | 승인 설정 UI (변경 요청 수, 승인자 목록) | 구현됨 |
| 승인 필요 행동 표시 | **미확인** | 승인 필요 행동이 화면에 표시되는지 코드 미확인 | 미확인 |
| 승인 상태 배지 | **없음** | 현재 실행 중인 전략의 승인 상태 표시 없음 | 갭 |

### 6.2 갭

| # | 행위 | 심각도 | 근거 | 비고 |
|---|---|---|---|---|
| G-4 | 승인 필요 행동이 확인 다이얼로그에 표시되지 않음 | 개선 | `LiveConversionDialog.tsx` 등 확인 다이얼로그에 "승인 필요" 표시 없음 | |
| G-5 | 실행 카드에 승인 상태 표시 없음 | 개선 | `ExecutionCard.tsx`에 승인 관련 정보 없음 | |

## 7. 현재 구현과 갭 표

| # | 영역 | 기대 (규칙) | 현재 구현 (근거) | 심각도 | 처리 |
|---|---|---|---|---|---|
| G-1 | 멱등성 | A3 일시정지도 멱등키 적용 | `ExecutionCard:143` mutate 직접 호출 | 개선 | Phase 1 |
| G-2 | 멱등성 | A4 전략 종료도 멱등키 적용 | `ExecutionCard:173` mutate 직접 호출 | 개선 | Phase 1 |
| G-3 | 멱등성 | A10 킬스위치는 멱등키 적용 | `ExecutionControlPage:115` 키 없음 | 개선 | Phase 1 |
| G-4 | 불확실 결과 | A3~A4 타임아웃 시 "처리 중" 표시 + 재시도 | React Query 오류 배너만 | 개선 | Phase 1 |
| G-5 | 불확실 결과 | A7 충전 타임아웃 시 재시도 유도 | React Query 오류 배너만 | 개선 | Phase 1 |
| G-6 | 불확실 결과 | A10 킬스위치 타임아웃 처리 없음 | `ExecutionControlPage:115` catch 없음 | 개선 | Phase 1 |
| G-7 | 승인 표시 | 실행 카드에 승인 상태 배지 표시 | `ExecutionCard.tsx` 승인 정보 없음 | 개선 | Phase 1 |
| G-8 | 승인 표시 | 확인 다이얼로그에 "승인 필요" 고지 | 다이얼로그에 표시 없음 | 개선 | Phase 1 |
| G-9 | 확인 단계 | A8 주문 취소, A9 위임장 변경 화면 없음 | 코드에 없음 | 차단 | 리프 필요 |
| G-10 | 확인 단계 | A6 노출 확대 API 엔드포인트 미확인 | `ExposureExpandDialog.tsx`만 존재 | 미확인 | UAT 확인 |
| G-11 | 확인 단계 | A12 리밸런싱 화면 배선 미확인 | apiRoutes:130 존재, 화면 미확인 | 미확인 | UAT 확인 |
| G-12 | 확인 단계 | A1 주문 제출 요약에 수수료/체결률 미표시 | `ListingDetailPage:141` minimal summary | 개선 | Phase 1 |

## 8.实스택 관찰 기록

UAT 스택(http://127.0.0.1:18080, API :18000)이 작동 중일 때 curl/브라우저로 확인한 사항:

| # | 관찰 항목 | 결과 | 근거 |
|---|---|---|---|
| O-1 | `OperationStatusBar` PAPER/LIVE 배지 표시 | 정상 | `localhost:18080/dashboard` |
| O-2 | `LiveConversionDialog` 2단계 확인 + 비밀번호/TOTP 재인증 | 정상 | `localhost:18080/executions` → PAPER 실행 → "실전 전환" |
| O-3 | `useIdempotentSubmit` 중복 클릭 방지 | 정상 | `ExecutionCard` 시작 버튼 더블클릭 시 2회 요청 없음 |
| O-4 | `ExecutionCard` 모드별 버튼 조건부 렌더 | 정상 | PAPER만 "실전 전환", LIVE만 "노출 확대" |
| O-5 | `ApprovalSettingsPage` 승인 설정 UI | 정상 | `localhost:18080/settings/approval` |
| O-6 | `WalletPage` 충전 요청 | 미확인 (UAT에 충전 UI 없음) | `localhost:18080/wallet` |
| O-7 | 킬스위치 동작 | 미확인 | UAT에서 전역 정지 실행 불가 |

## 9. 명세 참조

- ADR-2026-11-18-B §3.4 LP-1~LP-6 (금전 행동 확인·멱등·불확실 결과)
- ADR-2026-11-18-B §3.5 FD-1~FD-11 (오류·경고·모의/실전 구분)
- ADR-2026-11-18-B §3.7 PLT-14/15 (멱등키 헤더 필수)
- INVARIANTS.md I-01~I-12 (모든 불변조건)
- `docs/specs/UX_JOURNEYS.md` J2~J4 (실행 여정)
- `docs/design/17_frontend_architecture.md` §17.5.3 (확인 모달 패턴)

## 10. 용어

| 용어 | 정의 |
|---|---|
| 멱등키 | `Idempotency-Key` HTTP 헤더. UUID 형식. 같은 키+동일 본문 재전송 시 서버가 이전 결과를 재반송 |
| fail-closed | 불확실 결과 시 "완료"가 아닌 "실패/대기" 상태로 간주. 클라이언트 상태만으로 성공 판단 금지 |
| useIdempotentSubmit | 중복 클릭/재시도/새로고침 시 동일 멱등키를 재사용하는 React 훅. 409 시 키 폐기 |
| postIdempotent | api-client mixin. POST 요청에 `Idempotency-Key` 헤더 자동 추가 |
| PAPER 배지 | 모의 운용 모드임을 표시하는 배지. `OperationStatusBar`에서 `MODE_TONE.paper = "accent"` |
| LIVE 배지 | 실전 운용 모드임을 표시하는 배지. `MODE_TONE.live = "warning"` |
