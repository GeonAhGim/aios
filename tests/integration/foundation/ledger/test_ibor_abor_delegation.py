# ruff: noqa: F811  # pytest fixtures imported from test_ibor_abor are re-declared as test params
"""FA-12 IBOR `core.bitemporal.as_of` delegation -- DEEPEN-DEPTH evidence (negative /
failure injection / gate-red / perf) for
`test_ibor_abor.py::test_compute_ibor_view_delegates_to_core_bitemporal_as_of`.
Split from test_ibor_abor.py to stay under the 500-line ratchet; fixtures and helpers
are reused from that module unchanged."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

import src.core.bitemporal as bitemporal
from src.foundation.ledger.application import ibor_view
from tests.integration.foundation.ledger.test_ibor_abor import (  # noqa: F401
    _account_id,
    _balanced_pair,
    _insert_dated_entry,
    accounts,
    fund_id,
    portfolio_id,
)

_PATCH_TARGET = "src.foundation.ledger.application.ibor_view.bitemporal_as_of"


async def _seed(pool, fund_id, portfolio_id, accounts, posted_at, amount: str) -> None:
    debit_code, credit_code = accounts
    await _insert_dated_entry(
        pool,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        posted_at=posted_at,
        lines=_balanced_pair(debit_code, credit_code, Decimal(amount)),
        account_ids={
            debit_code: await _account_id(pool, debit_code),
            credit_code: await _account_id(pool, credit_code),
        },
    )


async def test_delegation_naive_cutoff_never_reaches_the_kernel(monkeypatch):
    """Negative 1: a naive cutoff is rejected before the pool or the kernel is touched."""
    calls: list[object] = []
    monkeypatch.setattr(_PATCH_TARGET, lambda *a, **k: calls.append(k) or [])
    with pytest.raises(ibor_view.NaiveCutoffError):
        await ibor_view.compute_ibor_view(
            cast(asyncpg.Pool, None), fund_id=uuid4(), cutoff=datetime(2026, 1, 1)
        )
    assert calls == []


async def test_delegation_cutoff_before_posting_gets_kernel_call_but_no_balance(
    monkeypatch, pool, fund_id, portfolio_id, accounts
):
    """Negative 2: a posting after the cutoff is invisible; the kernel is still delegated to."""
    posted = datetime.now(UTC) - timedelta(hours=1)
    await _seed(pool, fund_id, portfolio_id, accounts, posted, "7.00")
    seen: list[int] = []
    real = bitemporal.as_of

    def spy(records, *, valid_time, tx_time):
        seen.append(len(records))
        return real(records, valid_time=valid_time, tx_time=tx_time)

    monkeypatch.setattr(_PATCH_TARGET, spy)
    result = await ibor_view.compute_ibor_view(
        pool, fund_id=fund_id, cutoff=posted - timedelta(minutes=1)
    )
    assert len(seen) == 1
    assert result.balances == {}


async def test_delegation_kernel_verdict_is_authoritative_not_a_local_comparison(
    monkeypatch, pool, fund_id, portfolio_id, accounts
):
    """Negative 3 + gate-red: a kernel stub that hides every record empties the view even
    though the postings are <= cutoff -- a local `posted_at <= cutoff` reimplementation
    would still show 7.00, so the delegation spy assertion would be what catches it."""
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    await _seed(pool, fund_id, portfolio_id, accounts, cutoff, "7.00")
    monkeypatch.setattr(_PATCH_TARGET, lambda records, *, valid_time, tx_time: [])
    hidden = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)
    assert hidden.balances == {}
    monkeypatch.undo()
    shown = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)
    assert shown.balances[accounts[0]] == Decimal("7.00")


async def test_delegation_kernel_failure_propagates_fail_closed(
    monkeypatch, pool, fund_id, portfolio_id, accounts
):
    """Failure injection: a kernel error surfaces; no partial/empty view is returned."""
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    await _seed(pool, fund_id, portfolio_id, accounts, cutoff, "7.00")

    def boom(records, *, valid_time, tx_time):
        raise RuntimeError("injected bitemporal failure")

    monkeypatch.setattr(_PATCH_TARGET, boom)
    with pytest.raises(RuntimeError, match="injected bitemporal failure"):
        await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)


@pytest.mark.perf
async def test_delegated_ibor_view_budget_and_10x_delay_red(
    pool, fund_id, portfolio_id, accounts, perf_budget, monkeypatch
):
    """Perf: compute_ibor_view over one posting pair, best-of-3 wall_ms (real DB round
    trip), local measurement ~2 ms vs a 1500ms budget (>30x headroom). Gate-red: a virtual
    delay (>10x the measurement) on the delegated kernel must breach it."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    budget_ms = 1500.0
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    await _seed(pool, fund_id, portfolio_id, accounts, cutoff, "7.00")

    async def _one() -> None:
        await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)

    reset_virtual_totals()
    ok = await perf_budget.best_of_async(_one, n=3, warmup=1)
    assert ok.wall_ms < budget_ms, perf_budget.describe(ok, budget_ms=budget_ms)

    monkeypatch.setattr(_PATCH_TARGET, virtual_delayed_sync(bitemporal.as_of, budget_ms / 1000))
    slow = await perf_budget.best_of_async(_one, n=3, warmup=1)
    reset_virtual_totals()
    assert slow.wall_ms >= budget_ms, "injected delay did not breach the budget"
