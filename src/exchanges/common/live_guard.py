"""Common LIVE-mode guard for exchange adapter methods (order-capability API).

Executor.execute() blocks live orders via two independent checks: (1) hard
block when `mode != "PAPER"`, (2) verification of
`adapter.is_paper_trading`/`is_sandboxed`. Check (1) lives in the caller's
context (strategy_executions.mode), so the adapter instance cannot inspect it
itself — this decorator can only substitute for (2).

Red team #2026-09-02-32 — Convert/Grid/Strategy/Margin/Futures/Loan/
Subaccount extension methods bypass `Executor` and connect directly to the
exchange, leaving this defense line entirely absent. Applying this decorator
at least ensures "an adapter configured for LIVE (demo_mode=False) cannot
execute these methods at all" — not a complete replacement for the dual
defense Executor provides, but it leaves a minimum safety net so that when
the next leaf wires these methods into the router, forgetting to reimplement
that guard still leaves this fallback in place.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from src.core.exceptions import FrozenZonePaperAdapterBlockedError

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def require_paper_sandbox(func: F) -> F:
    @functools.wraps(func)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not (self.is_paper_trading and self.is_sandboxed):
            raise FrozenZonePaperAdapterBlockedError(
                f"{func.__qualname__}은(는) PAPER/sandbox로 구성된 adapter에서만 "
                "호출할 수 있습니다(레드팀 #2026-09-02-32) — LIVE로 구성된 "
                "adapter에서는 Executor를 거치지 않는 이 확장 메서드가 차단됩니다."
            )
        return await func(self, *args, **kwargs)

    return cast(F, wrapper)
