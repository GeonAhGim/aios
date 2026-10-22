# 파일 범위 분할 제안: task-16648 / task-18495

## 원본 작업 분석

- **task-16648**: QA(묶음 1건) — CI complexity 정정
- **task-18495**: ratchet 초과 파일 분할 — code_ratchets 게이트 `loc_over_500` 18개→37개 증가 대응
- **원본 files**: `["src/"]` — 전체 소스 디렉토리 (너무 광범위)
- **문제**: CI complexity 단계에서 `over_cap_count 9개 -> 10개 (증가, CAP=25)` 보고 + `loc_over_500` 18개→37개 증가
- **현재 상태**: 로컬 게이트 녹색 (complexity는 이전 커밋 `7740471bd`에서 이미 정정됨)

## 실제 게이트 대상 파일

### complexity 게이트 (task-16648)

`check_complexity.py`가 스캔하는 대상:
1. `src/` 내 모든 `.py` 파일 (1569개 파일, 10개 함수가 CAP=25 초과)
2. `scripts/` 내 모든 `.py` 파일

현재 CAP=25 초과 함수 9개:
| 파일 | 함수 | 복잡도 |
|------|------|--------|
| `src/core/script/import_/pine/lexer.py` | `tokenize()` | 74 |
| `src/core/script/grammar/lexer.py` | `tokenize()` | 64 |
| `src/foundation/backtest/application/quick_backtest.py` | `run_quick_backtest()` | 63 |
| `scripts/consistency/wiring.py` | `check_port_protocol_implementations()` | 46 |
| `scripts/check_zone_manifest.py` | `main()` | 30 |
| `src/core/indicators/engine/__init__.py` | `run_chain_incremental()` | 27 |
| `src/foundation/backtest/application/run_backtest.py` | `run_backtest()` | 27 |
| `src/foundation/mandates/application/activate_revision.py` | `activate_revision()` | 27 |
| `src/foundation/market_data/domain/instruments/roll.py` | `continuous_futures()` | 27 |

### loc_over_500 게이트 (task-18495) — 37개 파일

baseline: 18개 → 현재: 37개 (증가 19개)

#### tests/integration/foundation/ledger/ — 7개 파일 (가장 큰 군)

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_purchase_flow.py` | 757 | purchase/hold/capture/release (21개 테스트) |
| `test_refund.py` | 667 | refund case R1/R2/R3, duplicate (24개 테스트) |
| `test_chargeback.py` | 629 | chargeback full/shortfall/duplicate/digest/freeze (22개 테스트) |
| `test_backfill.py` | 591 | backfill operations |
| `test_post_entry.py` | 573 | post entry |
| `test_ibor_abor.py` | 519 | IBOR/ABOR |

**분할안**: `test_purchase_flow.py` → `test_purchase_hold.py`(purchase+hold), `test_capture_release.py`(capture+release), `test_purchase_budget.py`(budget). `test_refund.py` → `test_refund_cases.py`(R1-R3), `test_refund_dedup.py`(duplicate). `test_chargeback.py` → `test_chargeback_coverage.py`(full/shortfall), `test_chargeback_edge.py`(duplicate/digest/freeze/rollback).

#### tests/integration/foundation/market_data/ — 4개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_dc13_hot_postgres_deepen_2886.py` | 518 | DC13 hot postgres |
| `test_dc8_coverage_entitlements_deepen_2884.py` | 546 | DC8 coverage |
| `test_hot_postgres.py` | 529 | hot postgres |

#### tests/integration/foundation/entities/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_postgres_entity_repository.py` | 617 | entity repository CRUD |

#### tests/integration/oms/ — 3개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_bitget_private_ws_to_inbox.py` | 560 | Bitget WebSocket |
| `test_cancel_modify_deepen.py` | 589 | cancel/modify |
| `test_restart_recovery.py` | 516 | restart/recovery |

#### tests/integration/ — 3개 파일 (루트 테스트)

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_auth_router.py` | 594 | auth router |
| `test_execution_scheduler.py` | 522 | execution scheduler |
| `test_order_service.py` | 559 | order service |

#### tests/integration/risk/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_intraday_monitor.py` | 688 | intraday risk monitor (31개 테스트) |

#### tests/integration/exchange/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_kis_durability.py` | 552 | KIS durability |

#### tests/integration/db/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_migration_tenant_membership.py` | 561 | migration tenant |

#### tests/integration/db_schema/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_market_data.py` | 522 | market data schema |

#### tests/integration/platform/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_break_glass.py` | 538 | break glass |

#### tests/foundation/integration/risk_gate/ — 2개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_recovery_gate.py` | 678 | recovery gate (31개 테스트) |
| `test_risk_gate_lifecycle.py` | 638 | risk gate lifecycle |

#### tests/foundation/integration/market_data/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_instruments_schema_deepen_2879.py` | 595 | instruments schema |

#### tests/foundation/unit/ — 2개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_begin_connection_application.py` | 585 | connection application |
| `test_provider_spi_deepen_2885.py` | 556 | provider SPI |

#### tests/unit/core/portfolio/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_mandate_binding.py` | 511 | mandate binding |

#### tests/unit/foundation/ — 6개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_decomposition.py` | 502 | EMS decomposition |
| `test_market_abuse.py` | 522 | market abuse |
| `test_l2_ingest_session_deepen_2902.py` | 554 | L2 ingest session |
| `test_ingest_job.py` | 513 | research data ingest |
| `test_preview_order.py` | 544 | preview order |

#### tests/unit/oms/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_observability_deepen.py` | 591 | OMS observability |

#### tests/unit/scripts/ — 2개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_canary_bitget.py` | 555 | canary bitget |
| `test_check_audit_regressions.py` | 546 | audit regressions |

#### tests/adversarial/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_rule_purity.py` | 580 | rule purity |

## 분할 우선순위

### 우선순위 1: ledger 테스트 (가장 큰 영향)
- `test_purchase_flow.py` (757줄) → 3개 파일로 분할
- `test_refund.py` (667줄) → 2개 파일로 분할
- `test_chargeback.py` (629줄) → 2개 파일로 분할

### 우선순위 2: risk_gate + risk 테스트
- `test_recovery_gate.py` (678줄) → gate lifecycle vs evidence 평가로 분할
- `test_risk_gate_lifecycle.py` (638줄) → gate lifecycle로 분할
- `test_intraday_monitor.py` (688줄) → monitor vs signal로 분할

### 우선순위 3: oms + auth 테스트
- `test_auth_router.py` (594줄) → auth lifecycle vs session으로 분할
- `test_cancel_modify_deepen.py` (589줄) → cancel vs modify로 분할
- `test_observability_deepen.py` (591줄) → observability metrics로 분할

### 우선순위 4: 나머지 파일 (각각 1개 리프)
- 나머지 20개 파일은 각각 1개 리프로 분할 제안

## 권장

**우선순위 1 → 2 → 3 → 4 순서**로 권장. ledger 테스트가 가장 많은 파일(7개)과 최대 LOC(757줄)를 차지하므로 먼저 처리해야 전체 37개 중 19개(51%)를 해소할 수 있음.

## 비고

- 현재 CI complexity는 녹색(9=9). 이전 회귀(`7740471bd`)가 이미 정정됨.
- task-16648의 실제 목적(회귀 정정)은 이미 완료되었으므로, 새로운 분할 작업은 **예방적 복잡도 개선**으로 재정의 필요.
- 원본 hold 해제·재큐·코드 수정은 금지 (task-16651 spec).
- `loc_over_500` 게이트는 관찰용 aids — baseline을 올리지 말고 파일 분할로 해소해야 함.
