"""PostgresNavRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-9.
DoD(task-375): "pos_nav_daily 체인/UNIQUE 위반 거부"(negative) — 같은
(account_id, nav_date)에 다른 source_hash면 POS_NAV_CHAIN_BROKEN, DB
CHECK(closing_nav = cash + positions_mv) 위반은 asyncpg 예외로 거부.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.positions.adapters.postgres_nav_repository import (
    NavChainBrokenError,
    PostgresNavRepository,
)
from src.foundation.positions.contracts.v1 import NAVSnapshot
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account

_DAY_COUNTER = date(2026, 1, 1)


@pytest.fixture
def repo(pool):
    return PostgresNavRepository(pool)


def _nav(*, account_id, nav_date, closing_nav="1000", source_hash: str | None = None):
    from decimal import Decimal

    closing = Decimal(closing_nav)
    return NAVSnapshot(
        account_id=account_id,
        nav_date=nav_date,
        base_currency=Currency.KRW,
        opening_nav=Decimal("900"),
        cash=closing,
        positions_mv=Decimal("0"),
        realized=Decimal("50"),
        unrealized_delta=Decimal("0"),
        funding=Decimal("0"),
        fees=Decimal("0"),
        flows=Decimal("50"),
        closing_nav=closing,
        fx_rates=[],
        source_hash=source_hash or hashlib.sha256(str(closing).encode()).hexdigest(),
    )


async def _setup(pool):
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    return account_id


async def test_get_returns_none_when_absent(pool, repo):
    account_id = await _setup(pool)
    async with pool.acquire() as conn, conn.transaction():
        assert await repo.get(conn, account_id, _DAY_COUNTER) is None


async def test_insert_persists_and_get_returns_it(pool, repo):
    account_id = await _setup(pool)
    nav = _nav(account_id=account_id, nav_date=_DAY_COUNTER)

    async with pool.acquire() as conn, conn.transaction():
        inserted = await repo.insert(conn, nav)

    assert inserted.closing_nav == nav.closing_nav
    assert inserted.source_hash == nav.source_hash

    async with pool.acquire() as conn, conn.transaction():
        fetched = await repo.get(conn, account_id, _DAY_COUNTER)
    assert fetched is not None
    assert fetched.source_hash == nav.source_hash


async def test_insert_same_day_same_source_hash_is_idempotent(pool, repo):
    account_id = await _setup(pool)
    day = _DAY_COUNTER + timedelta(days=1)
    nav = _nav(account_id=account_id, nav_date=day)

    async with pool.acquire() as conn, conn.transaction():
        first = await repo.insert(conn, nav)
    async with pool.acquire() as conn, conn.transaction():
        second = await repo.insert(conn, nav)

    assert second.source_hash == first.source_hash

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM pos_nav_daily WHERE account_id = $1 AND nav_date = $2",
            account_id,
            day,
        )
    assert count == 1


async def test_insert_same_day_different_source_hash_raises_chain_broken(pool, repo):
    """DoD negative: pos_nav_daily 체인 위반 거부 — 같은 날 다른 재계산
    결과는 덮어쓰지 않고 거부한다."""
    account_id = await _setup(pool)
    day = _DAY_COUNTER + timedelta(days=2)

    async with pool.acquire() as conn, conn.transaction():
        await repo.insert(conn, _nav(account_id=account_id, nav_date=day, source_hash="a" * 64))

    with pytest.raises(NavChainBrokenError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.insert(conn, _nav(account_id=account_id, nav_date=day, source_hash="b" * 64))

    async with pool.acquire() as conn, conn.transaction():
        current = await repo.get(conn, account_id, day)
    assert current is not None
    assert current.source_hash == "a" * 64, "체인 위반 시도가 기존 행을 바꿨습니다"


async def test_insert_preserves_full_decimal_precision_and_fx_rates_round_trip(pool, repo):
    """수치 round-trip 단언(DEEPEN task-2945): 금액 컬럼은 NUMERIC(30,10),
    `fx_rates`는 JSONB(Decimal이 문자열로 직렬화됨)다 — 정수부 다자릿수 +
    소수부 10자리(음수 포함) 값이 INSERT...RETURNING 경로(`insert`가
    반환하는 뷰)와 별도 SELECT 경로(`get`) 양쪽에서 원본 Decimal과 정확히
    일치해야 한다(perf 단언 아님 — p95는 이 리프 소관이 아니다)."""
    from datetime import datetime, timezone
    from decimal import Decimal

    from src.data.models.base import FXRate

    account_id = await _setup(pool)
    day = _DAY_COUNTER + timedelta(days=4)

    cash = Decimal("123456789012345.1234567891")
    positions_mv = Decimal("987654321098765.9876543219")
    closing_nav = cash + positions_mv
    fx_rate = FXRate(
        base=Currency.USDT,
        quote=Currency.KRW,
        rate=Decimal("1234.0987654321"),
        timestamp=datetime.now(timezone.utc),
        source="test",
    )
    nav = NAVSnapshot(
        account_id=account_id,
        nav_date=day,
        base_currency=Currency.KRW,
        opening_nav=Decimal("-999999999999999.9999999999"),
        cash=cash,
        positions_mv=positions_mv,
        realized=Decimal("0.0000000001"),
        unrealized_delta=Decimal("-0.0000000001"),
        funding=Decimal("0"),
        fees=Decimal("0"),
        flows=Decimal("0"),
        closing_nav=closing_nav,
        fx_rates=[fx_rate],
        source_hash=hashlib.sha256(b"precision-round-trip").hexdigest(),
    )

    async with pool.acquire() as conn, conn.transaction():
        inserted = await repo.insert(conn, nav)

    assert inserted.cash == cash
    assert inserted.positions_mv == positions_mv
    assert inserted.closing_nav == closing_nav
    assert inserted.opening_nav == nav.opening_nav
    assert inserted.realized == nav.realized
    assert inserted.unrealized_delta == nav.unrealized_delta
    assert len(inserted.fx_rates) == 1
    assert inserted.fx_rates[0].rate == fx_rate.rate
    assert inserted.fx_rates[0].base == fx_rate.base
    assert inserted.fx_rates[0].quote == fx_rate.quote

    async with pool.acquire() as conn, conn.transaction():
        fetched = await repo.get(conn, account_id, day)
    assert fetched is not None
    assert fetched.cash == cash
    assert fetched.closing_nav == closing_nav
    assert len(fetched.fx_rates) == 1
    assert fetched.fx_rates[0].rate == fx_rate.rate


async def test_insert_violating_closing_nav_check_is_rejected(pool, repo):
    """DoD negative: UNIQUE뿐 아니라 CHECK(closing_nav = cash + positions_mv)
    위반도 거부돼야 한다."""
    account_id = await _setup(pool)
    day = _DAY_COUNTER + timedelta(days=3)
    from decimal import Decimal

    broken = _nav(account_id=account_id, nav_date=day).model_copy(
        update={"closing_nav": Decimal("999999")}
    )

    with pytest.raises(asyncpg.PostgresError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.insert(conn, broken)
