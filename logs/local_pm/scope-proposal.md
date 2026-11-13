# 파일 범위 분할 제안: task-24630

## 현황

- task-24630: `[health:ci_red] CI pytest_perf_parallel_2 정정`
- files: `["src/"]` — **과다 지정**. 전소스 스캔이 아니라 단일 테스트 파일 수정임.
- 이전 시도: task-24669 → task-24681 → task-24695 (현재), 모두 턴 한도 초과로 실패
- 원인: `files: ["src/"]`로 지정되어 있어 관련 파일 수백 개를 조사하려 함 → 턴 소모

## 실제 변경 파일 분석

### 수정된 파일 (2 commit)

| commit | 파일 | 내용 |
|--------|------|------|
| ce240bfa4 | `tests/foundation/unit/ml/test_local_trainer.py` | `delayed_sync`(busy-wait) → `virtual_delayed_sync`(ledger) 전환 |
| 91af43d07 | `tests/foundation/unit/ml/test_local_trainer.py` | Mock wraps 추가, 호출 검증 보강 |

### 참조만 된 파일 (수정 안 함)

- `src/foundation/ml/adapters/local_trainer.py` — `lgb.train` 호출 패턴 확인용
- `tests/support/perf_inject.py` — `virtual_delayed_sync` 정의 (수정 안 함)
- `tests/support/perf_budget.py` — `PerfBudget.sample` 측정 방식 (수정 안 함)

## 분할안

### split-1: [test] test_local_trainer.py perf-gate-red 정정 (권장)
- **범위**: `tests/foundation/unit/ml/test_local_trainer.py`
- **파일 수**: 1개 (284줄)
- **검증**: `pytest tests/foundation/unit/ml/test_local_trainer.py` (14 passed)
- **예상 턴**: 5-10턴

### split-2: [source] local_trainer.py 리뷰 (선택, 별도 task)
- **범위**: `src/foundation/ml/adapters/local_trainer.py`
- **내용**: virtual_delayed_sync 전환이 lgb.train 호출 패턴과 호환되는지 정적 분석
- **검증**: mypy, ruff check

## 권장

task-24630은 **split-1만으로도 충분**. `files`를 `["tests/foundation/unit/ml/test_local_trainer.py"]`로 좁히면 턴 한도 문제를 피할 수 있음. split-2는 별도 QA task로 생성 가능.
