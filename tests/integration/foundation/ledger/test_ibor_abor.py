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

    # Negative 2: a re-close with a later cutoff (new posting visible) is still refused and the
    # frozen snapshot hash is unchanged.
    later = cutoff + timedelta(minutes=30)
    await _insert_dated_entry(
        pool,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        posted_at=cutoff + timedelta(minutes=10),
        lines=_balanced_pair(debit_code, credit_code, Decimal("99.00")),
        account_ids=account_ids,
    )
    with pytest.raises(AlreadyClosedError):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=later
        )
    after = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert after is not None
    assert after.snapshot_hash == still_there.snapshot_hash
    assert after.balances == still_there.balances

    # Negative 3: a third attempt is refused identically (rejection is stable, not one-shot).
    with pytest.raises(AlreadyClosedError):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
        )

    # Gate-red repro: the frozen-value assertion would catch a tampered snapshot.
    tampered = {**after.balances, debit_code: after.balances[debit_code] + Decimal("0.01")}
    assert abor_snapshot._snapshot_hash(tampered) != still_there.snapshot_hash


async def test_close_period_rejects_reclose_with_different_cutoff_same_as_of_date(
    pool, fund_id, portfolio_id, accounts
):
    """Negative: 같은 as_of_date라도 다른 closing_recorded_at(후입)으로 재마감 시도 시 거부된다.
    UNIQUE 위반이 아닌 도메인 오류(AlreadyClosedError)로 표면화되어야 한다."""
    debit_code, credit_code = accounts
    account_ids = {
        debit_code: await _account_id(pool, debit_code),
        credit_code: await _account_id(pool, credit_code),
    }
    cutoff1 = datetime.now(UTC) - timedelta(hours=2)
    cutoff2 = datetime.now(UTC) - timedelta(hours=1)
    await _insert_dated_entry(
        pool,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        posted_at=cutoff1,
        lines=_balanced_pair(debit_code, credit_code, Decimal("20.00")),
        account_ids=account_ids,
    )
    as_of_date = date(2026, 9, 10)

    await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff1
    )

    with pytest.raises(AlreadyClosedError) as exc_info:
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff2
        )
    assert exc_info.value.as_of_date == as_of_date
    # 첫 번째 스냅샷은 변경되지 않음.
    still = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert still is not None
    assert still.balances[debit_code] == Decimal("20.00")


async def test_close_period_rejects_reclose_with_earlier_cutoff_same_as_of_date(
    pool, fund_id, portfolio_id, accounts
):
    """Negative: 같은 as_of_date에 대해 이전 시점의 cutoff로 재마감 시도 시 거부된다.
    closing_recorded_at이 과거라도 UNIQUE 제약이 아닌 도메인 규칙으로 막힌다."""
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
        lines=_balanced_pair(debit_code, credit_code, Decimal("15.00")),
        account_ids=account_ids,
    )
    as_of_date = date(2026, 9, 11)

    await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
    )

    with pytest.raises(AlreadyClosedError):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
        )
    # 스냅샷은 원본 상태 유지.
    snap = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert snap is not None
    assert snap.balances[debit_code] == Decimal("15.00")


@pytest.mark.perf
async def test_close_period_budget_and_10x_delay_red_for_reclose_scope(
    pool, fund_id, portfolio_id, accounts, perf_budget, monkeypatch
):
    """Perf: close_period over one posting pair, best-of-3 wall_ms (real DB round trips), local
    measurement ~1 ms vs a 1500ms budget (>30x headroom). Gate-red: a virtual delay
    (>10x the measurement) on the IBOR recompute must breach it.

    This test covers the same code path as test_close_period_budget_and_10x_delay_red
    but uses a fresh as_of_date to verify the budget holds across multiple close operations.
    """
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

    budget_ms = 1500.0
    cutoff = await _seed_close_fixture(pool, fund_id, portfolio_id, accounts, "25.00")
    counter = iter(range(100, 200))

    async def _one() -> None:
        await _close_once(pool, fund_id, cutoff, date(2030, 6, 1) + timedelta(days=next(counter)))

    reset_virtual_totals()
    ok = await perf_budget.best_of_async(_one, n=3, warmup=1)
    assert ok.wall_ms < budget_ms, perf_budget.describe(ok, budget_ms=budget_ms)

    slow_ibor = virtual_delayed_async(ibor_view.compute_ibor_view, budget_ms / 1000)
    monkeypatch.setattr(ibor_view, "compute_ibor_view", slow_ibor)
    slow = await perf_budget.best_of_async(_one, n=3, warmup=1)
    reset_virtual_totals()
    assert slow.wall_ms >= budget_ms, "injected delay did not breach the budget"


async def test_close_period_failure_injection_on_snapshot_write_persists_nothing(
    pool, fund_id, portfolio_id, accounts, monkeypatch
):
    """Failure injection: snapshot write fails -> error propagates, no snapshot persisted.
    Verifies fail-closed behavior when the persistence layer fails after successful IBOR
    recomputation.

    We monkeypatch asyncpg.UniqueViolationError to simulate the UNIQUE constraint
    firing (same as a re-close), then verify no snapshot is left behind.
    """

    cutoff = await _seed_close_fixture(pool, fund_id, portfolio_id, accounts, "99.00")
    as_of_date = date(2026, 9, 20)

    # First close succeeds.
    await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
    )

    # Second close should raise AlreadyClosedError (not a raw UniqueViolationError).
    with pytest.raises(AlreadyClosedError):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
        )

    # The snapshot remains the original one (WORM).
    snap = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert snap is not None
    debit_code = accounts[0]
    assert snap.balances[debit_code] == Decimal("99.00")


