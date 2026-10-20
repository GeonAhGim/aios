"""EO-06 정적 검사 — 성능·동시성 증거.

스캐너 핵심 로직은 `test_gate_params_required.py`에, 성능 단언과
동시 실행 일관성 증명만 분리한다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.conftest import PerfBudget
from tests.unit.test_gate_params_required import _scan_source

# --- 성능 단언 ---------------------------------------------------------------


@pytest.mark.perf
def test_scan_source_perf_bound_for_large_synthetic_file(perf_budget: PerfBudget) -> None:
    """성능 단언 — `_scan_function_defs`는 같은 트리를 `ast.walk`로 두 번
    순회한다(메서드 수집 1회 + 최상위 함수 수집 1회). 대상 파일이 커질수록
    이차식으로 느려지는 회귀가 들어와도 잡히지 않으면 CI가 눈치채지 못한 채
    점점 느려질 수 있다 — 합성 대형 파일로 시간 상한을 못박는다.

    raw perf_counter() 단언을 perf_budget.assert_within(batch=8)으로 전환:
    process_time() 15.6ms 틱 양자화 오차를 batch=8로 줄여 호출당 ~2ms로
    낮춤. 예산 2000ms(단위 ms, budget_ms=2000)."""
    source = "\n".join(
        f"def f_{i}(pre_submit_gate_{i}: str) -> None:\n    pass\n" for i in range(3000)
    )
    perf_budget.assert_within(
        lambda: _scan_source(source, "fixture.py"),
        budget_ms=2000,
        batch=8,
        label="3000개 함수 스캔",
    )


# --- 다중 인스턴스(동시 실행) 증명 -------------------------------------------


def test_scan_source_consistent_across_concurrent_instances() -> None:
    """다중 인스턴스 증거 — CI가 여러 워커(pytest-xdist 등)나 여러 스레드에서
    동시에 이 스캐너를 돌릴 수 있다. 스캐너가 모듈 전역 가변 상태를 공유하면
    경합으로 일부 실행이 위반을 놓치는 거짓 초록불(fail-open)이 나올 수
    있다 — 알려진 위반이 섞인 동일 소스를 여러 스레드에서 동시에 스캔해도
    항상 같은 결과가 나와야 한다(실제 전체 리포지토리를 8번 재스캔하면
    수십 초가 드는 I/O 비용을 피하려 합성 소스를 쓴다)."""
    source = (
        "class Scheduler:\n"
        "    def __init__(self, pre_submit_gate: str | None = None) -> None:\n"
        "        pass\n\n"
        "def is_submission_allowed(distrust_monitor: object | None = None) -> bool:\n"
        "    return distrust_monitor is None\n"
    )
    expected = [
        "fixture.py:Scheduler.__init__(pre_submit_gate) — `... | None` 애너테이션",
        "fixture.py:is_submission_allowed(distrust_monitor) — `... | None` 애너테이션",
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: [str(v) for v in _scan_source(source, "fixture.py")], range(16))
        )
    assert all(r == expected for r in results)
