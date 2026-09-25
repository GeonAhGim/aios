"""EM-14 (task-5277) -- DI for `src/api/routers/foundation/ems.py`.

Split out of `foundation_deps.py` (architecture guard P6.line_cap, 300
lines) rather than added inline -- same convention as `suitability_deps.py`/
`admin_deps.py` (one deps module per bounded context, not one shared file).
"""

from __future__ import annotations

import asyncpg
from fastapi import Depends, Request

from src.api.deps import get_pool
from src.foundation.ems.adapters.tca_storage import PostgresTcaResultRepository
from src.foundation.ems.application.tick_algo import AlgoScheduler
from src.foundation.ems.ports.tca_result_repository import TcaResultRepository


def get_tca_result_repository(pool: asyncpg.Pool = Depends(get_pool)) -> TcaResultRepository:
    return PostgresTcaResultRepository(pool)


def get_algo_scheduler(request: Request) -> AlgoScheduler:
    """`src.main`'s lifespan always sets `app.state.algo_scheduler`, whether or
    not `AIOS_ALGO_SCHEDULER_ENABLED` actually starts its background loop
    (main.py EM-15 wiring) -- so this is a plain state read, same pattern as
    `deps.get_pool`/`deps.get_event_bus`.
    """
    scheduler: AlgoScheduler = request.app.state.algo_scheduler
    return scheduler
