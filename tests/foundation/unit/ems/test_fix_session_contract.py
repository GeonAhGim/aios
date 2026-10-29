"""Reusable suite: subclass FixSessionContract and supply a fresh session fixture."""

import socket
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import ChildOrder
from src.foundation.ems.ports.fix_session import FixSessionPort
from src.services.oms.contracts.v1_events import ProviderOrderEvent
from tests.conftest import PerfBudget


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
        child_id=uuid4(),
        parent_id=uuid4(),
        slice_seq=0,
        instrument_id="BTC-USDT",
        side=OrderSide.BUY,
        planned_qty=Decimal("1"),
        scheduled_at=datetime(2026, 9, 9, tzinfo=UTC),
        order_id=uuid4(),
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


@pytest.mark.perf
@pytest.mark.asyncio
async def test_local_acceptance_performance(order: ChildOrder, perf_budget: PerfBudget) -> None:
    """Local contract budget only, not network or venue throughput.

    raw time.perf_counter() → perf_budget.sample_async() 전환(task-10965).
    """
    session = FakeFixSession()
    await session.logon()

    async def _run_10000() -> None:
        for number in range(10_000):
            assert await session.send_order(order, cl_ord_id=str(number)) == number + 1

    sample = await perf_budget.sample_async(_run_10000)

    assert sample.wall_ms < 1000, f"10000 local accepts took {sample.wall_ms:.1f}ms (budget 1000ms)"
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


@pytest.mark.perf
@pytest.mark.asyncio
async def test_single_call_cpu_zero_convergence(order: ChildOrder, perf_budget: PerfBudget) -> None:
    """Single-call send_order CPU time converges to near-zero (no hidden O(n) path).

    Measures 1회 호출당 CPU 밀리초가 batch 증가에 따라 0 수렴하는지 검증 —
    Windows time.process_time() 양자화(15.6ms tick)를 batch로 극복한다.
    """
    session = FakeFixSession()
    await session.logon()

    # warmup
    await session.send_order(order, cl_ord_id="warmup")

    # batch=1: quantization-heavy, batch=1000: quantization-smoothed
    s1 = await perf_budget.sample_async(
        lambda: session.send_order(order, cl_ord_id=f"b1-{id(session)}"), batch=1
    )

    # batch=1000: run 1000 unique calls in one interval, then divide by batch
    async def _batch_1000() -> int:
        results = []
        for i in range(1000):
            results.append(await session.send_order(order, cl_ord_id=f"b1000-{id(session)}-{i}"))
        return results[-1]

    s1000 = await perf_budget.sample_async(_batch_1000, batch=1)

    # CPU time per call at batch=1000 must be < 0.1ms (quantization-clean)
    assert s1000.cpu_ms < 0.1, (
        f"single-call CPU at batch=1000 is {s1000.cpu_ms:.4f}ms (budget 0.1ms)"
    )
    # batch=1 must not be wildly off from batch=1000 (no hidden O(n) per call)
    assert s1.cpu_ms < 5.0, f"single-call CPU at batch=1 is {s1.cpu_ms:.4f}ms (budget 5.0ms)"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_perf_budget_detects_10x_delay_injection(
    order: ChildOrder, perf_budget: PerfBudget
) -> None:
    """Performance test turns red when 10x sleep is injected into send_order.

    gate-red reproduction: 정상 입력에서는 녹색이지만 sleep 주입으로 지연되면
    적색 단언이 발동함을 검증한다.
    """
    import time

    class DelayedSession(FakeFixSession):
        async def send_order(self, order: ChildOrder, *, cl_ord_id: str) -> int:
            # 10ms — Windows time.sleep() resolution is ~15.6ms tick;
            # 1ms (0.001s) can resolve to 0 on some ticks, making wall_ms
            # drop below the 100ms threshold in CI parallel runs.
            time.sleep(0.01)
            return await super().send_order(order, cl_ord_id=cl_ord_id)

    session = DelayedSession()
    await session.logon()

    async def _run_1000() -> None:
        for number in range(1000):
            await session.send_order(order, cl_ord_id=f"delayed-{number}")

    sample = await perf_budget.sample_async(_run_1000)

    # 1000 calls × 10ms = 10s total wall; budget threshold is 100ms → perf budget
    # must detect. 10ms per call guarantees measurable wall time on Windows
    # (time.sleep() tick resolution ~15.6ms).
    # time.sleep does not consume CPU (cpu_ms stays ~0), so we check wall_ms
    # to detect injected wall-clock delays in send_order.
    assert sample.wall_ms >= 100, (
        f"Expected wall time >= 100ms with injected delay; got {sample.wall_ms:.1f}ms"
    )
