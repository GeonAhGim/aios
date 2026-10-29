"""Reusable suite: subclass FixSessionContract and supply a fresh session fixture."""

import socket
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count
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
    """Measure per-call CPU with batches that amortize Windows clock ticks."""
    session = FakeFixSession()
    await session.logon()
    await session.send_order(order, cl_ord_id="warmup")
    identifiers = count()

    async def send_one() -> int:
        return await session.send_order(order, cl_ord_id=f"measured-{next(identifiers)}")

    # Even the small sample must amortize the 15.625ms CPU clock quantum.
    # best_of_async(n=5) picks the minimum cpu_ms across 5 runs to skip
    # CI CPU-contention spikes (task-19178 pattern).
    small = await perf_budget.best_of_async(send_one, n=5, batch=32)
    large = await perf_budget.best_of_async(send_one, n=5, batch=1000)

    assert large.cpu_ms < 0.1, (
        f"single-call CPU at batch=1000 is {large.cpu_ms:.4f}ms (budget 0.1ms)"
    )
    assert small.cpu_ms < 5.0, f"single-call CPU at batch=32 is {small.cpu_ms:.4f}ms (budget 5.0ms)"
    # best_of_async(n=5) runs 5 batches; result is the last call in the winning batch.
    # small: 5×32=160 calls after warmup(2) → result ∈ {33, 65, 97, 129, 161}
    assert small.result in (33, 65, 97, 129, 161), f"unexpected small.result={small.result}"
    # large: 5×1000=5000 calls after small(160) → result ∈ {1161, 2161, 3161, 4161, 5161}
    assert large.result in (1161, 2161, 3161, 4161, 5161), f"unexpected large.result={large.result}"
    # total calls = warmup(1) + original(1) + small(160) + large(5000)
    assert len(session.sent) == 5161, f"unexpected sent count={len(session.sent)}"


@pytest.mark.asyncio
@pytest.mark.parametrize("large_cpu_seconds", [0.015625, 0.0625, 0.1, 0.125, 1.0])
async def test_cpu_convergence_clock_quantization_and_budget(
    order: ChildOrder, monkeypatch: pytest.MonkeyPatch, large_cpu_seconds: float
) -> None:
    """A clock tick passes; actual per-call budget breaches still fail closed."""
    # best_of_async(n=5) calls sample_async 5× per run × 2 runs = 10 calls;
    # each sample_async calls process_time twice (start+end) → 20+ calls total.
    # process_time() is monotonic — use cumulative values so end ≥ start always.
    # Inject large_cpu_seconds as a delta on the 4th call (large measurement).
    # After that, every call returns the same cumulative tick so deltas are
    # large_cpu_seconds / batch, which exceeds 0.1ms when large_cpu_seconds ≥ 0.1.
    _acc = [0.0]

    def _process_time() -> float:
        if _acc[0] == 0.0:
            _acc[0] = 0.015625  # 1st call: 0 → 0.015625 (delta=0.015625)
        elif _acc[0] == 0.015625:
            _acc[0] = 0.015625 + 0.015625  # 2nd call: delta=0.015625
        elif _acc[0] == 0.015625 + 0.015625:
            _acc[0] += large_cpu_seconds  # 3rd call: inject large delta
        # 4th+ calls: keep adding large_cpu_seconds each time
        else:
            _acc[0] += large_cpu_seconds
        return _acc[0]

    monkeypatch.setattr("tests.support.perf_budget.time.process_time", _process_time)
    if large_cpu_seconds >= 0.1:
        with pytest.raises(AssertionError, match="budget 0.1ms"):
            await test_single_call_cpu_zero_convergence(order, PerfBudget())
    else:
        await test_single_call_cpu_zero_convergence(order, PerfBudget())


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
