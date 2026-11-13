# 파일 범위 분할 제안: task-24871 [재투입]

## 현황

- task-24871: `[health:ci_red] CI pytest_perf_parallel_3 정정`
- files: `["src/"]` — **과다 지정**. 전소스 스캔이 아니라 3개 테스트 파일의 개별 정정임.
- 이전 시도: ops-2가 1회 시도 후 턴 한도 초과 (1362s, 46턴)
- 원인: `files: ["src/"]`로 지정되어 있어 관련 파일 수백 개를 조사하려 함 → 턴 소모
- **핵심 발견: 3개 테스트 모두 현재 main에서 이미 통과함.**

## 실제 변경 파일 분석

### 회귀 원인 커밋: cc4e82bb924 (2026-11-05)

```
fix(indicators): 지표 지연 검증을 perf 단계로 분리
```

- 변경 파일: **1개** (`tests/unit/test_indicator_service.py`, +4줄)
- 내용: 2개 테스트에 `@pytest.mark.perf` 마커 추가
- **이 커밋 자체가 3개 probe 테스트를 변경하지 않음** — CI가 병렬 실행 시 다른 테스트와의 상호작용으로 적색

### probe 1회 대상 테스트 3건

| 테스트 파일 | 함수명 | 현재 상태 | 실제 수정 파일 |
|------------|--------|----------|---------------|
| `tests/unit/data/models/test_serialization.py` | `test_serialize_cpu_measurement_converges_with_batch` | **통과** | `test_serialization.py` (commit `8dd266256`: sample → best_of) |
| `tests/unit/services/test_open_order_sweeper_edge.py` | `test_sweep_open_orders_p95_latency_detection_injected_delay` | **통과** | 이 파일만 수정 필요 (perf_budget 전환 관련) |
| `tests/integration/foundation/allocation/test_conftest_deepen.py` | `test_create_order_with_fills_round_trip_stays_within_local_budget` | **통과** | 이 파일만 수정 필요 (budget 상향, baseline 위치 변경) |

### 각 테스트의 실제 수정 파일

| split | 범위 | 파일 수 | 줄 수 | 검증 | 예상 턴 |
|-------|------|---------|-------|------|---------|
| split-1 | `tests/unit/data/models/test_serialization.py` | 1 | ~200 | `pytest tests/unit/data/models/test_serialization.py` (15 passed) | 2-3 |
| split-2 | `tests/unit/services/test_open_order_sweeper_edge.py` | 1 | ~192 | `pytest tests/unit/services/test_open_order_sweeper_edge.py` | 3-5 |
| split-3 | `tests/integration/foundation/allocation/test_conftest_deepen.py` | 1 | ~181 | `pytest tests/integration/foundation/allocation/test_conftest_deepen.py` | 3-5 |

## 권장

1. **task-24871은 현재 main에서 이미 모든 테스트가 통과하므로 no-op 가능**
   - `git log cc4e82bb..HEAD -- tests/unit/data/models/test_serialization.py` → `8dd266256`에서 fix
   - `test_sweep_open_orders_p95_latency_detection_injected_delay` → 현재 통과
   - `test_create_order_with_fills_round_trip_stays_within_local_budget` → commit `8e04419fb`/`053acaccd`에서 budget/baseline 정정

2. **만약 ND-17 재발행이 새 회귀를 가리킨다면** — files를 위 3개 파일로 좁혀야 함:
   ```
   files: [
     "tests/unit/data/models/test_serialization.py",
     "tests/unit/services/test_open_order_sweeper_edge.py",
     "tests/integration/foundation/allocation/test_conftest_deepen.py"
   ]
   ```

3. **분할 권장**: split-1은 trivial(2줄 변경), split-2와 split-3은 각각 독립적.
   split-2와 split-3을 별도 task로 나누면 각 task가 3-5턴으로 턴 한도 문제를 피함.
