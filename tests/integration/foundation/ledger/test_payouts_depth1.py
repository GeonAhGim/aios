"""LC-15a / task-24830: missing payout performance evidence only.

Keep the existing test_payouts.py 30-capture / 3-second budget unchanged.
Negative, rollback and forged-reference evidence remains in that file.
Each trial rolls back so best-of measures creation, never the replay fast path.
The same assertion must reject a virtual repository delay of 10x the budget.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from src.foundation.ledger.application.payouts import schedule_payouts
from tests.integration.conftest import create_test_user
from tests.integration.foundation.ledger.test_payouts import (
    _WINDOW,
    _captured_hold,
    _RealPorts,
    _record_for,
)
from tests.support.perf_inject import virtual_delayed_async

_BUDGET_MS = 3000.0
_CAPTURE_COUNT = 30


@pytest.fixture
async def payout_trial(pool):
    ports = _RealPorts(pool)
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    captures = [
        _record_for(await _captured_hold(pool, ports, buyer, seller, Decimal("10.00")), seller)
        for _ in range(_CAPTURE_COUNT)
    ]
    period_start = min(record.captured_at for record in captures).date()
    now = max(record.captured_at for record in captures) + _WINDOW + timedelta(seconds=1)

    async def run():
        async with pool.acquire() as conn:
            transaction = conn.transaction()
            await transaction.start()
            try:
                batches = await schedule_payouts(
                    conn,
                    captures,
                    period_start=period_start,
                    period_end=period_start + timedelta(days=1),
                    now=now,
                    actor_subject_id=None,
                    settlement_window=_WINDOW,
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=ports.clock,
                    payouts=ports.payouts,
                )
                assert len(batches) == 1
                batch = batches[0]
                assert batch.amount == Decimal("8.50") * _CAPTURE_COUNT
                assert batch.state == "RELEASED"
                assert set(batch.capture_entry_ids) == {record.entry_id for record in captures}
                assert (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM ledger_payout_item WHERE batch_id = $1",
                        batch.batch_id,
                    )
                    == _CAPTURE_COUNT
                )
                return batch
            finally:
                await transaction.rollback()

    return ports, run


def _assert_budget(sample):
    assert sample.wall_ms < _BUDGET_MS, (
        f"payout creation wall={sample.wall_ms:.3f}ms exceeds {_BUDGET_MS:.0f}ms"
    )


@pytest.mark.perf
async def test_payout_creation_best_of_meets_existing_budget(payout_trial, perf_budget):
    _, run = payout_trial
    sample = await perf_budget.best_of_async(run, n=5)
    print(f"payout creation: cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms")
    _assert_budget(sample)


@pytest.mark.perf
async def test_payout_creation_budget_rejects_10x_virtual_delay(
    payout_trial, perf_budget, monkeypatch
):
    ports, run = payout_trial
    monkeypatch.setattr(
        ports.payouts,
        "create_batch",
        virtual_delayed_async(ports.payouts.create_batch, 10 * _BUDGET_MS / 1000),
    )
    sample = await perf_budget.best_of_async(run, n=5)
    assert sample.wall_ms >= 10 * _BUDGET_MS
    with pytest.raises(AssertionError, match="payout creation wall=.*exceeds 3000ms"):
        _assert_budget(sample)
