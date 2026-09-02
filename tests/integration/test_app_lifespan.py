"""16번대 통합테스트 — src/main.py lifespan.

background_loops.py 분리(P6) 후에도 lifespan의 동작(app.state 배선, 백그라운드
루프 시작·정지, pool/event_bus 정리)이 그대로인지 확인한다. 실제 dev DB에
연결한다(tests/conftest.py의 TEST_DATABASE_URL).
"""
from __future__ import annotations

import asyncio

from src.core.event_bus.in_process import InProcessEventBus
from src.main import app
from src.services.credential_resolver import CredentialResolver
from src.services.execution_loop.scheduler import ExecutionLoopScheduler


def _defined_in_background_loops(task: asyncio.Task[None]) -> bool:
    code = getattr(task.get_coro(), "cr_code", None)
    return code is not None and "background_loops.py" in code.co_filename


async def test_lifespan_wires_app_state_and_starts_background_loops() -> None:
    tasks_before = asyncio.all_tasks()

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.event_bus, InProcessEventBus)
        assert isinstance(app.state.credential_resolver, CredentialResolver)
        assert isinstance(app.state.execution_scheduler, ExecutionLoopScheduler)
        assert not app.state.pool._closed

        # heartbeat/alert/risk_guard/safety 루프(conftest.py가
        # AIOS_EXECUTION_LOOP_ENABLED=0으로 실행 루프는 꺼둔다). InProcessEventBus의
        # 내부 워커 태스크 등 다른 신규 태스크와 구분하기 위해 background_loops.py에
        # 정의된 코루틴만 골라낸다.
        new_tasks = asyncio.all_tasks() - tasks_before
        loop_tasks = {task for task in new_tasks if _defined_in_background_loops(task)}
        assert len(loop_tasks) == 4
        assert all(not task.done() for task in loop_tasks)

        pool = app.state.pool
        assert await pool.fetchval("SELECT 1") == 1

    # 종료 시 루프가 전부 취소·수거되고 pool이 닫힌다.
    assert all(task.done() for task in loop_tasks)
    assert pool._closed


async def test_lifespan_can_start_and_stop_twice() -> None:
    """app 재시작(예: 여러 테스트 모듈의 client 픽스처)에도 잔여 태스크가
    누적되지 않는지 — 백그라운드 루프 취소 로직이 매번 완전히 정리하는지 확인."""
    tasks_before = asyncio.all_tasks()

    async with app.router.lifespan_context(app):
        pass
    async with app.router.lifespan_context(app):
        pass

    tasks_after = asyncio.all_tasks()
    assert tasks_after - tasks_before == set()
