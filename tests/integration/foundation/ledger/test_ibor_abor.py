"""FA-12 `application/{ibor_view,abor_snapshot}.py` 통합테스트 -- 실 DB
(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-12
(task-2059 decision, PM 2026-09-08).

DoD: IBOR은 `posted_at` cutoff로 필터링한 재계산이 결정론적이어야 하고,
ABOR 마감은 (1) 그 재계산 값을 그대로 영속화하고 (2) 같은
`(fund_id, as_of_date)` 재마감을 거부하며(adversarial) (3) 마감 이후 새
posting이 생기면 `is_correction_pending`이 이를 표시해야 한다.

`ledger_journal_entry`/`ledger_posting_line`은 WORM이라 `posted_at`을
호출자가 임의로 고를 수 있는 공개 API가 없다(실제 코드는 항상
`datetime.now()`) -- `ibor_abor_setup._insert_dated_entry`가 실제
`postgres_journal_repository.append`와 동일한 삽입 형태를 감사 이벤트부터
직접 재현해, "과거 cutoff"를 결정론적으로 통제한다.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import asyncpg
import pytest

import src.core.bitemporal as bitemporal
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.ledger.application import abor_snapshot, ibor_view
from src.foundation.ledger.application.abor_snapshot import (
    AlreadyClosedError,
)
from src.foundation.ledger.application.ibor_view import NaiveCutoffError
from src.foundation.ledger.contracts.v1 import (
    UserSub,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.entities.conftest import build_hierarchy
from tests.integration.foundation.ledger.ibor_abor_setup import (
    _account_id as _account_id,
)
from tests.integration.foundation.ledger.ibor_abor_setup import (
    _balanced_pair as _balanced_pair,
)
from tests.integration.foundation.ledger.ibor_abor_setup import (
    _create_user_ledger_account as _create_user_ledger_account,
)
from tests.integration.foundation.ledger.ibor_abor_setup import (
    _insert_dated_entry as _insert_dated_entry,
)
from tests.integration.foundation.ledger.ibor_abor_setup import (
    _signed_delta as _signed_delta,
)
from tests.integration.foundation.ledger.ibor_abor_setup import (
    _t as _t,
)


@pytest.fixture
async def fund_id(pool: asyncpg.Pool) -> UUID:
    tenant_id = await create_test_tenant(pool)
    repo = PostgresEntityRepository(pool)
    hierarchy = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    return hierarchy.fund.fund_id


@pytest.fixture
async def portfolio_id(pool: asyncpg.Pool, fund_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT portfolio_id FROM portfolio WHERE fund_id = $1", fund_id
        )
    assert value is not None
    return value


@pytest.fixture
async def accounts(pool: asyncpg.Pool) -> tuple[str, str]:
    debit_code = await _create_user_ledger_account(pool, UserSub.RECEIVABLE, allow_negative=True)
    credit_code = await _create_user_ledger_account(pool, UserSub.AVAILABLE, allow_negative=True)
    return debit_code, credit_code


async def test_ibor_view_recomputes_deterministically_as_of_cutoff(
    pool, fund_id, portfolio_id, accounts
):
    debit_code, credit_code = accounts
    account_ids = {
        debit_code: await _account_id(pool, debit_code),
        credit_code: await _account_id(pool, credit_code),
    }
    base = datetime.now(UTC) - timedelta(hours=1)
    t1, t2 = _t(base, 0), _t(base, 10)

    await _insert_dated_entry(
        pool,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        posted_at=t1,
        lines=_balanced_pair(debit_code, credit_code, Decimal("100.00")),
        account_ids=account_ids,
    )
    await _insert_dated_entry(
        pool,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        posted_at=t2,
        lines=_balanced_pair(debit_code, credit_code, Decimal("50.00")),
        account_ids=account_ids,
    )

    as_of_t1 = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=t1)
    assert as_of_t1.balances[debit_code] == Decimal("100.00")
    assert as_of_t1.balances[credit_code] == Decimal("-100.00")

    as_of_t2 = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=t2)
    assert as_of_t2.balances[debit_code] == Decimal("150.00")
    assert as_of_t2.balances[credit_code] == Decimal("-150.00")

    # 재계산은 결정론적이다 -- 같은 cutoff는 언제 다시 물어도 같은 값.
    replay = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=t1)
    assert replay.balances == as_of_t1.balances


@pytest.mark.perf
async def test_compute_ibor_view_stays_within_latency_budget_for_50_postings(
    pool, fund_id, portfolio_id, accounts
):
    """수치 성능 단언 (DEPTH_FA 감사, task-2059/FA-12 유일 미달 항목) --
    50개 분개(100 posting line)에 대한 재계산이 예산(1.5s) 안에 끝나야
    한다. 새 테이블 없이 매번 WORM 저널 전체를 `posted_at <= cutoff`로
    다시 접는 설계(§9 FA-12 decision)라 상한이 없으면 회귀를 놓친다."""
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

    started = time.perf_counter()
    result = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=_t(base, 49))
    elapsed_s = time.perf_counter() - started

    assert elapsed_s < 1.5, f"compute_ibor_view over 50 entries took {elapsed_s:.3f}s (budget 1.5s)"
    assert result.balances[debit_code] == Decimal("50.00")


async def test_compute_ibor_view_delegates_to_core_bitemporal_as_of(
    monkeypatch, pool, fund_id, portfolio_id, accounts
):
    """FA-12/task-2059 decision item 6 -- 재구현 금지: `posted_at <= cutoff`
    가시성 판정은 FA-9 `core.bitemporal.as_of` 커널에 위임해야 한다. 실제
    커널을 스파이로 감싸 호출을 증명한다(DC-21 `test_point_in_time.py`와
    동일한 패턴) -- `ibor_view.py`가 이 위임을 걷어내고 로컬 비교로
    재구현하면 이 assertion만 깨진다(값 검증 테스트는 우연히 통과할 수 있다)."""
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
        lines=_balanced_pair(debit_code, credit_code, Decimal("7.00")),
        account_ids=account_ids,
    )

    calls: list[dict[str, object]] = []
    real_as_of = bitemporal.as_of

    def spy_as_of(records, *, valid_time, tx_time):
        calls.append({"valid_time": valid_time, "tx_time": tx_time})
        return real_as_of(records, valid_time=valid_time, tx_time=tx_time)

    monkeypatch.setattr("src.foundation.ledger.application.ibor_view.bitemporal_as_of", spy_as_of)

    result = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)

    assert len(calls) == 1, "compute_ibor_view did not delegate to core.bitemporal.as_of"
    assert calls[0] == {"valid_time": cutoff, "tx_time": cutoff}
    assert result.balances[debit_code] == Decimal("7.00")


async def test_ibor_view_rejects_naive_cutoff():
    with pytest.raises(NaiveCutoffError):
        await ibor_view.compute_ibor_view(
            pool=cast(asyncpg.Pool, None), fund_id=uuid4(), cutoff=datetime(2026, 1, 1)
        )


class _NoPool:
    def acquire(self) -> None:
        raise AssertionError("pool.acquire() reached")


_POOL = cast(asyncpg.Pool, _NoPool())


@pytest.mark.parametrize("naive", [datetime(2026, 1, 1, 0, 0, 0, 1), datetime.min, datetime.max])
async def test_ibor_view_naive_cutoff_variants_rejected_before_pool(naive: datetime):
    with pytest.raises(NaiveCutoffError, match="cutoff"):
        await ibor_view.compute_ibor_view(_POOL, fund_id=uuid4(), cutoff=naive)


async def test_ibor_view_aware_cutoff_injected_pool_failure_propagates():
    with pytest.raises(AssertionError, match="acquire"):
        await ibor_view.compute_ibor_view(_POOL, fund_id=uuid4(), cutoff=datetime.now(UTC))


async def _reject_naive() -> None:
    with pytest.raises(NaiveCutoffError):
        await ibor_view.compute_ibor_view(_POOL, fund_id=uuid4(), cutoff=datetime.min)


@pytest.mark.perf
async def test_ibor_view_naive_rejection_budget_and_10x_delay_red(perf_budget, monkeypatch):
    """Perf: local best-of-3 ~0.1ms cpu, 50ms budget (>30x headroom). Gate-red: 10x delay."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()
    ok = await perf_budget.best_of_async(_reject_naive, n=3, warmup=1, batch=8)
    assert ok.cpu_ms < 50.0, perf_budget.describe(ok, budget_ms=50.0)
    guard = virtual_delayed_sync(ibor_view._require_tz_aware, 0.5)
    monkeypatch.setattr(ibor_view, "_require_tz_aware", guard)
    slow = await perf_budget.best_of_async(_reject_naive, n=3, warmup=1, batch=8)
    reset_virtual_totals()
    assert slow.cpu_ms >= 50.0, "injected delay did not breach the budget"


async def test_close_period_persists_the_recomputed_ibor_value(
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
    assert closed.closing_recorded_at == cutoff

    reread = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert reread is not None
    assert reread.balances == closed.balances
    assert reread.snapshot_hash == closed.snapshot_hash


async def test_close_period_rejects_reclose_of_same_as_of_date(
    pool, fund_id, portfolio_id, accounts
):
    """DoD(3) adversarial: 같은 (fund_id, as_of_date) 2회 마감은 두 번째가
    거부되어야 한다 -- UNIQUE 위반이 그대로 새지 않고 도메인 오류로 표면화."""
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
        lines=_balanced_pair(debit_code, credit_code, Decimal("10.00")),
        account_ids=account_ids,
    )
    as_of_date = date(2026, 9, 2)

    await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
    )

    with pytest.raises(AlreadyClosedError) as exc_info:
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
        )
    assert exc_info.value.fund_id == fund_id
    assert exc_info.value.as_of_date == as_of_date

    # 재마감 거부가 스냅샷 값을 건드리지 않았는지 확인.
    still_there = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert still_there is not None
    assert still_there.balances[debit_code] == Decimal("10.00")
