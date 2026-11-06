# ruff: noqa: F811 -- imported fixtures are re-declared as test parameters
"""FA-12 ABOR 재마감 거부 D3 깊이 보강 (음성·실패 주입·gate-red·perf).

Split from test_ibor_abor.py (file-size ratchet); reuses its helpers and fixtures read-only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from src.foundation.ledger.application import abor_snapshot, ibor_view
from src.foundation.ledger.application.abor_snapshot import AlreadyClosedError
from src.foundation.ledger.application.ibor_view import NaiveCutoffError
from tests.integration.foundation.ledger.test_ibor_abor import (  # noqa: F401 -- fixtures
    _account_id,
    _balanced_pair,
    _insert_dated_entry,
    accounts,
    fund_id,
    portfolio_id,
)


async def _close_once(pool, fund_id, portfolio_id, accounts, as_of_date, amount="10.00"):
    """Seed one dated entry and close `as_of_date`; returns (cutoff, view, ids)."""
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
    closed = await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
    )
    return cutoff, closed, account_ids


async def test_reclose_with_different_cutoff_is_rejected_and_keeps_original_hash(
    pool, fund_id, portfolio_id, accounts
):
    """negative 1: 다른 cutoff(더 늦은 closing_recorded_at)로 같은 날짜를 다시 마감해도
    거부되고, 최초 스냅샷의 hash/closing_recorded_at 은 바뀌지 않는다."""
    as_of_date = date(2026, 9, 3)
    cutoff, closed, _ = await _close_once(pool, fund_id, portfolio_id, accounts, as_of_date)

    with pytest.raises(AlreadyClosedError):
        await abor_snapshot.close_period(
            pool,
            fund_id=fund_id,
            as_of_date=as_of_date,
            closing_recorded_at=cutoff + timedelta(minutes=30),
        )
    reread = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert reread is not None
    assert reread.snapshot_hash == closed.snapshot_hash
    assert reread.closing_recorded_at == cutoff


async def test_reclose_after_new_posting_is_rejected_and_flags_correction(
    pool, fund_id, portfolio_id, accounts
):
    """negative 2: 마감 후 새 posting 이 생겨도 재마감으로 값을 고칠 수 없다 -- 거부되고
    스냅샷 잔액은 그대로이며 정정 필요(is_correction_pending)만 표시된다."""
    as_of_date = date(2026, 9, 4)
    cutoff, closed, account_ids = await _close_once(
        pool, fund_id, portfolio_id, accounts, as_of_date
    )
    debit_code, credit_code = accounts
    await _insert_dated_entry(
        pool,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        posted_at=datetime.now(UTC),
        lines=_balanced_pair(debit_code, credit_code, Decimal("99.00")),
        account_ids=account_ids,
    )

    with pytest.raises(AlreadyClosedError):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=datetime.now(UTC)
        )
    reread = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert reread is not None
    assert reread.balances == closed.balances
    assert reread.balances[debit_code] == Decimal("10.00")
    assert await abor_snapshot.is_correction_pending(pool, fund_id=fund_id, as_of_date=as_of_date)


async def test_close_period_with_naive_cutoff_is_rejected_and_persists_nothing(
    pool, fund_id, portfolio_id, accounts
):
    """negative 3: naive closing_recorded_at 은 NaiveCutoffError 로 거부되고 스냅샷이
    생기지 않는다(fail-closed). 이후 정상 마감은 그대로 성공한다."""
    as_of_date = date(2026, 9, 5)
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)

    with pytest.raises(NaiveCutoffError):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=naive
        )
    assert await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date) is None

    _, closed, _ = await _close_once(pool, fund_id, portfolio_id, accounts, as_of_date)
    assert closed.as_of_date == as_of_date


async def test_close_period_failure_injection_leaves_no_snapshot_and_allows_retry(
    monkeypatch, pool, fund_id, portfolio_id, accounts
):
    """failure injection: 재계산(compute_ibor_view)이 도중에 실패하면 스냅샷이 남지 않고,
    장애 해소 후 같은 날짜를 AlreadyClosedError 없이 마감할 수 있다."""
    as_of_date = date(2026, 9, 6)
    real = ibor_view.compute_ibor_view

    async def _boom(*args, **kwargs):
        raise RuntimeError("injected ibor failure")

    monkeypatch.setattr(ibor_view, "compute_ibor_view", _boom)
    with pytest.raises(RuntimeError, match="injected ibor failure"):
        await abor_snapshot.close_period(
            pool,
            fund_id=fund_id,
            as_of_date=as_of_date,
            closing_recorded_at=datetime.now(UTC) - timedelta(hours=1),
        )
    assert await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date) is None

    monkeypatch.setattr(ibor_view, "compute_ibor_view", real)
    _, closed, _ = await _close_once(pool, fund_id, portfolio_id, accounts, as_of_date)
    assert closed.as_of_date == as_of_date


async def test_reclose_guard_is_not_hollow_distinct_date_is_not_rejected(
    pool, fund_id, portfolio_id, accounts
):
    """gate-red 재현(입력 파괴): 재마감 거부 단언이 실제로 잡는지 -- 같은 날짜 입력을
    다른 날짜로 망가뜨리면 pytest.raises(AlreadyClosedError) 단언이 적색(Failed)이 된다."""
    cutoff, _, _ = await _close_once(pool, fund_id, portfolio_id, accounts, date(2026, 9, 7))

    with pytest.raises(pytest.fail.Exception):
        with pytest.raises(AlreadyClosedError):
            await abor_snapshot.close_period(
                pool, fund_id=fund_id, as_of_date=date(2026, 9, 8), closing_recorded_at=cutoff
            )


# 로컬 실측(Windows, 공유 Postgres): 재마감 거부 1회 cpu_ms 는 process_time 양자화(약 15.6ms)
# 미만(0 수렴). 예산 500ms 는 양자화 단위의 30배 이상 여유 -- CPU 시간 기준(best-of-5).
_RECLOSE_REJECT_BUDGET_MS = 500.0


@pytest.mark.perf
async def test_reclose_rejection_stays_within_budget_and_goes_red_with_10x_delay(
    pool, fund_id, portfolio_id, accounts, perf_budget
):
    """perf: 재마감 거부 경로 best-of-5 cpu 가 예산(500ms) 미만. 가상 지연 10배(5000ms)를
    주입하면 같은 단언이 적색이 되어 단언이 허수가 아님을 보증한다."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

    as_of_date = date(2026, 9, 9)
    cutoff, _, _ = await _close_once(pool, fund_id, portfolio_id, accounts, as_of_date)

    async def _reject() -> None:
        with pytest.raises(AlreadyClosedError):
            await abor_snapshot.close_period(
                pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
            )

    reset_virtual_totals()
    sample = await perf_budget.best_of_async(_reject, n=5, warmup=1)
    assert sample.cpu_ms < _RECLOSE_REJECT_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_RECLOSE_REJECT_BUDGET_MS
    )

    slow = await perf_budget.best_of_async(
        virtual_delayed_async(_reject, _RECLOSE_REJECT_BUDGET_MS * 10 / 1000), n=5, warmup=0
    )
    reset_virtual_totals()
    assert slow.wall_ms >= _RECLOSE_REJECT_BUDGET_MS * 10
    assert not slow.wall_ms < _RECLOSE_REJECT_BUDGET_MS
