"""LB-13 `record_funding_fee` 지연 단언 음성/주입 테스트 (task-17793, task-11539 감사 후속).

`test_record_funding_fee.py`(500줄 ratchet 여유 확보)에서 분리. 같은 latency 단언을
재사용해 (1) 짧은 가상 지연은 녹색, (2) 예산 초과 가상 지연은 적색, (3) 가상 지연이
실제 sleep 없이 측정치에만 더해짐을 검증한다. 가상 지연은
`tests/support/perf_inject.virtual_delayed_async`를 쓴다(실제 sleep/busy-wait 없음).
"""

from __future__ import annotations

import time
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.foundation.positions.application.record_funding_fee import record_funding_fee
from tests.conftest import PerfBudget
from tests.integration.foundation.positions.test_record_funding_fee import (
    _MAX_FUNDING_LATENCY_MS,
    _clock,
    _command,
    _open,
    _RealPorts,
)
from tests.support.perf_inject import virtual_delayed_async


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def _best_latency_ms(pool, ports_, perf_budget: PerfBudget, inject_s: float | None, mp):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("-5"),
    )
    if inject_s is not None:
        mp.setattr(
            asyncpg.Connection,
            "execute",
            virtual_delayed_async(asyncpg.Connection.execute, inject_s),
        )

    async def _run() -> None:
        async with pool.acquire() as conn, conn.transaction():
            await record_funding_fee(
                conn,
                command,
                asset_class=AssetClass.CRYPTO,
                journal=ports_.journal,
                snapshots=ports_.snapshots,
                audit=ports_.audit,
                clock=_clock,
            )

    # Each call needs a fresh funding_id, otherwise best-of repeats hit idempotency replay.
    base = command

    async def _run_fresh() -> None:
        nonlocal command
        command = _command(
            tenant_id=base.tenant_id,
            account_id=base.account_id,
            position_key=base.position_key,
            amount=Decimal("-5"),
        )
        await _run()

    sample = await perf_budget.best_of_async(_run_fresh, n=5, warmup=1)
    return sample


@pytest.mark.perf
async def test_short_virtual_delay_stays_within_latency_budget(
    pool, ports, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """음성 1: 10ms 가상 지연은 sanity 상한(2000ms)을 넘지 않아 단언이 녹색이다."""
    sample = await _best_latency_ms(pool, ports, perf_budget, 0.01, monkeypatch)
    assert sample.wall_ms < _MAX_FUNDING_LATENCY_MS


@pytest.mark.perf
async def test_over_budget_virtual_delay_trips_latency_assertion(
    pool, ports, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """음성 2/gate-red: 예산 초과 가상 지연(2.5s)은 같은 단언을 적색으로 만든다."""
    sample = await _best_latency_ms(pool, ports, perf_budget, 2.5, monkeypatch)
    with pytest.raises(AssertionError):
        assert sample.wall_ms < _MAX_FUNDING_LATENCY_MS


@pytest.mark.perf
async def test_virtual_delay_does_not_actually_sleep(
    pool, ports, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """음성 3/주입 실패: 가상 지연은 측정치에만 더해지고 실제 경과 시간은 늘리지 않는다."""
    started = time.perf_counter()
    sample = await _best_latency_ms(pool, ports, perf_budget, 2.5, monkeypatch)
    real_s = time.perf_counter() - started
    assert sample.wall_ms >= 2500.0
    assert real_s < 2.5 * 6, "virtual delay must not consume real wall time"
