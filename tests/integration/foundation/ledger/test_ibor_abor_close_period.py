# ruff: noqa: F811 -- imported fixtures are re-declared as test parameters
"""FA-12 ABOR close_period depth evidence (task-22428): negatives, injection, perf, gate-red.

Split from test_ibor_abor.py (file-size ratchet); reuses its helpers and fixtures read-only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ledger.application import abor_snapshot, ibor_view
from src.foundation.ledger.application.ibor_view import NaiveCutoffError
from tests.integration.foundation.ledger.test_ibor_abor import (  # noqa: F401 -- fixtures
    _account_id,
    _balanced_pair,
    _insert_dated_entry,
    accounts,
    fund_id,
    portfolio_id,
)


async def _seed(pool, fund_id, portfolio_id, accounts, amount: str = "42.00") -> datetime:
    debit_code, credit_code = accounts
    account_ids = {
        debit_code: await _account_id(pool, debit_code),
        credit_code: await _account_id(pool, credit_code),
    }
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    await _insert_dated_entry(
        pool,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        posted_at=cutoff,
        lines=_balanced_pair(debit_code, credit_code, Decimal(amount)),
        account_ids=account_ids,
    )
    return cutoff


async def test_close_period_rejects_naive_cutoff_and_persists_nothing(
    pool, fund_id, portfolio_id, accounts
):
    """Negative: a naive closing_recorded_at is refused and no snapshot row is written."""
    await _seed(pool, fund_id, portfolio_id, accounts)
    as_of = date(2026, 9, 3)
    with pytest.raises(NaiveCutoffError):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of, closing_recorded_at=datetime(2026, 9, 3)
        )
    assert await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of) is None


async def test_close_period_does_not_leak_snapshot_to_other_fund_or_date(
    pool, fund_id, portfolio_id, accounts
):
    """Negative: the snapshot is keyed by (fund_id, as_of_date) only."""
    cutoff = await _seed(pool, fund_id, portfolio_id, accounts)
    as_of = date(2026, 9, 4)
    await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of, closing_recorded_at=cutoff
    )
    other_fund = await abor_snapshot.get_snapshot(pool, fund_id=uuid4(), as_of_date=as_of)
    other_date = await abor_snapshot.get_snapshot(
        pool, fund_id=fund_id, as_of_date=as_of + timedelta(days=1)
    )
    assert other_fund is None
    assert other_date is None


async def test_close_period_of_empty_fund_persists_empty_balances(pool, fund_id):
    """Negative: a fund with no postings closes to an empty (not zero-filled) balance set."""
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    closed = await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=date(2026, 9, 5), closing_recorded_at=cutoff
    )
    assert closed.balances == {}
    reread = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=date(2026, 9, 5))
    assert reread is not None and reread.balances == {}


async def test_closed_snapshot_is_immutable_after_later_posting(
    pool, fund_id, portfolio_id, accounts
):
    """Adversarial: a posting after the cutoff must not change the persisted snapshot."""
    cutoff = await _seed(pool, fund_id, portfolio_id, accounts)
    as_of = date(2026, 9, 6)
    closed = await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of, closing_recorded_at=cutoff
    )
    await _seed(pool, fund_id, portfolio_id, accounts, amount="7.00")
    reread = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of)
    assert reread is not None
    assert reread.balances == closed.balances
    assert reread.snapshot_hash == closed.snapshot_hash


async def test_close_period_hash_gate_red_when_balances_tampered(
    pool, fund_id, portfolio_id, accounts
):
    """Gate-red reproduction: the persisted hash matches the balances, and corrupting the
    balances makes the very same equality fail (the assertion really detects drift)."""
    cutoff = await _seed(pool, fund_id, portfolio_id, accounts)
    closed = await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=date(2026, 9, 7), closing_recorded_at=cutoff
    )
    assert abor_snapshot._snapshot_hash(closed.balances) == closed.snapshot_hash
    tampered = dict(closed.balances)
    tampered[accounts[0]] += Decimal("0.01")
    with pytest.raises(AssertionError):
        assert abor_snapshot._snapshot_hash(tampered) == closed.snapshot_hash


async def test_close_period_propagates_recompute_failure_and_persists_nothing(
    pool, fund_id, portfolio_id, accounts, monkeypatch
):
    """Failure injection: if the IBOR recompute fails, close_period surfaces the error
    unchanged and leaves no snapshot behind (fail-closed)."""
    cutoff = await _seed(pool, fund_id, portfolio_id, accounts)

    async def boom(*_a: object, **_k: object) -> None:
        raise ConnectionError("injected recompute failure")

    monkeypatch.setattr(ibor_view, "compute_ibor_view", boom)
    as_of = date(2026, 9, 8)
    with pytest.raises(ConnectionError, match="injected recompute failure"):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of, closing_recorded_at=cutoff
        )
    monkeypatch.undo()
    assert await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of) is None


@pytest.mark.perf
async def test_close_period_best_of_wall_within_budget_and_10x_delay_fires_red(
    pool, fund_id, portfolio_id, accounts, perf_budget, monkeypatch
):
    """Perf + gate-red. Budget 1.5s wall per close_period (1 posting; same order as the
    existing ibor_view budget, no existing budget changed). Local measurement
    (2026-11-06): best-of-5 wall is tens of ms, i.e. >=30x headroom. Red: a virtual
    10x-budget delay (15s, no real sleep) on every `Connection.fetch` must breach the
    same assertion."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

    budget_ms = 1500.0
    cutoff = await _seed(pool, fund_id, portfolio_id, accounts)
    day = {"n": 0}

    async def run() -> object:
        day["n"] += 1  # unique as_of_date per call (WORM, UNIQUE per fund/date)
        return await abor_snapshot.close_period(
            pool,
            fund_id=fund_id,
            as_of_date=date(2025, 1, 1) + timedelta(days=day["n"]),
            closing_recorded_at=cutoff,
        )

    green = await perf_budget.samples_async(run, n=5)
    best_ms = min(s.wall_ms for s in green)
    assert best_ms < budget_ms, f"best-of-5 wall={best_ms:.1f}ms budget<{budget_ms}ms"

    reset_virtual_totals()
    monkeypatch.setattr(
        asyncpg.Connection,
        "fetch",
        virtual_delayed_async(asyncpg.Connection.fetch, budget_ms * 10 / 1000),
    )
    slow = await perf_budget.samples_async(run, n=2)
    slow_best_ms = min(s.wall_ms for s in slow)
    assert slow_best_ms >= budget_ms * 10, f"injection not observed: {slow_best_ms:.1f}ms"
    assert not slow_best_ms < budget_ms
