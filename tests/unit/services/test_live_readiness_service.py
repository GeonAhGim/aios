"""LP-2 서비스: 소유자·모드 거부와 주문 행 -> 전환 근거 계산 연결."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.services.execution_types import ExecutionControlError
from src.services.live_readiness_service import LiveReadinessService

NOW = datetime(2026, 11, 7, tzinfo=UTC)


class _Conn:
    def __init__(self, execution, orders):
        self._execution = execution
        self._orders = orders
        self.fetch_args = None

    async def fetchrow(self, sql, *args):
        return self._execution

    async def fetch(self, sql, *args):
        self.fetch_args = args
        return self._orders


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


def _order(**kw):
    base = {
        "status": "FILLED",
        "side": "BUY",
        "quantity": Decimal("10"),
        "filled_quantity": Decimal("10"),
        "price": Decimal("100"),
        "average_fill_price": Decimal("100"),
        "created_at": NOW,
        "updated_at": NOW,
        "fee_total": Decimal("1.5"),
        "fee_currency": "USDT",
    }
    base.update(kw)
    return base


async def test_paper_execution_of_the_owner_returns_computed_readiness() -> None:
    user = uuid4()
    conn = _Conn(
        {"user_id": user, "mode": "PAPER"},
        [_order(), _order(status="REJECTED", filled_quantity=Decimal(0), fee_total=Decimal(0))],
    )
    result = await LiveReadinessService(_Pool(conn)).for_execution(7, user)
    assert conn.fetch_args == (7,)
    assert result.order_count == 2 and result.filled_count == 1 and result.rejected_count == 1
    assert result.fee_totals == {"USDT": Decimal("1.5")}


async def test_missing_execution_is_rejected() -> None:
    with pytest.raises(ExecutionControlError, match="존재하지 않는"):
        await LiveReadinessService(_Pool(_Conn(None, []))).for_execution(1, uuid4())


async def test_other_users_execution_is_rejected_without_reading_orders() -> None:
    conn = _Conn({"user_id": uuid4(), "mode": "PAPER"}, [_order()])
    with pytest.raises(ExecutionControlError, match="본인"):
        await LiveReadinessService(_Pool(conn)).for_execution(1, uuid4())
    assert conn.fetch_args is None  # 타인 실행의 주문은 읽지도 않는다


async def test_live_execution_is_rejected() -> None:
    user = uuid4()
    conn = _Conn({"user_id": user, "mode": "LIVE"}, [_order()])
    with pytest.raises(ExecutionControlError, match="PAPER"):
        await LiveReadinessService(_Pool(conn)).for_execution(1, user)
    assert conn.fetch_args is None


async def test_execution_without_orders_reports_unavailable_not_zero_performance() -> None:
    user = uuid4()
    result = await LiveReadinessService(
        _Pool(_Conn({"user_id": user, "mode": "PAPER"}, []))
    ).for_execution(1, user)
    assert result.order_count == 0 and result.fill_ratio is None
    assert "fill_ratio" in result.unavailable
