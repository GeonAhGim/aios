# 08. 테스트 계획 — v1.2

> **v1.1(2026-08-10) = "0번부터 재검토" 라운드 — 번호 충돌 정정.** 이 문서의
> §8.1~8.7이 정책문서(docx) 8장(Trading Core, 8.1~8.10 — 이 프로젝트에서
> 가장 자주 인용되는 장)과 번호가 겹치는 것을 발견 — 04/10/13/15/16/17번과
> 동일 라운드에 동일 조치. 모든 최상위 헤더를 "§8.X"로 전면 변경, 정책조항
> 인용은 "정책문서" 명시.
> **v1.2(2026-08-10)**: §8.3-A/B(FD-11~21 테스트케이스) 실제 병합 완료 —
> 이전 라운드에 patch-08-test-plan-fd11-21.md로만 존재하고 본문 미반영이었던
> 것을 "산출물 최종 점검" 단계(01/06번과 동일 문제 재발견)에서 완결.

> 근거: 06_mvp_scope.md §6.3 Definition of Done을 실제 테스트 케이스로 변환

## §8.1 테스트 피라미드

```
        ▲
       /E2E\          Bitget Demo 계정 실제 왕복 (수동 실행, CI 상시 아님)
      /------\
     /통합테스트\      Event Bus pub/sub, DB read/write, Adapter+Mock 조합
    /----------\
   /   단위테스트   \  데이터 모델 검증, 개별 함수 로직
  /--------------\
```

## §8.2 단위 테스트 (`tests/unit/`)

| 대상 | 테스트 케이스 |
|---|---|
| `AIOSTask` | 필수 필드 누락 시 ValidationError, `required_permission_level` 0~6 범위 검증, 기본값(status=PENDING) 확인 |
| `FSMStrategyConfig` | `initial_state`가 `states`에 없으면 실패(Validator 책임), 모든 state가 최소 1개 transition 보유 검증 |
| `Order` | `price=None` + `order_type=LIMIT` 조합 거부(Validator), `client_order_id` 중복 생성 방지 로직 |
| `Validator.validate_order_params` | tick_size 배수 아닌 가격 거부, 수량<=0 거부 |
| `Validator.validate_strategy_config` | FSM 무결성 위반 케이스 5종 이상(고아 state, 자기순환 등) |
| `Parser` | 거래소별 Raw 응답 샘플(fixture) → 정확한 Pydantic 모델 변환 확인 |
| `risk_policy.yaml 로더` | 스키마 위반 시 로딩 실패, 정상 파일 로딩 시 모든 Draft 필드 접근 가능 |

## §8.3 통합 테스트 (`tests/integration/`)

| 대상 | 테스트 케이스 |
|---|---|
| `InProcessEventBus` | publish→subscribe 정상 전달, handler 예외 시 다른 handler 영향 없음(log_and_continue), 큐 백프레셔(§8.6 하단 참조) |
| `MockBithumbAdapter`(보류 중이나 코드 존재) | 시드 잔고 기준 매수/매도 시뮬레이션, 존재하지 않는 심볼 요청 시 명확한 에러 |
| `BitgetAdapter` (Demo 계정) | 시장가/지정가 주문 생성→상태조회→취소 전체 왕복, `client_order_id` 재전송 시 중복 주문 안 만들어짐(멱등성, 7.5) |
| `KISAdapter` (모의투자) | 시세조회·잔고조회까지만(매매 제외, 06번 스콥 원칙) |
| DB (`orders`, `audit_log`) | `audit_log`에 `UPDATE`/`DELETE` 시도 시 권한 오류 확인(WORM 강제, 16.3) |
| `Reconciliation` | 내부 상태 vs Mock 거래소 상태 불일치 주입 → `reconciliation_events` 기록 확인 |

## §8.3-A 멀티테넌시·마켓플레이스 통합 테스트 (v1.1 병합 — "0번부터 재검토" 라운드에서
발견: patch-08-test-plan-fd11-21.md로만 존재하고 본문 미반영이었던 것 완결)

