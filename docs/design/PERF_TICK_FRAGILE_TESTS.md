# PERF_TICK_FRAGILE_TESTS.md

Windows 양자화 tick(15.625ms)에 취약한 단발 sample 테스트 전수 목록

## 조사 개요

**조사 일자**: 2026-10-29
**조사 방법**: AST 스캔 — `tests/` 전체에서 `cpu_ms` 단언과 `sample`/`sample_async` 호출 패턴 추출
**제외 조건**: `best_of`/`best_of_async`를 사용하는 파일 전량 제외
**포함 조건**:
1. `cpu_ms`를 0 또는 5ms 이하 임계와 비교 단언하는 테스트
2. `best_of`/`best_of_async`를 사용하지 않음
3. `batch <= 8` 단발 `sample`/`sample_async` 호출 포함

## 집계

| 지표 | 값 |
|------|-----|
| **대상 파일 수** | 46 |
| **전체 sample/sample_async 호출 수** | 103 |
| **소.batch(<=8) 호출 수** | 73 |
| **cpu_ms == 0 임계 파일** | 1 |
| **cpu_ms 0~1ms 임계 파일** | 34 |
| **cpu_ms 1~5ms 임계 파일** | 12 |

## 파일별 목록

### foundation.integration (1)

| 파일 | cpu_ms 임계 | sample 호출 | 소.batch |
|------|------------|-------------|----------|
| `foundation/integration/trust/test_postgres_repository.py` | [1.0, 1.0] | sample x2 | 2 |

### foundation.unit (16)

| 파일 | cpu_ms 임계 | sample 호출 | 소.batch |
|------|------------|-------------|----------|
| `foundation/unit/ems/test_fix_session_contract.py` | [0.1, 5.0] | sample_async x5 | 3 |
| `foundation/unit/ems/test_route_order_application.py` | [1.0] | sample_async x4 | 3 |
| `foundation/unit/entities/test_ensure_default_hierarchy.py` | [1.0] | sample_async x4 | 4 |
| `foundation/unit/follow/test_mirror_signal.py` | [1.0] | sample_async x3 | 2 |
| `foundation/unit/market_data/ports/test_ports_protocol_deepen_2877.py` | [1.0, 1.0] | sample x2 | 2 |
| `foundation/unit/ml/test_drift.py` | [1.0] | sample x1 | 1 |
| `foundation/unit/ml/test_register_model.py` | [1.0] | sample_async x2 | 1 |
| `foundation/unit/paper_control/test_models.py` | [1.0] | sample x1 | 0 |
| `foundation/unit/paper_control/test_projections.py` | [1.0, 100.0] | sample_async+sample x3 | 3 |
| `foundation/unit/positions/test_fx_forward.py` | [1.0] | sample x1 | 1 |
| `foundation/unit/positions/test_weighted.py` | [1.0, 1.0] | sample x3 | 1 |
| `foundation/unit/reconciliation/test_models.py` | [1.0, 1.0] | sample x3 | 1 |
| `foundation/unit/research_data/test_contracts_v1_deepen_2905.py` | [5.0] | 없음 | 0 |
| `foundation/unit/research_data/test_known_at_deepen_2905.py` | [1.0, 1.0] | sample x3 | 0 |
| `foundation/unit/research_data/test_revision.py` | [5.0, 50.0] | sample x3 | 0 |
| `foundation/unit/trust/test_segregation_of_duty.py` | [1.0, 1.0] | sample x3 | 0 |
| `foundation/unit/validation/application/test_compile_artifact.py` | [5.0] | sample_async x2 | 2 |
| `foundation/unit/validation/checks/test_point_in_time.py` | [5.0, 5.0, 5.0] | sample x3 | 0 |
| `foundation/unit/validation/test_contracts_v1.py` | [1.0, 1.0, 1.0] | sample x3 | 1 |

### unit (29)

