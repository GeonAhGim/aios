# ruff: noqa: F811  # pytest fixtures imported from test_ibor_abor are re-declared as test params
"""FA-12 IBOR recompute -- DEEPEN-DEPTH evidence (negative / failure injection /
perf / gate-red). Split from test_ibor_abor.py to stay under the 500-line ratchet;
fixtures and helpers are reused from that module unchanged."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ledger.application import ibor_view
from tests.integration.foundation.ledger.test_ibor_abor import (  # noqa: F401
    _account_id,
    _balanced_pair,
    _insert_dated_entry,
    _t,
    accounts,
    fund_id,
    portfolio_id,
)


async def _seed_two_entries(pool, fund_id, portfolio_id, accounts):
    debit_code, credit_code = accounts
    account_ids = {
        debit_code: await _account_id(pool, debit_code),
        credit_code: await _account_id(pool, credit_code),
    }
    base = datetime.now(UTC) - timedelta(hours=1)
    t1, t2 = _t(base, 0), _t(base, 10)
    for posted_at, amount in ((t1, "100.00"), (t2, "50.00")):
        await _insert_dated_entry(
            pool,
            fund_id=fund_id,
            portfolio_id=portfolio_id,
            posted_at=posted_at,
            lines=_balanced_pair(debit_code, credit_code, Decimal(amount)),
            account_ids=account_ids,
        )
    return debit_code, credit_code, t1, t2


async def test_ibor_view_cutoff_before_first_posting_is_empty(
    pool, fund_id, portfolio_id, accounts
):
    """DEEPEN-DEPTH negative 1 -- a cutoff earlier than every posting sees nothing."""
    _, _, t1, _ = await _seed_two_entries(pool, fund_id, portfolio_id, accounts)
    view = await ibor_view.compute_ibor_view(
        pool, fund_id=fund_id, cutoff=t1 - timedelta(microseconds=1)
    )
    assert view.balances == {}


async def test_ibor_view_cutoff_just_before_later_posting_excludes_it(
    pool, fund_id, portfolio_id, accounts
):
    """DEEPEN-DEPTH negative 2 -- cutoff is inclusive (posted_at <= cutoff): one
    microsecond before t2 must not include the 50.00 posting."""
    debit_code, credit_code, _, t2 = await _seed_two_entries(pool, fund_id, portfolio_id, accounts)
    view = await ibor_view.compute_ibor_view(
        pool, fund_id=fund_id, cutoff=t2 - timedelta(microseconds=1)
    )
    assert view.balances[debit_code] == Decimal("100.00")
    assert view.balances[credit_code] == Decimal("-100.00")


async def test_ibor_view_does_not_leak_postings_across_funds(pool, fund_id, portfolio_id, accounts):
    """DEEPEN-DEPTH negative 3 -- a different fund id sees none of this fund's postings."""
    _, _, _, t2 = await _seed_two_entries(pool, fund_id, portfolio_id, accounts)
    view = await ibor_view.compute_ibor_view(pool, fund_id=uuid4(), cutoff=t2)
    assert view.balances == {}


async def test_ibor_view_propagates_pool_failure_fail_closed():
    """DEEPEN-DEPTH failure injection -- a DB acquire failure must surface, never
    degrade into an empty (silently wrong) view."""

    class _BrokenPool:
        def acquire(self):
            raise ConnectionError("injected pool failure")

    with pytest.raises(ConnectionError, match="injected pool failure"):
        await ibor_view.compute_ibor_view(
            cast(asyncpg.Pool, _BrokenPool()), fund_id=uuid4(), cutoff=datetime.now(UTC)
        )


_IBOR_RECOMPUTE_BUDGET_MS = 1500.0


def _assert_recompute_within_budget(perf_budget, sample) -> None:
    # Async virtual delay lands on the wall reading only, so wall_ms is the gate.
    assert sample.wall_ms < _IBOR_RECOMPUTE_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_IBOR_RECOMPUTE_BUDGET_MS
    )


@pytest.mark.perf
async def test_ibor_view_recompute_best_of_within_budget(
    pool, fund_id, portfolio_id, accounts, perf_budget
):
    """DEEPEN-DEPTH perf -- best-of-5 wall time for a 2-entry recompute.
    Local measurement (2026-11-06): best-of-5 wall ~5-15 ms;
    the 1500 ms budget leaves >=100x headroom."""
    _, _, _, t2 = await _seed_two_entries(pool, fund_id, portfolio_id, accounts)
    sample = await perf_budget.best_of_async(
        lambda: ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=t2), n=5, warmup=1
    )
    _assert_recompute_within_budget(perf_budget, sample)


@pytest.mark.perf
async def test_ibor_view_recompute_budget_goes_red_under_10x_virtual_delay(
    pool, fund_id, portfolio_id, accounts, perf_budget
):
    """DEEPEN-DEPTH gate-red repro -- inject a virtual delay of 10x the budget; the
    same assertion must fail, proving it catches slowdowns."""
    from tests.support.perf_inject import virtual_delayed_async

    _, _, _, t2 = await _seed_two_entries(pool, fund_id, portfolio_id, accounts)
    slow = virtual_delayed_async(
        lambda: ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=t2),
        _IBOR_RECOMPUTE_BUDGET_MS * 10 / 1000,
    )
    sample = await perf_budget.best_of_async(slow, n=3, warmup=0)
    with pytest.raises(AssertionError, match="budget<"):
        _assert_recompute_within_budget(perf_budget, sample)
