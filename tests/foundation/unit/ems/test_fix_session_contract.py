"""Reusable suite: subclass FixSessionContract and supply a fresh session fixture."""

import socket
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter
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


@pytest.mark.asyncio
async def test_tcp_eof_before_acceptance_preserves_state(order: ChildOrder) -> None:
    """Real TCP EOF, test-only transport probe; production FIX remains 미검증."""
    class TcpProbeSession(FakeFixSession):
        async def send_order(self, order: ChildOrder, *, cl_ord_id: str) -> int:
            if client.recv(1) == b"":
                raise RuntimeError("Transport closed before acceptance")
            return await super().send_order(order, cl_ord_id=cl_ord_id)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.settimeout(2)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname(), timeout=2) as client:
            peer, _ = listener.accept()
            with peer:
                session = TcpProbeSession()
                await session.logon()
                peer.sendall(b"+")
                assert await session.send_order(order, cl_ord_id="accepted") == 1
                peer.shutdown(socket.SHUT_WR)
                with pytest.raises(RuntimeError, match="Transport closed"):
                    await session.send_order(order, cl_ord_id="rejected")
                assert session.seq_num == 1
                assert session.sent == {"accepted"}


@pytest.mark.asyncio
async def test_local_acceptance_performance(order: ChildOrder) -> None:
    """Local contract budget only, not network or venue throughput."""
    session = FakeFixSession()
    await session.logon()
    started = perf_counter()
    for number in range(10_000):
        assert await session.send_order(order, cl_ord_id=str(number)) == number + 1
    elapsed = perf_counter() - started
    assert elapsed < 1.0, f"10000 local accepts took {elapsed:.3f}s (budget 1s)"
    assert len(session.sent) == 10_000


@pytest.mark.asyncio
async def test_contract_gate_rejects_duplicate_bypass_mutant(order: ChildOrder) -> None:
    """The unchanged reusable contract turns red when duplicate defense is bypassed."""
    class DuplicateBypassSession(FakeFixSession):
        async def send_order(self, order: ChildOrder, *, cl_ord_id: str) -> int:
            self.sent.discard(cl_ord_id)
            return await super().send_order(order, cl_ord_id=cl_ord_id)

    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        await FixSessionContract().test_rejects_duplicate(DuplicateBypassSession(), order)