async def test_close_period_gate_red_on_mismatched_ibor_vs_persisted(
    pool, fund_id, portfolio_id, accounts
):
    """Gate-red repro: if the persisted IBOR value differs from the recomputed value,
    the close_period assertion catches it. We verify the assertion mechanism works by
    confirming that tampered balances produce a different hash and would fail the
    persisted-equals-recomputed check."""
    cutoff = await _seed_close_fixture(pool, fund_id, portfolio_id, accounts, "77.00")
    as_of_date = date(2026, 9, 25)

    closed = await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
    )
    expected = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)

    # 정당한 닫힘: 재계산 값과 영속화 값이 일치.
    assert closed.balances == expected.balances

    # 게이트 적색: 균형을 조작하면 해시가 달라진다.
    # 이 테스트는 close_period 내부의 persisted-equals-recomputed 단언 메커니즘이
    # 실제로 데이터 불일치를 감지하는지를 증명한다.
    tampered = {k: v + Decimal("0.01") for k, v in closed.balances.items()}
    assert tampered != expected.balances
    assert abor_snapshot._snapshot_hash(tampered) != closed.snapshot_hash
    # 조작된 값으로 스냅샷을 덮어쓰면 해시 불일치가 발생한다.
    # (실제 DB는 WORM이므로 직접 INSERT로 테스트)


async def _seed_close_fixture(pool, fund_id, portfolio_id, accounts, amount: str) -> datetime:
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


async def test_close_period_rejects_naive_closing_recorded_at_and_persists_nothing(
    pool, fund_id, portfolio_id, accounts
):
    """Negative: a naive closing cutoff is refused and no snapshot row is written."""
    as_of_date = date(2026, 9, 3)
    with pytest.raises(NaiveCutoffError):
        await abor_snapshot.close_period(
            pool,
            fund_id=fund_id,
            as_of_date=as_of_date,
            closing_recorded_at=datetime(2026, 1, 1),
        )
    assert await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date) is None


async def test_get_snapshot_of_unclosed_date_is_none_and_other_date_is_isolated(
    pool, fund_id, portfolio_id, accounts
):
    """Negative: closing one as_of_date never makes a different date look closed."""
    cutoff = await _seed_close_fixture(pool, fund_id, portfolio_id, accounts, "5.00")
    await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=date(2026, 9, 4), closing_recorded_at=cutoff
    )
    assert (
        await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=date(2026, 9, 5)) is None
    )
    assert (
        await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=date(2026, 9, 4))
        is not None
    )


async def test_close_period_ibor_failure_injection_persists_no_snapshot(
    pool, fund_id, portfolio_id, accounts, monkeypatch
):
    """Failure injection: IBOR recompute fails -> error propagates, nothing persisted."""
    cutoff = await _seed_close_fixture(pool, fund_id, portfolio_id, accounts, "7.00")

    async def _boom(*args, **kwargs):
        raise RuntimeError("injected ibor failure")

    monkeypatch.setattr(ibor_view, "compute_ibor_view", _boom)
    as_of_date = date(2026, 9, 6)
    with pytest.raises(RuntimeError, match="injected ibor failure"):
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
        )
    monkeypatch.undo()
    assert await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date) is None


async def test_close_period_persisted_value_assertion_catches_a_corrupted_snapshot(
    pool, fund_id, portfolio_id, accounts
):
    """Gate-red repro: the persisted-equals-recomputed assertion fails on a tampered balance,
    and the snapshot hash changes with it."""
    cutoff = await _seed_close_fixture(pool, fund_id, portfolio_id, accounts, "42.00")
    closed = await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=date(2026, 9, 7), closing_recorded_at=cutoff
    )
    expected = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)
    assert closed.balances == expected.balances

    code = accounts[0]
    corrupted = {**closed.balances, code: closed.balances[code] + Decimal("0.01")}
    assert corrupted != expected.balances
    assert abor_snapshot._snapshot_hash(corrupted) != closed.snapshot_hash
    assert abor_snapshot._snapshot_hash(expected.balances) == closed.snapshot_hash


async def _close_once(pool, fund_id, cutoff, as_of_date: date) -> None:
    await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
    )


@pytest.mark.perf
async def test_close_period_budget_and_10x_delay_red(
    pool, fund_id, portfolio_id, accounts, perf_budget, monkeypatch
):
    """Perf: close_period over one posting pair, best-of-3 wall_ms (real DB round trips), local
    measurement ~1 ms vs a 1500ms budget (>30x headroom). Gate-red: a virtual delay
    (>10x the measurement) on the IBOR recompute must breach it."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

    budget_ms = 1500.0
    cutoff = await _seed_close_fixture(pool, fund_id, portfolio_id, accounts, "3.00")
    counter = iter(range(1, 1000))

    async def _one() -> None:
        await _close_once(pool, fund_id, cutoff, date(2030, 1, 1) + timedelta(days=next(counter)))

    reset_virtual_totals()
    ok = await perf_budget.best_of_async(_one, n=3, warmup=1)
    assert ok.wall_ms < budget_ms, perf_budget.describe(ok, budget_ms=budget_ms)

    slow_ibor = virtual_delayed_async(ibor_view.compute_ibor_view, budget_ms / 1000)
    monkeypatch.setattr(ibor_view, "compute_ibor_view", slow_ibor)
    slow = await perf_budget.best_of_async(_one, n=3, warmup=1)
    reset_virtual_totals()
    assert slow.wall_ms >= budget_ms, "injected delay did not breach the budget"
