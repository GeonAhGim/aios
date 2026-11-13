# 파일 범위 분할 제안: task-24631

## 현황

- task-24631: `[health:ci_red] CI pytest_perf_parallel_1 정정`
- files: `["src/"]` — **과다 지정**. 전소스 스캔이 아니라 단일 테스트 지원 파일 수정임.
- 이전 시도: ops-3가 2회 시도 후 턴 한도 초과 (task-24631 attempt1/2)
- 원인: `files: ["src/"]`로 지정되어 있어 관련 파일 수백 개를 조사하려 함 → 턴 소모
- 이미 main에 fix commit 19ecd1000이 병합됨 (no-op done)

## 실제 변경 파일 분석

### 수정된 파일 (task-24631의 commit 19ecd1000)

| 파일 | 내용 | 줄 수 |
|------|------|-------|
| `tests/support/perf_inject.py` | `delayed_sync`를 wall-clock 기준 spin → CPU time 기준 spin으로 전환 | +26/-3 |
| `tests/support/test_perf_inject.py` | CPU 경쟁 시뮬레이션 테스트 3건 신규 추가 | +46 |

### 참조만 된 파일 (수정 안 함, 250개 이상)

- `tests/support/perf_budget.py` — `PerfBudget.sample_*` 측정 방식 (수정 안 함)
- `src/exchanges/bitget/symbols.py` — `virtual_delayed_sync` 사용 (수정 안 함)
- `tests/unit/exchanges/test_symbol_canonicalization_depth1.py` — `virtual_delayed_sync` 사용 (수정 안 함)
- 그 외 250개 테스트 파일이 `delayed_sync`/`virtual_delayed_sync`/`perf_budget`를 import

## 분할안

### split-1: [test] perf_inject.py delayed_sync CPU time spin 정정 (권장)
- **범위**: `tests/support/perf_inject.py`
- **파일 수**: 1개 (130줄)
- **검증**: `pytest tests/support/test_perf_inject.py` (5 passed)
- **예상 턴**: 3-5턴
- **비고**: 이 파일 하나만 고치면 됨. `delayed_sync`의 spin 기준을 wall→CPU time으로 변경

### split-2: [test] perf_inject 테스트 보강 (선택)
- **범위**: `tests/support/test_perf_inject.py`
- **파일 수**: 1개 (81줄)
- **검증**: 위와 동일
- **예상 턴**: 2-3턴
- **비고**: CPU 경쟁 시뮬레이션, starvation 처리, wall-time 우선 등 3개 negative/failure-injection 테스트

## 권장

task-24631은 **split-1 + split-2를 합쳐도 files 2개**임. `files`를
`["tests/support/perf_inject.py", "tests/support/test_perf_inject.py"]`로 좁히면 턴 한도
문제를 피할 수 있음.

참조 파일 250개는 `delayed_sync`를 **호출**만 할 뿐 수정 대상이 아님. 수정 범위와
참조 범위를 구분해야 함.
