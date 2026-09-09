"""Reusable suite: subclass FixSessionContract and supply a fresh session fixture."""

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import ChildOrder
from src.foundation.ems.ports.fix_session import FixSessionPort
from src.services.oms.contracts.v1_events import ProviderOrderEvent


class FakeFixSession:
    def __init__(self) -> None:
        self.logged_on = False
        self.seq_num = 0
        self.sent: set[str] = set()
        self.callback: Callable[[ProviderOrderEvent], None] | None = None

    async def logon(self) -> None:
        self.logged_on = True

    async def logout(self) -> None:
        self.logged_on = False

    async def send_order(self, order: ChildOrder, *, cl_ord_id: str) -> int:
        if not self.logged_on:
            raise RuntimeError("Session is logged out")
        if cl_ord_id in self.sent:
            raise RuntimeError("Duplicate cl_ord_id")
        self.sent.add(cl_ord_id)
        self.seq_num += 1
        return self.seq_num

    async def reset_sequence(self) -> None:
        self.seq_num = 0

    def register_execution_report_callback(
        self, callback: Callable[[ProviderOrderEvent], None]
    ) -> None:
        self.callback = callback


@pytest.fixture
def order() -> ChildOrder:
    return ChildOrder(
        child_id=uuid4(), parent_id=uuid4(), slice_seq=0,
        instrument_id="BTC-USDT", side=OrderSide.BUY, planned_qty=Decimal("1"),
        scheduled_at=datetime(2026, 9, 9, tzinfo=timezone.utc), order_id=uuid4(),
    )


class FixSessionContract:
    @pytest.mark.asyncio
    async def test_requires_logon(self, session: FixSessionPort, order: ChildOrder) -> None:
        with pytest.raises(RuntimeError):
            await session.send_order(order, cl_ord_id="first")
        await session.logon()
        assert await session.send_order(order, cl_ord_id="first") == 1
        await session.logout()
        with pytest.raises(RuntimeError):
            await session.send_order(order, cl_ord_id="second")
        await session.logon()
        assert await session.send_order(order, cl_ord_id="second") == 2

    @pytest.mark.asyncio
    async def test_rejects_duplicate(self, session: FixSessionPort, order: ChildOrder) -> None:
        await session.logon()
        assert await session.send_order(order, cl_ord_id="same") == 1
        with pytest.raises(RuntimeError):
            await session.send_order(order, cl_ord_id="same")
        assert await session.send_order(order, cl_ord_id="next") == 2
        await session.reset_sequence()
        with pytest.raises(RuntimeError):
            await session.send_order(order, cl_ord_id="same")

    @pytest.mark.asyncio
    async def test_sequence_and_reset(self, session: FixSessionPort, order: ChildOrder) -> None:
        await session.logon()
        for seq_num in range(1, 4):
            assert await session.send_order(order, cl_ord_id=str(seq_num)) == seq_num
        await session.reset_sequence()
        assert await session.send_order(order, cl_ord_id="after-reset") == 1
        assert await session.send_order(order, cl_ord_id="after-reset-2") == 2

    def test_complete_protocol(self, session: FixSessionPort) -> None:
        assert isinstance(session, FixSessionPort)
        session.register_execution_report_callback(lambda report: None)


class TestFakeFixSession(FixSessionContract):
    @pytest.fixture
    def session(self) -> FixSessionPort:
        return FakeFixSession()


def test_partial_implementation_is_rejected() -> None:
    class MissingSendOrder:
        async def logon(self) -> None: ...

        async def logout(self) -> None: ...

        async def reset_sequence(self) -> None: ...

        def register_execution_report_callback(
            self, callback: Callable[[ProviderOrderEvent], None]
        ) -> None: ...

    assert isinstance(MissingSendOrder(), FixSessionPort) is False
