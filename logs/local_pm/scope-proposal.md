# 파일 범위 분할 제안: task-25129

## 현황
- task-25129: QA 묶음 1건(부모 24871), files=`src/` (디렉터리 단위라 path_scope 미해소).
- 실제 대상은 원인 커밋 cc4e82bb(tests/unit/test_indicator_service.py 마커 3줄만 변경, 적색의 직접 원인 아님)이 아니라 spec이 지목한 테스트 3개다. 모두 `tests/`이며 `src/`는 수정 대상이 아니다.

## 실제 파일별 분할안 (리프 1개 = 파일 1개)
1. `tests/unit/data/models/test_serialization.py` (212줄) — `test_serialize_cpu_measurement_converges_with_batch`(L143) 적색: Windows 15.6ms 틱 양자화로 batch=1 cpu=15.625ms. 보조: `tests/support/perf_budget.py`(PerfBudget.best_of/sample) 사용.
2. `tests/unit/services/test_open_order_sweeper_edge.py` (191줄) — `test_sweep_open_orders_p95_latency_detection_injected_delay`.
3. `tests/integration/foundation/allocation/test_conftest_deepen.py` (180줄) — `test_create_order_with_fills_round_trip_stays_within_local_budget` (실DB, TEST_DATABASE_URL).

세 파일 모두 500줄 미만이라 `check_code_ratchets.py --near 30` 분할 부담 없음. 서로 독립이라 병렬 가능, 의존 없음.

## 지침
- files 를 위 3개 경로로 교체(`src/` 제거). 예산 상향·기준선 상향·skip/ignore 금지(B-2): 타이머 해상도 의존 측정은 `perf_counter` 기반/반복 배치로 측정 가능한 크기까지 키워 양자화에 둔감하게 고친다.
- 공용 `tests/support/perf_budget.py` 수정이 필요하면 4번째 리프로 분리(영향 범위 넓음).
- 원본 task-25129 hold 해제·재큐·코드 수정은 이 제안에서 하지 않았다.

