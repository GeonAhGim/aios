"""lifespan 배선 검증 — 병렬 실행 격리.

task-20235: test_lifespan_wires_app_state_and_starts_background_loops를 sibling 파일로
분리(§6-12, _<aspect>.py 규칙). 공유 `app` 싱글톤을 그대로 쓰므로 병렬 워커 간
lifespan 상태 충돌이 있을 수 있으나, 이 테스트는 lifespan 내부 배선만 확인하고
상태를 변경하지 않는다.
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

        # heartbeat/alert/risk_guard/safety/personal_daily_loss_monitor 루프
        # (conftest.py가 AIOS_EXECUTION_LOOP_ENABLED=0으로 실행 루프는 꺼둔다).
        # personal_daily_loss_task는 task-6510부터 항상 배선된다(src/main.py가
        # start_personal_daily_loss_monitor_task를 무조건 호출) — src/services/
        # personal_daily_loss_loop.py에 정의돼 있지만, 그 함수가 만드는 태스크의
        # 코루틴은 인자로 받은 background_loops.run_periodic_loop이므로
        # _defined_in_background_loops가 여전히 이 태스크를 잡아낸다.
        # InProcessEventBus의 내부 워커 태스크 등 다른 신규 태스크와 구분하기
        # 위해 background_loops.py에 정의된 코루틴만 골라낸다.
        new_tasks = asyncio.all_tasks() - tasks_before
        loop_tasks = {task for task in new_tasks if _defined_in_background_loops(task)}
        assert len(loop_tasks) == 5
        assert all(not task.done() for task in loop_tasks)

        pool = app.state.pool
        assert await pool.fetchval("SELECT 1") == 1

    # 종료 시 루프가 전부 취소·수거되고 pool이 닫힌다.
    assert all(task.done() for task in loop_tasks)
    assert pool._closed
