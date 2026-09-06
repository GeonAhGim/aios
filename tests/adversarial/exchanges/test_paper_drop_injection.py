"""DROP(응답 유실) 주입 → UNKNOWN 종단 적대적 테스트(L4-23, §6 F17).

task-1605 note DoD(3) — 예외를 삼키지 않고 `SentUnknownError`로 종단하되,
venue측 원장(`paper_sim_orders`)은 실제 처리 결과를 그대로 보존해
`find_order_by_client_id`/`get_order`로 재조회 가능해야 한다
(L4-16 `unknown_resolver`가 나중에 쓸 경로). DoD(2) — LIVE 요청은 이
어댑터가 아니라 방어선(`require_paper_sandbox`)이 실제로 차단하는지도
여기서 증명한다(defense-in-depth, is_paper_trading/is_sandboxed는 상수라
정상 경로로는 절대 LIVE가 될 수 없으므로 서브클래스로 값을 뒤집어 확인).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.data.models.base import AssetClass, Currency
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.paper.fee_model import FeeModel
from src.exchanges.paper.fill_model import FillModel
from src.exchanges.paper.latency_model import LatencyModel
from src.exchanges.paper.ledger_repository import PaperLedgerRepository
from src.exchanges.paper.simulator_adapter import PaperSimulatorAdapter, SentUnknownError
from tests.support.paper_sim_fakes import FakeReferenceAdapter, SeqRandom, fixed_adv, instant_sleep

_NO_SLIPPAGE_FILL = FillModel(
    spread_bps=Decimal("0"),
    impact_bps_per_pct_adv=Decimal("0"),
    partial_fill_prob=0.0,
    partial_min_pct=Decimal("100"),
)
_TAKER_FEE = FeeModel(maker_bps=Decimal("0"), taker_bps=Decimal("10"), fee_currency=Currency.USDT)
_ALWAYS_DROP_LATENCY = LatencyModel(ack_ms_p50=0, ack_ms_p99=0, drop_response_prob=1.0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_order(client_order_id: str) -> Order:
    return Order(
        client_order_id=client_order_id,
        strategy_id="strat",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="paper_sim",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.CRYPTO,
    )


def _make_adapter(pool, account_id) -> PaperSimulatorAdapter:
    reference = FakeReferenceAdapter(bid=Decimal("29990"), ask=Decimal("30000"))
    return PaperSimulatorAdapter(
        reference,
        PaperLedgerRepository(),
        _NO_SLIPPAGE_FILL,
        _TAKER_FEE,
        _ALWAYS_DROP_LATENCY,
        clock=_now,
        rng=SeqRandom([0.0, 0.0]),  # u=0(지연 0), drop 판정 0.0 < 1.0 → 항상 DROP
        account_id=account_id,
        pool=pool,
        adv_provider=fixed_adv,
        sleeper=instant_sleep,
    )


async def test_drop_injection_raises_but_ledger_holds_real_terminal_state(pool) -> None:
    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    async with pool.acquire() as conn:
        await PaperLedgerRepository().deposit(conn, account_id, "USDT", Decimal("100000"))

    order = _make_order("drop-1")
    with pytest.raises(SentUnknownError) as exc_info:
        await adapter.place_order(order)
    assert exc_info.value.client_order_id == "drop-1"

    # 응답은 못 받았지만 venue 진실은 이미 커밋돼 있다 — unknown_resolver 몫.
    resolved = await adapter.find_order_by_client_id("drop-1")
    assert resolved is not None
    assert resolved.status is OrderStatus.FILLED
    assert resolved.filled_quantity == Decimal("1")

    balances = {b.asset: b for b in await adapter.get_balance()}
    assert balances["BTC"].total == Decimal("1")
    assert balances["USDT"].available == Decimal("69970")


async def test_drop_then_resend_same_client_order_id_does_not_double_fill(pool) -> None:
    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    async with pool.acquire() as conn:
        await PaperLedgerRepository().deposit(conn, account_id, "USDT", Decimal("100000"))

    order = _make_order("drop-2")
    with pytest.raises(SentUnknownError):
        await adapter.place_order(order)

    # OMS가 UNKNOWN을 해소하기 전에 순진하게 재전송해도(§6 F17) 원장은 1행 그대로.
    resend_result = await adapter.place_order(order)
    assert resend_result.status is OrderStatus.FILLED
    assert resend_result.filled_quantity == Decimal("1")

    balances = {b.asset: b for b in await adapter.get_balance()}
    assert balances["USDT"].available == Decimal("69970")  # 두 번 안 깎임


class _LiveConfiguredPaperAdapter(PaperSimulatorAdapter):
    """방어 심화 검증 전용 — 정상 경로로는 절대 만들어질 수 없는 상태
    (`is_paper_trading=False`)를 강제로 재현해 `require_paper_sandbox`가
    실제로 place_order를 막는지 확인한다."""

    @property
    def is_paper_trading(self) -> bool:
        return False


async def test_live_configured_subclass_is_blocked_by_defense_in_depth_guard(pool) -> None:
    account_id = uuid4()
    reference = FakeReferenceAdapter(bid=Decimal("29990"), ask=Decimal("30000"))
    adapter = _LiveConfiguredPaperAdapter(
        reference,
        PaperLedgerRepository(),
        _NO_SLIPPAGE_FILL,
        _TAKER_FEE,
        _ALWAYS_DROP_LATENCY,
        clock=_now,
        rng=SeqRandom([0.0, 0.0]),
        account_id=account_id,
        pool=pool,
        adv_provider=fixed_adv,
        sleeper=instant_sleep,
    )
    async with pool.acquire() as conn:
        await PaperLedgerRepository().deposit(conn, account_id, "USDT", Decimal("100000"))

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.place_order(_make_order("blocked-1"))

    # 거부됐으므로 주문행 자체가 없어야 한다.
    assert await adapter.find_order_by_client_id("blocked-1") is None
