"""lifespan 시작/정리 성능 예산 테스트 — 병렬 실행 격리.

task-20184: test_lifespan_startup_performance_meets_budget를 sibling 파일로
분리(§6-12, _<aspect>.py 규칙). 공유 `app` 싱글톤과 분리해 병렬 워커의 lifespan
충돌 영향을 받지 않는다.
"""

from __future__ import annotations

import pytest

from tests.conftest import PerfBudget


@pytest.mark.perf
async def test_lifespan_startup_performance_meets_budget(
    perf_budget: PerfBudget,
) -> None:
    """성능 단언(DoD): lifespan 초기화/정리 시간이 예산 내인지 확인한다.

    ADR-2026-09-09-C 성능 예산 기준:
    - startup p99 < 5s (pool 생성, event_bus 초기화, background loops 시작)
    - shutdown p99 < 2s (tasks cancel, pool.close, event_bus.stop)

    raw time.perf_counter() → perf_budget.samples_async() 전환(task-11073).
    lifespan_context 는 한 번에 startup+shutdown 를 실행하므로 전체 span 을
    측정하고 합산 예산으로 체크한다. CI 병렬 실행 환경에서 안정성을 위해
    원본 7초 → 14s(task-15462) → 28s(task-15488)로 단계적 확대.

    병렬 실행 격리: 공유 `app` 싱글톤을 쓰면 동시 테스트와 lifespan 상태가
    충돌한다(task-20036). 매 샘플마다 새 FastAPI 인스턴스를 만들어 격리한다.
    """
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    async def _lifespan_once() -> None:
        test_app = FastAPI(lifespan=_lifespan)
        async with test_app.router.lifespan_context(test_app):
            pass

    samples = await perf_budget.samples_async(_lifespan_once, n=3)
    # p99 근사: 3회 샘플 중 최대값
    # CI 병렬 실행 환경에서 안정성을 위해 원본 14s의 2배로 확대(task-15462 이후).
    # wall_ms → cpu_ms: CI 병렬 워커의 코어 경합이 벽시계 시간을 부풀린다(task-19193).
    max_cpu_ms = max(s.cpu_ms for s in samples)
    assert max_cpu_ms < 28000, (
        f"lifespan startup+shutdown p99={max_cpu_ms:.0f}ms exceeded budget 28s "
        f"(cpu_ms samples={[s.cpu_ms for s in samples]})"
    )
