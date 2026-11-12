# scope-proposal: task-23992 파일 범위 분할안

## 현황
- task-23992는 `files: ["src/"]`로 전소스 1583개 .py 파일을 단일 QA 범위로 포함
- 현재 상태: **done** (noop — guards 이미 green, 회귀 없음)
- 문제: `src/` 단일 파일로 `local_leaf_oversize_reasons: ["path_scope=src/"]` 표시됨

## 실제 변경 파일 분석 (origin/main..HEAD)
- 현재 HEAD(d3f422aa2)는 origin/main과 동일 → 변경 파일 0건
- guards 스테이지는 diff 기반 검사이므로, 실제 작업은 변경된 파일에 한정

## 분할안

task-23992의 실제 QA 범위는 변경된 파일에 따라 동적이지만, **향후 재발 방지를 위해** 아래처럼 분할:

### split-1: [guards] architecture_guard.py 관련
- **범위**: `src/core/`, `src/foundation/`, `src/services/` (FROZEN_PAPER_ONLY + 계약/이주 경로)
- **검증 항목**: P1 frozen, P5 contract, P6 line length, migration chain
- **예상 파일 수**: ~200개 (core/ ~150, foundation 계약/이주 ~50)

### split-2: [guards] security_guard.py 관련
- **범위**: `src/exchanges/`, `src/db/migrations/versions/`, `src/core/executor/executor.py`
- **검증 항목**: P2 live guard, P3 WORM, P4 secret
- **예상 파일 수**: ~250개 (exchanges ~100, migrations ~134, 기타 ~20)

### split-3: [guards] 공통 유틸 + lint
- **범위**: `src/api/`, `src/foundation/` 나머지, `src/services/` 나머지
- **검증 항목**: P7 unconditional update, 한글 주석, Decimal 사용
- **예상 파일 수**: ~1100개

## 권장
- 현재 task-23992는 **noop으로 종료済み**이므로 분할 불필요
- 향후 유사 CI red 발생 시, 위 3개로 분할 queued
- 분할 시 spec은 각 split의 검증 항목을 명시, files는 위 범위 제한