| 대상 | 테스트 케이스 |
|---|---|
| `ExchangeCredentialService.get_adapter_for_user` | **서로 다른 두 사용자가 동시에 각자의 Bitget 키로 조회했을 때 키가 섞이지 않음**(정책문서 4.10 멀티테넌시 격리 원칙의 핵심 테스트 — FD-12.2 완료조건 재확인) |
| `MarketplaceService.verify` | 검증담당자가 본인 리스팅을 검증 시도 시 거부(이해상충 규칙, 15번 문서 §15.6) |
| `MarketplaceService.purchase` | 자전거래(seller==buyer) 거부, 동일 Idempotency-Key 재요청 시 중복 구매 미생성 |
| `ExecutionService.start` | Watchdog/Circuit Breaker가 PAUSED 상태로 만든 실행에 대해 사용자 시작 시도 시 거부(정책문서 8.6-B 우선순위 실증) |
| `SuitabilityService` | 재평가로 등급이 나빠졌을 때 기존 RUNNING 실행에 즉시 경고 발생(FD-15.2 예외상황) |
| `NotificationGateway` | 강제 채널(human_approval.requested 등) 발송 실패 시 CRITICAL 재시도(5회) 후 audit_log 기록 |
| `AdminService.confirm_payment` | PENDING_PAYMENT 상태에서는 FD-13.4 실행권한 미부여, CONFIRMED 전환 후에만 부여 |
| `disputes`/`reviews` UNIQUE 제약 | 동일 구매건 중복 분쟁 접수 거부(부분유니크인덱스), 1구매 1리뷰 제약 |

## §8.3-B 프론트엔드 계약 테스트 (v1.1 병합)

| 대상 | 테스트 케이스 |
|---|---|
| `packages/shared-types` | 16번 Pydantic 모델과 필드명 1:1 대응 자동 검증(CI에서 OpenAPI 스펙과 TS 타입 diff, 착수 시 도구 확정) |
| `CamelModel` 직렬화 | API 응답이 실제로 camelCase로 나가는지(snake_case 노출 시 프론트 타입 불일치 재발 방지 — 재점검 라운드에서 이 문제를 한 번 발견했던 이력) |

## §8.4 End-to-End (`tests/e2e/`, 수동/저빈도 실행)

- Bitget Demo 계정으로: 심볼 1개 선택 → 시세 구독 → 지정가 매수 주문 → 체결 대기 → 포지션 조회 → 매도 청산까지 전체 흐름 1회 수동 실행 및 로그 확인.
- 이 테스트는 CI에 상시 포함하지 않는다(외부 API 의존, Rate Limit 소모) — 릴리스 전 체크리스트 항목으로 별도 관리.

## §8.5 Watchdog·지연 벤치마크 (06번 Definition of Done 연동)

- `정책문서 8.6-A-1-1 Watchdog 오탐 검증 시뮬레이터`: 별도 스크립트(`scripts/watchdog_simulator.py`, Draft)로 과거 변동성 데이터 재생 — 단위/통합 테스트와 별개 카테고리로 관리.
- `8.2-D 지연 벤치마크`: `pytest-benchmark` 등으로 종단간 지연 측정, 결과를 `docs/benchmarks/`에 기록(ADR 아님, 별도 이력).

## §8.6 Event Bus 백프레셔 정책 (Draft — 05번 문서 보강)

> 레드팀 재검토(아래 §9) 결과 반영 — 05번 문서에는 없던 내용

- `InProcessEventBus`의 topic별 큐는 무제한이 아니다 — Draft 최대 깊이 1000.
- 초과 시 정책: 가장 오래된 메시지 삭제(drop-oldest)가 아니라 **신규 publish를 거부하고 WARNING 로그** — 오래된 시세/주문상태를 버리는 것이 더 위험할 수 있어 보수적으로 설계.
- 큐가 지속적으로 가득 차면(Draft: 1분 이상) `market.distrust.entered`류로 격상해 상위 계층에 알린다.

## §8.7 CI 파이프라인 (Draft 개요)

```yaml
# .github/workflows/ci.yml 개요 (실제 파일은 착수 시 작성)
on: [pull_request]
jobs:
  test:
    steps:
      - 15.6-A .aios-zone 매니페스트 검증 (FROZEN 경로 변경 시 자동 거부)
      - 16.5 의존성 Allowlist 검사
      - 단위 테스트 (§8.2)
      - 통합 테스트 (§8.3, Mock/Demo만 — 실계좌 접근 없음)
      - 멀티테넌시·마켓플레이스 통합 테스트 (§8.3-A, v1.1 병합)
      - 프론트엔드 계약 테스트 (§8.3-B, v1.1 병합)
      - SAST/Secret 스캔 (15.7)
```
