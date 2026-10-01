"""LB-15 `compute_daily_nav` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-15.
DoD(task-714): "멱등, 체인 위반 거부". `pos_snapshot`/`pos_nav_daily`는
실제 Postgres(LB-9 어댑터, `nav_repo.insert`의 ON CONFLICT+source_hash
비교까지 검증)를 쓰고, 현금 잔고는 이 리프의 관심사가 아닌 미착수
어댑터(`CashSource`, 모듈독스트링 "미검증")라 in-memory fake로 대역한다.

task-10197: 675줄 LOC 규율(500줄 경고) 초과로 책임별 분할. 공용 fake/셋업
헬퍼는 `_compute_daily_nav_fixtures.py`로 옮겼고, 이 파일에는 핵심
생명주기(1일차/연속일/재실행 멱등/체인 위반/스테일 마크/현금 누락) negative
test만 남긴다. 분리된 파일:
- `test_compute_daily_nav_concurrent.py` — asyncio.gather 기반 실제 DB 경합
- `test_compute_daily_nav_failure_injection.py` — 커넥션 유실 + 게이트 적색 재현
- `test_compute_daily_nav_perf.py` — 순차 DB 왕복 수 상한(D3 수치 성능 단언)
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.compute_daily_nav import (
    NavCashUnavailableError,
    NavMarkUnavailableError,
    compute_daily_nav,
)
from src.foundation.positions.domain import nav
from tests.integration.foundation.positions._compute_daily_nav_fixtures import (
    BITGET,
    NOW,
    FakeCashSource,
    FakeFxRateSource,
    cmd,
    open_marked_position,
    setup_account,
)


async def test_first_day_has_zero_opening_and_persists(pool: asyncpg.Pool) -> None:
    tenant_id, account_id = await setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    result = await compute_daily_nav(
        cmd(tenant_id=tenant_id, account_id=account_id, at=NOW),
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    assert result.opening_nav == Decimal("0")
    assert result.closing_nav == Decimal("1000")

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, result.nav_date)
    assert stored is not None
    assert stored.source_hash == result.source_hash


async def test_second_day_chains_off_first_days_closing(pool: asyncpg.Pool) -> None:
    tenant_id, account_id = await setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)
    common = dict(
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    day1 = await compute_daily_nav(
        cmd(tenant_id=tenant_id, account_id=account_id, at=NOW), **common
    )
    assert day1.closing_nav == Decimal("1000")

    cash.seed(account_id, Decimal("1030"))
    day2 = await compute_daily_nav(
        cmd(
            tenant_id=tenant_id,
            account_id=account_id,
            at=NOW + timedelta(days=1),
            realized="30",
        ),
        **common,
    )

    assert day2.opening_nav == day1.closing_nav
    assert day2.closing_nav == Decimal("1030")


async def test_rerun_same_day_is_idempotent_no_duplicate_row(pool: asyncpg.Pool) -> None:
    tenant_id, account_id = await setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("500"))
    nav_repo = PostgresNavRepository(pool)
    command = cmd(tenant_id=tenant_id, account_id=account_id, at=NOW, realized="500")
    common = dict(
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    first = await compute_daily_nav(command, **common)
    second = await compute_daily_nav(command, **common)

    assert first.source_hash == second.source_hash
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM pos_nav_daily WHERE account_id = $1 AND nav_date = $2",
            account_id,
            first.nav_date,
        )
    assert count == 1


async def test_chain_break_when_rollforward_does_not_reconcile_is_rejected(
    pool: asyncpg.Pool,
) -> None:
    """DoD negative: 대차대조(cash+positions_mv=1000)와 롤포워드(realized=1
    뿐이라 0+1=1) 등식이 어긋나면 저장을 거부한다."""
    tenant_id, account_id = await setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    with pytest.raises(nav.NavChainBrokenError):
        await compute_daily_nav(
            cmd(tenant_id=tenant_id, account_id=account_id, at=NOW, realized="1"),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash,
            nav_repo=nav_repo,
            calendar=BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, BITGET.trading_day_of(NOW))
    assert stored is None, "체인 위반 시도가 행을 저장했습니다"


async def test_stale_mark_on_open_position_rejects_nav(pool: asyncpg.Pool) -> None:
    """DoD negative: 열린 포지션의 mark_price가 None(스테일)이면 전체 NAV
    산출을 거부한다 — 추정치 대입 금지."""
    tenant_id, account_id = await setup_account(pool)
    await open_marked_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"), mark_price=None
    )
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    with pytest.raises(NavMarkUnavailableError):
        await compute_daily_nav(
            cmd(tenant_id=tenant_id, account_id=account_id, at=NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash,
            nav_repo=nav_repo,
            calendar=BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, BITGET.trading_day_of(NOW))
    assert stored is None


async def test_marked_open_position_contributes_to_positions_mv(pool: asyncpg.Pool) -> None:

    tenant_id, account_id = await setup_account(pool)
    await open_marked_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        quantity=Decimal("2"),
        mark_price=Money(amount=Decimal("100"), currency=Currency.USDT),
    )
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("50"))
    nav_repo = PostgresNavRepository(pool)

    result = await compute_daily_nav(
        cmd(tenant_id=tenant_id, account_id=account_id, at=NOW, realized="250"),
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    assert result.positions_mv == Decimal("200")  # 2 * 100
    assert result.closing_nav == Decimal("250")  # cash(50) + mv(200)


async def test_missing_cash_source_value_rejects_nav(pool: asyncpg.Pool) -> None:
    tenant_id, account_id = await setup_account(pool)
    nav_repo = PostgresNavRepository(pool)

    with pytest.raises(NavCashUnavailableError):
        await compute_daily_nav(
            cmd(tenant_id=tenant_id, account_id=account_id, at=NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=FakeCashSource(),  # seed 안 함 -> None
            nav_repo=nav_repo,
            calendar=BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )
