# 파일 범위 분할 제안: task-23846 (QA, coverage 적색)

## 분석

- 원본 files: `["src/"]` — 디렉터리라 파일 범위로 해석 불가(`unresolved_path_scope`).
- 원본 증상: `[coverage] FAIL: 기준선 미달 94.83% -> 56.76% (-38.07%p)` (esc-ci-coverage).
- `scripts/coverage_ratchet.py`는 `coverage.xml`만 읽는다. 94.83 -> 56.76 급락은 `src/` 한 파일의 회귀가 아니라
  pytest가 중도 종료된 partial report(분모 `lines-valid` 축소 또는 일부 패키지 미임포트)일 가능성이 높다.
  동일 패턴 선례: task-7644/7670, task-8680, task-9052, task-9120 (`coverage_ratchet.py` 모듈 docstring 참조).
- 따라서 `src/` 전체를 한 리프로 두면 안 되고, "측정 신뢰성 판정" -> "패키지별 실제 미커버 파일 정정" 순으로 쪼갠다.
  기준선 하향·ignore 추가는 금지(DECISION_GUIDELINES B-2).

## 제안 분할 (실제 파일 단위)

1. **리프 A — 측정 신뢰성 판정 (코드 수정 없음 가능)**
   - files: `scripts/coverage_ratchet.py`, `coverage-baseline.txt` (읽기 전용 확인)
   - 작업: 최신 `coverage.xml`의 `lines-valid`를 baseline 측정 시점과 비교, `gh run download`의 GitHub Actions 값과 대조.
     partial run이면 noop-reason에 수치와 함께 done, 아니면 리프 B~E 발행.
2. **리프 B — `src/core/` 하위 (FROZEN_PAPER_ONLY 제외)**
   - files: `src/core/indicators/`, `src/core/eventstore/`, `src/core/safety/`, `src/core/security/`,
     `src/core/validator/`, `src/core/scanner/`, `src/core/risk_stats/` 중 `coverage.xml`에서 line-rate 급락 클래스로 확인된 파일만
   - 주의: `src/core/strategy|portfolio|risk|executor`는 FROZEN 승인 없이 수정 불가.
3. **리프 C — `src/foundation/`** (리프 A에서 급락 확인된 aggregate 파일만 지정, 테스트는 `tests/unit/foundation/...`)
4. **리프 D — `src/services/` 및 `src/api/`** (`router_registry.py`, `routers/`, `schemas/`) — 급락 확인된 모듈만.
5. **리프 E — `src/exchanges/`, `src/db/`** — 급락 확인된 모듈만.

## 선행 조건

리프 A의 결과(급락 클래스 목록: `coverage.xml`에서 line-rate 0 또는 미임포트 파일)가 B~E의 files 목록을 확정한다.
A 없이 B~E를 발행하면 다시 `src/` 수준 범위가 되므로 A를 `depends_on`으로 둘 것.

## 비고

- 원본(task-23846) hold 해제·재큐·코드 수정은 하지 않았다(본 task는 제안만).
- 이 환경에서는 coverage 실측(`pytest --cov=src`, 약 26분)을 돌리지 않았다. 위 원인은 스크립트 docstring과 선례 기반 추정이며 리프 A에서 검증한다.
