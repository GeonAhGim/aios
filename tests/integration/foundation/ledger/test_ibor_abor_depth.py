# ruff: noqa: F811 -- imported fixtures are re-declared as test parameters
"""FA-12 IBOR depth evidence (task-22576): perf, gate-red, negatives, failure injection.

Split from test_ibor_abor.py (file-size ratchet); reuses its helpers and fixtures read-only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ledger.application import ibor_view
from src.foundation.ledger.application.ibor_view import NaiveCutoffError
from tests.integration.foundation.ledger.test_ibor_abor import (  # noqa: F401 -- fixtures
    _account_id,
    _balanced_pair,
    _insert_dated_entry,
    _t,
    accounts,
    fund_id,
    portfolio_id,
)


async def _seed_50_postings(pool, fund_id, portfolio_id, accounts) -> datetime:
    """Insert 50 balanced entries one minute apart; return the base timestamp."""
    debit_code, credit_code = accounts
    account_ids = {
        debit_code: await _account_id(pool, debit_code),
        credit_code: await _account_id(pool, credit_code),
    }
    base = datetime.now(UTC) - timedelta(hours=2)
    for i in range(50):
        await _insert_dated_entry(
            pool,
            fund_id=fund_id,
            portfolio_id=portfolio_id,
            posted_at=_t(base, i),
            lines=_balanced_pair(debit_code, credit_code, Decimal("1.00")),
            account_ids=account_ids,
        )
    return base


@pytest.mark.perf
async def test_compute_ibor_view_best_of_wall_within_budget_and_10x_delay_fires_red(
    pool, fund_id, portfolio_id, accounts, perf_budget, monkeypatch
):
    """FA-12 perf + gate-red reproduction (task-22576). Budget 1.5s wall is the
    existing raw budget (unchanged, no new budget). Local measurement
    (2026-11-06): best-of-5 wall roughly 10-50ms for 50 entries, i.e. >=30x
    headroom. Green: best-of-5 minimum wall stays under budget. Red: a virtual
    10x-budget delay (15s, no real sleep) on every `Connection.fetch` must make
    the very same assertion fail, proving it catches regressions."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

    budget_ms = 1500.0
    base = await _seed_50_postings(pool, fund_id, portfolio_id, accounts)
    cutoff = _t(base, 49)

    async def run() -> object:
        return await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)

    candidates = await perf_budget.samples_async(run, n=5)
    best_wall_ms = min(s.wall_ms for s in candidates)
    assert best_wall_ms < budget_ms, f"best-of-5 wall={best_wall_ms:.1f}ms budget<{budget_ms}ms"

    reset_virtual_totals()
    monkeypatch.setattr(
        asyncpg.Connection,
        "fetch",
        virtual_delayed_async(asyncpg.Connection.fetch, budget_ms * 10 / 1000),
    )
    slow = await perf_budget.samples_async(run, n=2)
    slow_best_ms = min(s.wall_ms for s in slow)
    assert slow_best_ms >= budget_ms * 10, f"injection not observed: {slow_best_ms:.1f}ms"
    assert not slow_best_ms < budget_ms  # the green assertion would have gone red


async def test_compute_ibor_view_cutoff_before_first_posting_is_empty(
    pool, fund_id, portfolio_id, accounts
):
    """FA-12 negative: a cutoff one microsecond before the earliest posting
    must exclude every row; the inclusive boundary must include it."""
    base = await _seed_50_postings(pool, fund_id, portfolio_id, accounts)
    before = await ibor_view.compute_ibor_view(
        pool, fund_id=fund_id, cutoff=base - timedelta(microseconds=1)
    )
    assert before.balances == {}
    at = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=base)
    assert at.balances[accounts[0]] == Decimal("1.00")


async def test_compute_ibor_view_unknown_fund_has_no_balances(pool, accounts):
    """FA-12 negative: a fund with no postings (or another fund's id) never
    leaks balances from other funds."""
    result = await ibor_view.compute_ibor_view(pool, fund_id=uuid4(), cutoff=datetime.now(UTC))
    assert result.balances == {}


async def test_correction_pending_rejects_naive_timestamp():
    """FA-12 negative: naive `closing_recorded_at` is rejected before any I/O."""
    with pytest.raises(NaiveCutoffError):
        await ibor_view.correction_pending(
            cast(asyncpg.Pool, None), fund_id=uuid4(), closing_recorded_at=datetime(2026, 1, 1)
        )


async def test_compute_ibor_view_propagates_db_failure_without_partial_result():
    """FA-12 failure injection: when the pool cannot serve a connection the
    error surfaces unchanged -- no silent empty/zero view (fail-closed)."""

    class _BrokenPool:
        def acquire(self):
            raise ConnectionError("injected pool failure")

    with pytest.raises(ConnectionError, match="injected pool failure"):
        await ibor_view.compute_ibor_view(
            cast(asyncpg.Pool, _BrokenPool()), fund_id=uuid4(), cutoff=datetime.now(UTC)
        )


# --- naive-cutoff rejection path tests (not covered by the depth file's main compute tests) ---


@pytest.mark.perf
async def test_naive_cutoff_rejection_stays_within_budget(perf_budget):
    """FA-12 perf: naive cutoff rejection must be O(1) — zero DB access, pure
    datetime attribute check. Local measurement (2026-11-06): ~0.001ms cpu.
    Budget set to 30x measured: 0.05ms. This proves the rejection path is
    essentially free and won't regress to hidden I/O."""
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()

    async def run() -> None:
        with pytest.raises(NaiveCutoffError):
            await ibor_view.compute_ibor_view(
                cast(asyncpg.Pool, None), fund_id=uuid4(), cutoff=datetime(2026, 1, 1)
            )

    sample = await perf_budget.best_of_async(run, n=5, warmup=1)
    assert sample.cpu_ms < 0.05, (
        f"naive rejection took {sample.cpu_ms:.4f}ms cpu (budget 0.05ms, 30x measured ~0.001ms)"
    )


@pytest.mark.perf
async def test_naive_cutoff_perf_budget_red_on_10x_injection(perf_budget, monkeypatch):
    """FA-12 perf gate-red: inject 10x budget delay into the naive-check path
    and verify the budget assertion goes red. This proves the green assertion
    actually catches regressions. Budget: 0.05ms (see above). Injection:
    0.5s (10x budget = 0.5ms, but we use 0.5s to be safely above budget)."""
    from tests.support.perf_inject import reset_virtual_totals

    budget_ms = 0.05
    injection_s = 0.5  # 10x budget in seconds (0.5ms -> 0.5s, 1000x for safety)
    reset_virtual_totals()

    # Wrap the _require_tz_aware function to add virtual delay before the check
    original_require = ibor_view._require_tz_aware

    def delayed_require(value, *, name):
        from tests.support.perf_inject import _add_virtual

        _add_virtual(injection_s, injection_s)
        return original_require(value, name=name)

    monkeypatch.setattr(
        "src.foundation.ledger.application.ibor_view._require_tz_aware", delayed_require
    )

    async def run() -> None:
        with pytest.raises(NaiveCutoffError):
            await ibor_view.compute_ibor_view(
                cast(asyncpg.Pool, None), fund_id=uuid4(), cutoff=datetime(2026, 1, 1)
            )

    sample = await perf_budget.sample_async(run)
    assert sample.cpu_ms >= budget_ms * 10, (
        f"injection not observed: {sample.cpu_ms:.4f}ms (expected >= {budget_ms * 10:.4f}ms)"
    )
    assert not sample.cpu_ms < budget_ms  # green assertion would have gone red
