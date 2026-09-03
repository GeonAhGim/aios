"""LB-15 `compute_daily_nav` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-15.
DoD(task-714): "멱등, 체인 위반 거부". `pos_snapshot`/`pos_nav_daily`는
실제 Postgres(LB-9 어댑터, `nav_repo.insert`의 ON CONFLICT+source_hash
비교까지 검증)를 쓰고, 현금 잔고는 이 리프의 관심사가 아닌 미착수
어댑터(`CashSource`, 모듈독스트링 "미검증")라 in-memory fake로 대역한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from src.data.models.base import Currency, Money
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.compute_daily_nav import (
    ComputeDailyNavCommand,
    NavCashUnavailableError,
    NavMarkUnavailableError,
    compute_daily_nav,
)
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain import nav
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_BITGET = VenueCalendar(venue="bitget", tz=ZoneInfo("UTC"), regular=KNOWN_SESSIONS["BITGET"])


class FakeCashSource:
    def __init__(self) -> None:
        self._balances: dict[UUID, Decimal | None] = {}

    def seed(self, account_id: UUID, balance: Decimal | None) -> None:
        self._balances[account_id] = balance

    async def cash(self, account_id: UUID, at: datetime) -> Decimal | None:
        return self._balances.get(account_id)


class FakeFxRateSource:
    """이 테스트 스위트는 통화 불일치 케이스를 쓰지 않으므로 호출되면
    그 자체가 결함 신호다."""

    async def rate(self, base: Currency, quote: Currency, at: datetime) -> None:  # pragma: no cover
        raise NotImplementedError(f"FX 경로가 필요 없는 테스트에서 호출됨: {base}->{quote}")


def _unique_symbol(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


def _position_key(venue_symbol: str) -> str:
    return str(
        PositionKey(
            venue="bitget", instrument_id=venue_symbol, strategy_id="default", execution_id="paper"
        )
    )


async def _open_marked_position(
    pool, *, tenant_id, account_id, quantity: Decimal, mark_price: Money | None
) -> PositionSnapshotView:
    position_key = _position_key(_unique_symbol("BTCUSDT"))
    snapshot = PositionSnapshotView(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("60000"), currency=Currency.USDT),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=mark_price,
        mark_at=_NOW if mark_price is not None else None,
        base_currency=Currency.USDT,
        last_journal_seq=0,
        updated_at=_NOW,
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


async def _setup_account(pool) -> tuple[UUID, UUID]:
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    return tenant_id, account_id


def _cmd(
    *,
    tenant_id,
    account_id,
    at,
    realized="1000",
    unrealized_delta="0",
    funding="0",
    fees="0",
    flows="0",
):
    return ComputeDailyNavCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        base_currency=Currency.USDT,
        at=at,
        realized=Decimal(realized),
        unrealized_delta=Decimal(unrealized_delta),
        funding=Decimal(funding),
        fees=Decimal(fees),
        flows=Decimal(flows),
        trace_id=uuid4(),
    )


async def test_first_day_has_zero_opening_and_persists(pool):
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    result = await compute_daily_nav(
        _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW),
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=_BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    assert result.opening_nav == Decimal("0")
    assert result.closing_nav == Decimal("1000")

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, result.nav_date)
    assert stored is not None
    assert stored.source_hash == result.source_hash


async def test_second_day_chains_off_first_days_closing(pool):
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)
    common = dict(
        snapshots=PostgresSnapshotRepository(pool), cash=cash, nav_repo=nav_repo,
        calendar=_BITGET, fx=FakeFxRateSource(), pool=pool,
    )

    day1 = await compute_daily_nav(
        _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW), **common
    )
    assert day1.closing_nav == Decimal("1000")

    cash.seed(account_id, Decimal("1030"))
    day2 = await compute_daily_nav(
        _cmd(
            tenant_id=tenant_id, account_id=account_id, at=_NOW + timedelta(days=1),
            realized="30",
        ),
        **common,
    )

    assert day2.opening_nav == day1.closing_nav
    assert day2.closing_nav == Decimal("1030")


async def test_rerun_same_day_is_idempotent_no_duplicate_row(pool):
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("500"))
    nav_repo = PostgresNavRepository(pool)
    cmd = _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW, realized="500")
    common = dict(
        snapshots=PostgresSnapshotRepository(pool), cash=cash, nav_repo=nav_repo,
        calendar=_BITGET, fx=FakeFxRateSource(), pool=pool,
    )

    first = await compute_daily_nav(cmd, **common)
    second = await compute_daily_nav(cmd, **common)

    assert first.source_hash == second.source_hash
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM pos_nav_daily WHERE account_id = $1 AND nav_date = $2",
            account_id, first.nav_date,
        )
    assert count == 1


async def test_chain_break_when_rollforward_does_not_reconcile_is_rejected(pool):
    """DoD negative: 대차대조(cash+positions_mv=1000)와 롤포워드(realized=1
    뿐이라 0+1=1) 등식이 어긋나면 저장을 거부한다."""
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    with pytest.raises(nav.NavChainBrokenError):
        await compute_daily_nav(
            _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW, realized="1"),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash,
            nav_repo=nav_repo,
            calendar=_BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, _BITGET.trading_day_of(_NOW))
    assert stored is None, "체인 위반 시도가 행을 저장했습니다"


async def test_stale_mark_on_open_position_rejects_nav(pool):
    """DoD negative: 열린 포지션의 mark_price가 None(스테일)이면 전체 NAV
    산출을 거부한다 — 추정치 대입 금지."""
    tenant_id, account_id = await _setup_account(pool)
    await _open_marked_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"), mark_price=None
    )
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    with pytest.raises(NavMarkUnavailableError):
        await compute_daily_nav(
            _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash,
            nav_repo=nav_repo,
            calendar=_BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, _BITGET.trading_day_of(_NOW))
    assert stored is None


async def test_marked_open_position_contributes_to_positions_mv(pool):
    tenant_id, account_id = await _setup_account(pool)
    await _open_marked_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("2"),
        mark_price=Money(amount=Decimal("100"), currency=Currency.USDT),
    )
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("50"))
    nav_repo = PostgresNavRepository(pool)

    result = await compute_daily_nav(
        _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW, realized="250"),
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=_BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    assert result.positions_mv == Decimal("200")  # 2 * 100
    assert result.closing_nav == Decimal("250")  # cash(50) + mv(200)


async def test_missing_cash_source_value_rejects_nav(pool):
    tenant_id, account_id = await _setup_account(pool)
    nav_repo = PostgresNavRepository(pool)

    with pytest.raises(NavCashUnavailableError):
        await compute_daily_nav(
            _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=FakeCashSource(),  # seed 안 함 -> None
            nav_repo=nav_repo,
            calendar=_BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )
