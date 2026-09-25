"""Composition root for `personal_daily_loss_monitor`'s always-on background task
(task-6510, moved here from `background_loops.py` to respect its P6 300-line cap).

`foundation/risk/application/personal_daily_loss_monitor.py` may not import
`positions.adapters.postgres_nav_repository` (RATCHET-2 `foundation-aggregates` --
crosses an aggregate boundary) nor `services.background_loops` (would cycle back, since
background_loops.py needs this module's task). This module takes `run_periodic_loop` as
a parameter instead of importing it from `background_loops.py`, so no cycle exists here
either -- `background_loops.py` passes its own `run_periodic_loop` in.

Reads `PERSONAL_DAILY_LOSS_MONITOR_INTERVAL_SECONDS`/`run_personal_daily_loss_monitor_tick`
off the `personal_daily_loss_monitor` module object (not a `from ... import name`) so
`tests/integration/oms/test_background_loops_wiring.py`'s
`monkeypatch.setattr(pdl_monitor_module, "PERSONAL_DAILY_LOSS_MONITOR_INTERVAL_SECONDS", ...)`
is still observed at call time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import asyncpg

from src.core.observability.loop_health import LoopHealth
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.risk.adapters.json_state_store import JsonPersonalStateStore
from src.foundation.risk.adapters.telegram_adapter import TelegramNotifierAdapter
from src.foundation.risk.application import personal_daily_loss_monitor as pdl_monitor

RunPeriodicLoop = Callable[..., Coroutine[Any, Any, None]]


def start_personal_daily_loss_monitor_task(
    pool: asyncpg.Pool,
    *,
    health: LoopHealth,
    run_periodic_loop: RunPeriodicLoop,
) -> asyncio.Task[None]:
    """Wires the tick into a real asyncio task on the caller's `run_periodic_loop`."""

    async def _tick() -> Any:
        return await pdl_monitor.run_personal_daily_loss_monitor_tick(
            pool,
            personal_state=JsonPersonalStateStore(),
            personal_notifier=TelegramNotifierAdapter(),
            nav_repo=PostgresNavRepository(pool),
        )

    return asyncio.create_task(
        run_periodic_loop(
            "personal_daily_loss_monitor",
            pdl_monitor.PERSONAL_DAILY_LOSS_MONITOR_INTERVAL_SECONDS,
            _tick,
            health=health,
            on_error="personal_daily_loss_monitor_loop: 이번 주기 실패 -- 재시도합니다.",
        )
    )
