# task-24626 범위 분할 제안

## 현황 분석

- **task-24626 files**: `["src/"]` — 과도하게 광범위. 실제 변경 대상 아님.
- **원인 커밋**: `cc4e82bb9` (2026-11-05) — `tests/unit/test_indicator_service.py`에 `@pytest.mark.perf` 마커 2건 추가.
- **실제 실패 테스트**: `tests/unit/data/models/test_serialization.py::test_serialize_cpu_measurement_converges_with_batch`
  - Windows `time.process_time()` 15.6ms 양자화 tick 때문에 batch=1 단일 샘플이 15.625ms로 읽혀 `<1ms` 단언 실패.
- **이미 적용된 수정**:
  - `8dd266256` — `best_of(n=20)` 최소값으로 전환 (tests/unit/data/models/test_serialization.py, 1파일/4줄)
  - `c9ac2994f` — 10x CPU 지연 주입을 `virtual_delayed_sync`로 전환 (tests/unit/data/models/test_serialization.py)
- **현재 상태**: 세 probe 대상 테스트 모두 로컬에서 녹색 (25 passed). origin/main에 이미 병합됨.

## 분할 제안

task-24626의 실질적 변경 파일은 1개뿐:

| 분할 ID | 파일 | 설명 |
|---------|------|------|
| LF-1 | `tests/unit/data/models/test_serialization.py` | CPU 0 수렴 단언을 best-of-N으로 안정화 + 지연 주입 전환 |

- `tests/unit/services/test_open_order_sweeper_edge.py`와 `tests/integration/foundation/allocation/test_conftest_deepen.py`는 probe 대상이지만 수정이 없었음.
- `src/` 경로 전체는 불필요. 테스트 파일만 해당.

## 결론

task-24626의 CI 적색은 이미 `8dd266256` + `c9ac2994f`로 해결됨. 새 구현 불필요. task-24626은 `status=done`으로 종료 가능.
