"""LB-7 ports protocol — PERF-detection (task-21972).

Budgets for `test_full_implementations_satisfy_their_ports` and
`test_dict_returning_fake_satisfies_isinstance_but_not_the_dto` live here (split from
test_ports_protocol.py to stay under the 500-line ratchet). Each budget assertion is
paired with a gate-red test injecting a 10x virtual delay (no real sleep/spin).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency, Money
from src.foundation.positions.contracts.v1 import JournalEntryType
from src.foundation.positions.ports.exchange_balance_source import ProviderBalanceSource
from src.foundation.positions.ports.fx_rate_source import FxRateSource
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.mark_price_source import MarkPriceSource
from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository
from tests.foundation.unit.positions.test_ports_protocol import (
    _DictReturningJournalRepo,
    _FullFxRateSource,
    _FullJournalRepo,
    _FullMarkPriceSource,
    _FullNavRepo,
    _FullProviderBalanceSource,
    _FullSnapshotRepo,
    _now,
)
from tests.support.perf_inject import virtual_delayed_async, virtual_delayed_sync


def _all_full_impls_satisfy_ports() -> bool:
    return (
        isinstance(_FullJournalRepo(), PositionJournalRepository)
        and isinstance(_FullSnapshotRepo(), SnapshotRepository)
        and isinstance(_FullNavRepo(), NavRepository)
        and isinstance(_FullMarkPriceSource(), MarkPriceSource)
        and isinstance(_FullFxRateSource(), FxRateSource)
        and isinstance(_FullProviderBalanceSource(), ProviderBalanceSource)
    )


@pytest.mark.perf
def test_full_implementations_port_check_within_budget(perf_budget) -> None:
    # Six construct+isinstance checks per call; batch=200 and best-of-5 keep the
    # Windows process_time tick (~15.6 ms) from collapsing or spiking the reading.
    sample = perf_budget.best_of(_all_full_impls_satisfy_ports, n=5, batch=200)
    assert sample.cpu_ms < 0.5, perf_budget.describe(sample, budget_ms=0.5)


@pytest.mark.perf
def test_full_implementations_port_check_budget_fails_under_10x_delay(perf_budget) -> None:
    """Gate-red: a 5 ms virtual delay (10x the 0.5 ms budget) per call turns the
    same budget assertion red, proving it can detect a slowdown."""
    slow = virtual_delayed_sync(_all_full_impls_satisfy_ports, 0.005)
    sample = perf_budget.best_of(slow, n=5, batch=20)
    assert sample.cpu_ms >= 5.0
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < 0.5, perf_budget.describe(sample, budget_ms=0.5)


def _append_kwargs() -> dict[str, object]:
    return dict(
        conn=None,
        position_key="acct-1:BTC/USDT",
        entry_type=JournalEntryType.FILL,
        qty_delta=Decimal("0.5"),
        price=Money(amount=Decimal("50000"), currency=Currency.USDT),
        fee=None,
        realized_pnl_base=Decimal("0"),
        fx_rate=None,
        fx_source=None,
        source_event_type="FILL",
        source_event_id=str(uuid4()),
        idempotency_key="fill:1:1",
        occurred_at=_now(),
    )


@pytest.mark.perf
async def test_dict_returning_fake_append_within_budget(perf_budget) -> None:
    fake = _DictReturningJournalRepo()

    # Budget: 5 ms wall per isinstance+append (async: virtual delay is
    # wall-only); batch/best-of defeat tick quantization.
    async def _call() -> object:
        assert isinstance(fake, PositionJournalRepository)
        return await fake.append(**_append_kwargs())

    sample = await perf_budget.best_of_async(_call, n=5, batch=200)
    assert sample.wall_ms < 5.0, perf_budget.describe(sample, budget_ms=5.0)


@pytest.mark.perf
async def test_dict_returning_fake_budget_fails_under_10x_delay(perf_budget) -> None:
    """Gate-red: a 50 ms virtual delay (10x the 5 ms budget) turns the same
    budget assertion red."""
    fake = _DictReturningJournalRepo()
    slow_append = virtual_delayed_async(fake.append, 0.05)

    async def _call() -> object:
        return await slow_append(**_append_kwargs())

    sample = await perf_budget.best_of_async(_call, n=5, batch=4)
    assert sample.wall_ms >= 50.0
    with pytest.raises(AssertionError):
        assert sample.wall_ms < 5.0, perf_budget.describe(sample, budget_ms=5.0)
