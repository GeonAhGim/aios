# ruff: noqa: F811 -- imported fixtures are re-declared as test parameters
"""FA-12 close_period persisted-value D2/D3 evidence (split from test_ibor_abor.py)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.ledger.application import abor_snapshot, ibor_view
from src.foundation.ledger.application.abor_snapshot import AlreadyClosedError
from src.foundation.ledger.application.ibor_view import NaiveCutoffError
from tests.integration.foundation.ledger.ibor_abor_setup import (
    _account_id,
    _balanced_pair,
    _insert_dated_entry,
)
from tests.integration.foundation.ledger.test_ibor_abor import (  # noqa: F401  (fixtures)
    accounts,
    fund_id,
    portfolio_id,
)


async def test_close_period_persisted_value_negative_failure_and_gate_red(
    pool, fund_id, portfolio_id, accounts
):
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
        lines=_balanced_pair(debit_code, credit_code, Decimal("42.00")),
        account_ids=account_ids,
    )
    as_of_date = date(2026, 9, 1)
    closed = await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
    )
    expected = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)
    assert closed.balances == expected.balances

    # Negative 1: a naive closing cutoff is refused.
    with pytest.raises(NaiveCutoffError):
        await abor_snapshot.close_period(
            pool,
            fund_id=fund_id,
            as_of_date=date(2026, 9, 10),
            closing_recorded_at=datetime(2026, 1, 1),
        )
    # Negative 2: re-closing the same date is refused and the persisted value is unchanged.
    with pytest.raises(AlreadyClosedError):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
        )
    # Negative 3: an unrelated date and an unknown fund never look closed.
    other = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=date(2026, 9, 10))
    assert other is None
    assert await abor_snapshot.get_snapshot(pool, fund_id=uuid4(), as_of_date=as_of_date) is None
    unchanged = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert unchanged is not None
    assert unchanged.snapshot_hash == closed.snapshot_hash

    # Failure injection: an IBOR recompute failure propagates and persists nothing.
    async def _boom(*args, **kwargs):
        raise RuntimeError("injected ibor failure")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ibor_view, "compute_ibor_view", _boom)
        with pytest.raises(RuntimeError, match="injected ibor failure"):
            await abor_snapshot.close_period(
                pool, fund_id=fund_id, as_of_date=date(2026, 9, 11), closing_recorded_at=cutoff
            )
    failed = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=date(2026, 9, 11))
    assert failed is None

    # Gate-red repro: a tampered balance no longer matches the recomputed value or the hash.
    tampered = {**closed.balances, debit_code: closed.balances[debit_code] + Decimal("0.01")}
    assert tampered != expected.balances
    assert abor_snapshot._snapshot_hash(tampered) != closed.snapshot_hash