| 파일 | cpu_ms 임계 | sample 호출 | 소.batch |
|------|------------|-------------|----------|
| `unit/api/schemas/foundation/test_trust.py` | [50.0, 1.0, 1.0] | sample x3 | 0 |
| `unit/core/observability/test_loop_health.py` | [1.0] | sample x3 | 3 |
| `unit/core/observability/test_metrics_port.py` | [1.0] | sample x1 | 1 |
| `unit/core/observability/test_research_data_coverage_alerts.py` | [1.0] | sample x1 | 1 |
| `unit/core/portfolio/sizing/test_selector.py` | [1.0] | sample x1 | 1 |
| `unit/core/portfolio/test_accounting.py` | [0.5, 4.0, 1.0] | sample x1 | 1 |
| `unit/core/script/test_ast.py` | [1.0] | sample x1 | 1 |
| `unit/core/script/test_lookahead.py` | [1.0] | sample x1 | 1 |
| `unit/core/script/test_resources.py` | [1.0] | sample x1 | 1 |
| `unit/core/script/test_script_library.py` | [1.0] | sample x1 | 1 |
| `unit/core/test_portfolio_engine.py` | [1.0] | sample x1 | 1 |
| `unit/data/models/test_serialization.py` | [1.0] | sample x1 | 1 |
| `unit/data/models/test_strategy_fsm.py` | [1.0] | sample x1 | 1 |
| `unit/foundation/ems/test_decomposition.py` | [1.0] | sample x1 | 1 |
| `unit/foundation/marketplace/test_contracts_v1.py` | [1.0] | sample x1 | 1 |
| `unit/foundation/reconciliation/test_projections.py` | [5.0] | sample_async x2 | 2 |
| `unit/foundation/research_data/test_link_entities_deepen_2906.py` | [5.0, 100.0] | sample_async x2 | 2 |
| `unit/foundation/research_data/test_macro_series.py` | [400.0, 5.0, 5.0] | sample x5 | 2 |
| `unit/foundation/research_data/test_sec_edgar_source.py` | [3.0] | sample x2 | 2 |
| `unit/foundation/whatif/application/test_preview_order.py` | [1.0, 1.0] | sample x2 | 2 |
| `unit/foundation/whatif/domain/test_impact.py` | [1.0, 1.0, 1.0] | 없음 | 0 |
| `unit/market_data/test_dedupe.py` | [1.0] | sample x1 | 1 |
| `unit/market_data/test_ohlc_sanity.py` | [1.0, 5000.0, 1.0] | sample x3 | 1 |
| `unit/scripts/test_upbit_openapi_fetch.py` | [5.0] | 없음 | 0 |
| `unit/services/test_market_state_multi_tf.py` | [1.0] | sample x1 | 1 |
| `unit/test_virtual_delay_injection.py` | [1.0, 1.0, 20.0] | sample+sample_async x6 | 6 |

## 후속 리프 목록 (3개 파일씩 묶음, 15개 리프)

### 그룹 1: foundation.unit (3 files)
1. `[perf-fix] foundation/unit/ems/test_fix_session_contract.py` — 0.1/5.0ms 임계, sample_async 단발 3개
2. `[perf-fix] foundation/unit/ems/test_route_order_application.py` — 1.0ms 임계, sample_async 단발 3개
3. `[perf-fix] foundation/unit/entities/test_ensure_default_hierarchy.py` — 1.0ms 임계, sample_async 단발 4개

### 그룹 2: foundation.unit (3 files)
4. `[perf-fix] foundation/unit/follow/test_mirror_signal.py` — 1.0ms 임계, sample_async 단발 2개
5. `[perf-fix] foundation/unit/market_data/ports/test_ports_protocol_deepen_2877.py` — 1.0ms 임계, sample batch=1 x2
6. `[perf-fix] foundation/unit/ml/test_drift.py` — 1.0ms 임계, sample 단발 1개

### 그룹 3: foundation.unit (3 files)
7. `[perf-fix] foundation/unit/ml/test_register_model.py` — 1.0ms 임계, sample_async 단발 1개
8. `[perf-fix] foundation/unit/paper_control/test_projections.py` — 1.0/100.0ms, sample+sample_async 단발 3개
9. `[perf-fix] foundation/unit/positions/test_fx_forward.py` — 1.0ms 임계, sample 단발 1개

### 그룹 4: foundation.unit (3 files)
10. `[perf-fix] foundation/unit/positions/test_weighted.py` — 1.0ms 임계, sample 단발 1개
11. `[perf-fix] foundation/unit/reconciliation/test_models.py` — 1.0ms 임계, sample 단발 1개
12. `[perf-fix] foundation/unit/validation/application/test_compile_artifact.py` — 5.0ms 임계, sample_async 단발 2개

### 그룹 5: foundation.unit (3 files)
13. `[perf-fix] foundation/unit/validation/test_contracts_v1.py` — 1.0ms 임계, sample batch=5 x1
14. `[perf-fix] foundation/integration/trust/test_postgres_repository.py` — 1.0ms 임계, sample 단발 2개
15. `[perf-fix] foundation/unit/paper_control/test_models.py` — 1.0ms 임계, sample batch=64 (단발 없음, 제외 검토)

### 그룹 6+: 나머지 31개 파일 (unit/ 하위)
- `unit/core/*` 7개 파일 (loop_health, metrics_port, research_data_coverage_alerts, portfolio/sizing/test_selector, portfolio/test_accounting, script/* 4개)
- `unit/data/models/*` 2개 파일
- `unit/foundation/*` 8개 파일
- `unit/market_data/*` 2개 파일
- `unit/services/*` 1개 파일
- `unit/test_virtual_delay_injection.py` 1개 파일
- `unit/api/schemas/foundation/test_trust.py` 1개 파일
- `unit/scripts/test_upbit_openapi_fetch.py` 1개 파일 (sample 없음, 제외 검토)
- `unit/foundation/whatif/domain/test_impact.py` 1개 파일 (sample 없음, 제외 검토)
- `foundation/unit/*` sample 없는 5개 파일 제외 검토

---

**금지**: 테스트·예산 변경 — 조사 전용
