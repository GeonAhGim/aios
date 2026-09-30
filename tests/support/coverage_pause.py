"""성능 측정 구간의 coverage 수명 관리."""

from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

coverage: ModuleType | None
try:
    import coverage
except ImportError:  # pragma: no cover -- coverage.py ships with pytest-cov
    # (dev dependency); guard the import so PerfBudget still works in a venv
    # that lacks it instead of failing perf tests over a missing optional dep.
    coverage = None

@contextmanager
def paused_coverage() -> Iterator[None]:
    """task-7250(esc-ci-coverage) — `pytest --cov=src`가 걸어 둔 전역
    line-tracer(`sys.settrace`)는 감싼 구간의 모든 CPU/wall 시간 측정에
    실제 오버헤드로 섞여 들어간다. perf 예산 단언은 이 구간에서만 활성
    `coverage.Coverage`를 멈췄다 재개해 트레이서 오버헤드를 측정에서
    제외한다 — `coverage`가 설치돼 있지 않거나 현재 활성 인스턴스가 없으면
    (예: coverage 없이 단독 실행) 아무 것도 하지 않는다. 감싼 코드는
    그대로 실행되므로(트레이싱만 잠시 꺼질 뿐) 라인 자체는 여전히
    실행되고, 다른 테스트가 같은 경로를 exercising하는 한 커버리지에
    영향이 없다."""
    active_cov = coverage.Coverage.current() if coverage is not None else None
    if active_cov is not None:
        active_cov.stop()
    try:
        yield
    finally:
        if active_cov is not None:
            active_cov.start()
