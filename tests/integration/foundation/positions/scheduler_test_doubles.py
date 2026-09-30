"""`PositionsScheduler` 통합테스트 공용 대역(test doubles)·헬퍼.

`test_positions_scheduler.py`(기본 마크 사이클), `test_positions_scheduler_isolation.py`
(계좌별 예외 격리 negative/동시성/게이트 적색 재현), `test_positions_scheduler_perf.py`
(수치 성능 예산)가 공유하는 대역·픽스처 헬퍼만 둔다 — 각 파일은 자신의 시나리오만 담당한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.data.models.base import Currency, Money
from src.data.models.trading import AccountBalance
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import CostMethod, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.reconciliation.contracts.v1 import (
    Classification,
    EntitySnapshot,
    ReconciliationRunView,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
BITGET = VenueCalendar(venue="bitget", tz=ZoneInfo("UTC"), regular=KNOWN_SESSIONS["BITGET"])


def clock() -> datetime:
    return NOW


class FakeMarkPriceSource:
    def __init__(self, price: Money | None = None) -> None:
        self._price = price

    async def mark(self, position_key: str, at: datetime) -> Money | None:
        return self._price


class FailingVenueMarkSource:
    """`venue == "FAIL"`인 포지션만 예외를 던지는 대역 — 계좌 하나의
    실패를 재현하려고 그 계좌의 포지션만 이 venue로 연다."""

    async def mark(self, position_key: str, at: datetime) -> Money | None:
        if PositionKey.parse(position_key).venue == "FAIL":
            raise ConnectionError("boom")
        return Money(amount=Decimal("70000"), currency=Currency.USDT)


class BlockingMarkPriceSource:
    """동시성 테스트 전용 — `mark()` 호출 시점에 `reached`를 신호하고
    `resume`이 풀릴 때까지 대기한다. 스케줄러가 스냅샷을 읽은 직후·아직
    쓰기 전인 그 창(window)에 맞춰 동시 쓰기를 주입하려는 목적이다
    (`asyncio.Event` 기반 결정론, task-409 선례)."""

    def __init__(self, price: Money, reached: asyncio.Event, resume: asyncio.Event) -> None:
        self._price = price
        self._reached = reached
        self._resume = resume

    async def mark(self, position_key: str, at: datetime) -> Money | None:
        self._reached.set()
        await self._resume.wait()
        return self._price


class FakeFxRateSource:
    async def rate(self, base: Currency, quote: Currency, at: datetime) -> None:  # pragma: no cover
        raise NotImplementedError("이 스위트는 통화 불일치를 쓰지 않는다")


class FailingVenueBalanceSource:
    """특정 `connection_id`만 잔고 조회에서 예외를 던지는 대역 — 계좌
    하나의 대사(reconcile) 실패를 재현한다."""

    def __init__(self, fail_connection_id) -> None:
        self._fail_connection_id = fail_connection_id

    async def balances(self, connection_id) -> list[AccountBalance]:
        if connection_id == self._fail_connection_id:
            raise ConnectionError("boom")
        return [
            AccountBalance(
                exchange="bitget", asset="BTC", total=Decimal("1"), available=Decimal("1")
            )
        ]


async def fake_recon(
    *, tenant_id, target_type: str, target_ref, connection_id, entities: list[EntitySnapshot]
) -> ReconciliationRunView:
    """FND-08 `run_reconciliation`을 재구현하지 않는다 — 스케줄러의
    계좌별 격리만 겨냥하므로 분류 로직은 이 리프의 관심사가 아니다
    (`test_reconcile_provider.py`가 실제 FND-08로 이미 검증)."""
    return ReconciliationRunView(
        id=uuid4(),
        target_type=target_type,
        target_ref=target_ref,
        items=[],
        aggregate_classification=Classification.HEALTHY,
        created_at=NOW,
    )


def unique_symbol(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


async def open_position(
    pool, *, tenant_id, account_id, quantity: Decimal, venue: str = "bitget"
) -> PositionSnapshotView:
    position_key = str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue=venue,
            instrument_id=unique_symbol("BTCUSDT"),
            strategy_id="default",
            execution_id="paper",
        )
    )
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
        mark_price=None,
        mark_at=None,
        base_currency=Currency.USDT,
        # task-3863: a real "open" position (quantity != 0) always got there via
        # record_fill folding at least one journal entry, so last_journal_seq=1
        # here (not the 0 sentinel that means "no row for this key yet") -- an
        # already-open position's mark-price replace must stay a normal CAS, not
        # collide with the adapter's first-creation-only guard at expected_seq=0.
        last_journal_seq=1,
        updated_at=NOW,
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


async def setup_account(pool) -> tuple:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    return tenant_id, account_id
