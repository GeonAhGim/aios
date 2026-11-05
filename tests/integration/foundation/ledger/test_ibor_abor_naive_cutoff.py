"""FA-12 IBOR naive-cutoff depth evidence (task-22427): negatives, injection, perf, red.

Split from test_ibor_abor.py (file-size ratchet); no DB needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ledger.application import ibor_view
from src.foundation.ledger.application.ibor_view import NaiveCutoffError


class _ExplodingPool:
    """Pool stub whose `acquire` must never run on the naive-cutoff path."""

    acquired = 0

    def acquire(self):
        type(self).acquired += 1
        raise RuntimeError("pool unavailable")


@pytest.mark.parametrize(
    "naive_cutoff",
    [datetime(2026, 1, 1), datetime(2026, 1, 1, 12, 30, 45, 123456), datetime.min],
    ids=["date-midnight", "microseconds", "datetime-min"],
)
async def test_ibor_view_naive_cutoff_rejected_before_any_db_access(naive_cutoff):
    """Negative x3: every tz-naive cutoff is rejected fail-closed before the pool is touched."""
    pool = _ExplodingPool()
    with pytest.raises(NaiveCutoffError, match="cutoff"):
        await ibor_view.compute_ibor_view(
            pool=cast(asyncpg.Pool, pool), fund_id=uuid4(), cutoff=naive_cutoff
        )
    assert _ExplodingPool.acquired == 0


async def test_ibor_view_aware_cutoff_db_failure_propagates_not_masked_as_naive():
    """Failure injection + gate-red: with an aware cutoff the tz guard passes, so the injected
    pool failure surfaces unchanged -- proves the naive assertion above discriminates."""
    pool = _ExplodingPool()
    with pytest.raises(RuntimeError, match="pool unavailable"):
        await ibor_view.compute_ibor_view(
            pool=cast(asyncpg.Pool, pool), fund_id=uuid4(), cutoff=datetime(2026, 1, 1, tzinfo=UTC)
        )


@pytest.mark.perf
async def test_ibor_view_naive_cutoff_rejection_latency_and_10x_delay_fires_red(
    perf_budget, monkeypatch
):
    """Perf: best-of-5 CPU of the naive-cutoff rejection. Budget 50ms (local best-of-5 is
    well under 1ms; >30x headroom). Red: a virtual 10x-budget delay injected into the guard
    must breach the same assertion."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    budget_ms = 50.0
    naive = datetime(2026, 1, 1)

    async def reject() -> None:
        with pytest.raises(NaiveCutoffError):
            await ibor_view.compute_ibor_view(
                pool=cast(asyncpg.Pool, None), fund_id=uuid4(), cutoff=naive
            )

    green = await perf_budget.best_of_async(reject, n=5, warmup=1, batch=20)
    assert green.cpu_ms < budget_ms, perf_budget.describe(green, budget_ms=budget_ms)

    reset_virtual_totals()
    monkeypatch.setattr(
        ibor_view,
        "_require_tz_aware",
        virtual_delayed_sync(ibor_view._require_tz_aware, budget_ms * 10 / 1000),
    )
    red = await perf_budget.best_of_async(reject, n=2, warmup=0, batch=1)
    with pytest.raises(AssertionError):
        assert red.cpu_ms < budget_ms, perf_budget.describe(red, budget_ms=budget_ms)
