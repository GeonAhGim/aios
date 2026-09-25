"""H-1b(task-3369) — fills in `OrderContext.mandate_revision_id` when a
call site left it `None`.

Split out of `foundation_gate.py` to keep that file under the 300-line cap
(P6, same reason `foundation_compliance.py` was split for CM-8). No
production call site (`execution_deps.py`/`background_loops.py`/
`oms/application/wiring.py`) fills in `mandate_revision_id` yet (module
docstring of `foundation_gate.py`), so `make_foundation_pre_submit_gate`
calls this at `gate()` entry to ask H-1a's `resolve_binding.
resolve_mandate_revision` for the tenant's currently bound revision before
falling through to its own "no mandate" branch.
"""
from __future__ import annotations

from dataclasses import replace

import asyncpg

from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.mandates.application.resolve_binding import resolve_mandate_revision
from src.services.order_service.gate import OrderContext


async def with_resolved_mandate(pool: asyncpg.Pool, context: OrderContext) -> OrderContext:
    if context.mandate_revision_id is not None:
        return context
    resolved = await resolve_mandate_revision(pool, default_portfolio_id(context.user_id))
    if resolved is None:
        return context
    return replace(context, mandate_revision_id=resolved.revision.id)
