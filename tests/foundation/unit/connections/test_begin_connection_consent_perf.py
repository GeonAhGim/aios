"""begin_connection consent-not-fresh path -- PERF-detection (task-16971).

Budget for `test_begin_connection_raises_when_consent_not_fresh`. Lives in a sibling
file because test_begin_connection_application.py is already over the 500-line ratchet.
The budget is measured with best_of (min over repeats, batch=10000) so a single Windows
process_time tick (~15.6 ms) cannot collapse or spike the reading; a 10x virtual delay
(no real sleep or spin) turns the same assertion red.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

import pytest

from src.foundation.connections.application.begin_connection import (
    ConsentRequiredError,
    begin_connection,
)
from tests.conftest import PerfBudget
from tests.foundation.unit.connections.test_begin_connection_application import (
    FakeConnectionRepository,
    FakeTrustRepository,
    _kwargs,
)
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

_BUDGET_MS = 1.0
_BATCH = 10000
_DELAY_S = 0.01  # 10x the 1.0 ms budget


def _make_run(
    call: Callable[..., Awaitable[object]],
) -> Callable[[], Awaitable[None]]:
    tenant_id = uuid4()
    conn_repo = FakeConnectionRepository()
    trust_repo = FakeTrustRepository(disclosure=None, latest_consent=None, active_consent=None)
    kwargs = _kwargs(tenant_id)

    async def _run() -> None:
        with pytest.raises(ConsentRequiredError):
            await call(conn_repo, trust_repo, **kwargs)

    return _run


@pytest.mark.perf
async def test_consent_not_fresh_perf_budget(perf_budget: PerfBudget) -> None:
    sample = await perf_budget.best_of_async(
        _make_run(begin_connection), n=5, warmup=1, batch=_BATCH
    )
    assert sample.cpu_ms < _BUDGET_MS, (
        f"consent_not_fresh CPU {sample.cpu_ms:.3f}ms exceeded {_BUDGET_MS}ms budget"
    )


@pytest.mark.perf
async def test_consent_not_fresh_budget_red_under_10x_virtual_delay(
    perf_budget: PerfBudget,
) -> None:
    """Gate-red: 10x virtual delay per call turns the same assertion red."""
    reset_virtual_totals()
    slow = virtual_delayed_async(begin_connection, _DELAY_S)
    sample = await perf_budget.best_of_async(_make_run(slow), n=3, batch=20)
    assert sample.wall_ms >= _DELAY_S * 1000
    with pytest.raises(AssertionError):
        assert sample.wall_ms < _BUDGET_MS, "budget exceeded"


async def test_consent_not_fresh_failure_injection_surfaces_repo_error() -> None:
    """Failure injection: a trust repository that raises is not swallowed."""

    class _Boom(FakeTrustRepository):
        async def get_active_disclosure(self, purpose: str) -> None:
            raise RuntimeError("trust store down")

    trust_repo = _Boom(disclosure=None, latest_consent=None, active_consent=None)
    conn_repo = FakeConnectionRepository()
    with pytest.raises(RuntimeError, match="trust store down"):
        await begin_connection(conn_repo, trust_repo, **_kwargs(uuid4()))
    assert conn_repo.insert_pending_calls == 0
