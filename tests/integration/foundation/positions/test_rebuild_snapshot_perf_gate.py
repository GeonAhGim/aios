"""rebuild_snapshot perf gate-red reproduction (task-21512): virtual delay trips the budget."""

from __future__ import annotations

import time
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide
from src.foundation.positions.application.rebuild_snapshot import rebuild_snapshot
from tests.conftest import PerfBudget
from tests.integration.foundation.positions.rebuild_snapshot_fixtures import (
    _clock,
    _fill,
    _funding,
    _open,
    _RealPorts,
)
from tests.integration.foundation.positions.rebuild_snapshot_fixtures import (
    ports as ports,
)
from tests.support.perf_inject import virtual_delayed_async

_MAX_REBUILD_LATENCY_MS = 2000.0


async def _build_rebuild_fixture(pool, ports_):
    """Shared setup: open position + 2 fills + 1 funding, return (tenant_id, account_id, position_key)."""  # noqa: E501
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports_,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )
    await _fill(
        pool,
        ports_,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.SELL,
        quantity=Decimal("4"),
        price=Decimal("120"),
        fill_seq=2,
        order_id=order_id,
    )
    await _funding(
        pool,
        ports_,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("-2"),
        funding_id=str(uuid4()),
    )
    return tenant_id, account_id, position_key


async def _run_rebuild(
    pool_, ports_, tenant_id_, position_key_, perf_budget_, inject_s=None, mp=None
):
    """Run rebuild_snapshot(dry_run=True), optionally injecting virtual delay.

    Returns the PerfBudget sample from best_of_async.
    """

    async def _run() -> None:
        report = await rebuild_snapshot(
            position_key_,
            tenant_id=tenant_id_,
            asset_class=AssetClass.CRYPTO,
            journal=ports_.journal,
            snapshots=ports_.snapshots,
            pool=pool_,
            clock=_clock,
            dry_run=True,
        )
        assert report.drift == {}
        assert report.applied is False
        assert report.entries == 3

    if inject_s is not None:
        mp.setattr(
            asyncpg.Connection,
            "execute",
            virtual_delayed_async(asyncpg.Connection.execute, inject_s),
        )

    sample = await perf_budget_.best_of_async(_run, n=5, warmup=1)
    return sample


@pytest.mark.perf
async def test_over_budget_virtual_delay_trips_rebuild_assertion(
    pool, ports, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gate-red reproduction: 2.5초 가상 지연은 같은 단언을 적색으로 만든다."""
    tenant_id, account_id, position_key = await _build_rebuild_fixture(pool, ports)

    sample = await _run_rebuild(
        pool, ports, tenant_id, position_key, perf_budget, inject_s=2.5, mp=monkeypatch
    )
    with pytest.raises(AssertionError):
        assert sample.wall_ms < _MAX_REBUILD_LATENCY_MS


@pytest.mark.perf
async def test_virtual_delay_does_not_actually_sleep_in_rebuild(
    pool, ports, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """주입 실패: 가상 지연은 측정치에만 더해지고 실제 경과 시간은 늘리지 않는다."""
    tenant_id, account_id, position_key = await _build_rebuild_fixture(pool, ports)

    started = time.perf_counter()
    sample = await _run_rebuild(
        pool, ports, tenant_id, position_key, perf_budget, inject_s=2.5, mp=monkeypatch
    )
    real_s = time.perf_counter() - started

    assert sample.wall_ms >= 2500.0, "가상 지연이 측정치에 반영되지 않음"
    assert real_s < 2.5 * 6, "virtual delay must not consume real wall time"


async def test_rebuild_snapshot_pool_failure_raises(
    pool: asyncpg.Pool, ports: _RealPorts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패주입 — pool.acquire() 가 예외를 던지면 rebuild_snapshot 도
    동일하게 전파해야 한다(의존성 결함 격리, fail-closed)."""
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )

    class _AcquireGuard:
        async def __aenter__(self) -> None:
            raise ConnectionError("simulated pool failure")

        async def __aexit__(self, *exc: object) -> None:
            pass

    def _failing_acquire(self: asyncpg.Pool, *, timeout: float | None = None) -> _AcquireGuard:
        return _AcquireGuard()

    monkeypatch.setattr(asyncpg.Pool, "acquire", _failing_acquire)

    with pytest.raises(ConnectionError, match="simulated pool failure"):
        await rebuild_snapshot(
            position_key,
            tenant_id=tenant_id,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            pool=pool,
            clock=_clock,
            dry_run=True,
        )
