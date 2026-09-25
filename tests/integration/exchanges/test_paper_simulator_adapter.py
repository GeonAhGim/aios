"""`PaperSimulatorAdapter`/`PaperLedgerRepository` 실DB 통합테스트(L4-23).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-23
DoD — 잔고 차감·환원, 재전송 멱등(원장 1행), 재시작 후 잔고 보존,
is_paper_trading/is_sandboxed 상수, 잔고 부족 fail-closed(negative).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1605)는 원 커밋(0db9a9b)을
D1로 판정했다 — negative=4, failure-injection·adversarial guard-bypass 증거는
있으나(별도 파일 `test_paper_drop_injection.py`) 수치 성능 단언이 없었다(D2
하한 미달). 파일 끝의 `test_place_order_market_fill_latency_within_
normalized_budget`가 그 한 항목만 보강한다 — `place_order`의 시장가 체결
자기루프(오더북 조회+체결 시뮬레이션+원장 insert+정산 tx) p95 지연을 이
풀의 `SELECT 1` p95에 정규화한 임계로 단언한다(절대 ms 상수는 공유 CI
변동에 취약하다는 task-2765/2767/2768 동일 전례).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.paper.fee_model import FeeModel
from src.exchanges.paper.fill_model import FillModel
from src.exchanges.paper.latency_model import LatencyModel
from src.exchanges.paper.ledger_repository import InsufficientBalanceError, PaperLedgerRepository
from src.exchanges.paper.simulator_adapter import PaperSimulatorAdapter
from tests.support.paper_sim_fakes import FakeReferenceAdapter, SeqRandom, fixed_adv, instant_sleep

_NO_SLIPPAGE_FILL = FillModel(
    spread_bps=Decimal("0"),
    impact_bps_per_pct_adv=Decimal("0"),
    partial_fill_prob=0.0,
    partial_min_pct=Decimal("100"),
)
_TAKER_FEE = FeeModel(maker_bps=Decimal("0"), taker_bps=Decimal("10"), fee_currency=Currency.USDT)
_NO_DROP_LATENCY = LatencyModel(ack_ms_p50=0, ack_ms_p99=0, drop_response_prob=0.0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_order(
    client_order_id: str,
    *,
    side: OrderSide,
    order_type: OrderType,
    quantity: Decimal,
    price: Decimal | None = None,
) -> Order:
    return Order(
        client_order_id=client_order_id,
        strategy_id="strat",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="paper_sim",
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=Money(amount=price, currency=Currency.USDT) if price is not None else None,
        asset_class=AssetClass.CRYPTO,
    )


def _make_adapter(
    pool,
    account_id: UUID,
    *,
    rng_values: list[float] | None = None,
    latency: LatencyModel = _NO_DROP_LATENCY,
) -> PaperSimulatorAdapter:
    reference = FakeReferenceAdapter(bid=Decimal("29990"), ask=Decimal("30000"))
    return PaperSimulatorAdapter(
        reference,
        PaperLedgerRepository(),
        _NO_SLIPPAGE_FILL,
        _TAKER_FEE,
        latency,
        clock=_now,
        rng=SeqRandom(rng_values or [0.0]),
        account_id=account_id,
        pool=pool,
        adv_provider=fixed_adv,
        sleeper=instant_sleep,
    )


async def _seed_usdt(pool, account_id: UUID, amount: Decimal) -> None:
    ledger = PaperLedgerRepository()
    async with pool.acquire() as conn:
        await ledger.deposit(conn, account_id, "USDT", amount)


def test_is_paper_trading_and_is_sandboxed_are_hardcoded_and_immutable(pool) -> None:
    adapter = _make_adapter(pool, uuid4())
    assert adapter.is_paper_trading is True
    assert adapter.is_sandboxed is True
    with pytest.raises(AttributeError):
        adapter.is_paper_trading = False  # type: ignore[misc]


async def test_market_buy_fills_debits_quote_credits_base_pays_fee(pool) -> None:
    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    await _seed_usdt(pool, account_id, Decimal("100000"))

    order = _make_order(
        "buy-1", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("1")
    )
    result = await adapter.place_order(order)

    assert result.status is OrderStatus.FILLED
    assert result.filled_quantity == Decimal("1")
    assert result.average_fill_price is not None
    assert result.average_fill_price.amount == Decimal("30000")

    balances = {b.asset: b for b in await adapter.get_balance()}
    assert balances["BTC"].total == Decimal("1")
    # notional 30000 + fee(10bps taker) 30 = 30030 차감
    assert balances["USDT"].available == Decimal("69970")
    assert balances["USDT"].total == Decimal("69970")


async def test_limit_order_not_crossing_stays_open_no_balance_move(pool) -> None:
    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    await _seed_usdt(pool, account_id, Decimal("100000"))

    order = _make_order(
        "limit-1",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
    )
    result = await adapter.place_order(order)

    assert result.status is OrderStatus.ACKNOWLEDGED
    assert result.filled_quantity == Decimal("0")
    balance = await adapter.get_balance("USDT")
    assert balance[0].available == Decimal("100000")


async def test_resubmission_same_client_order_id_is_idempotent(pool) -> None:
    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    await _seed_usdt(pool, account_id, Decimal("100000"))

    order = _make_order(
        "dup-1", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("1")
    )
    first = await adapter.place_order(order)
    second = await adapter.place_order(order)

    assert first.exchange_order_id == second.exchange_order_id
    assert second.filled_quantity == Decimal("1")  # 재체결 아님, 같은 값 그대로
    balance = await adapter.get_balance("USDT")
    assert balance[0].available == Decimal("69970")  # 두 번 안 깎임


async def test_restart_new_adapter_instance_sees_same_persisted_state(pool) -> None:
    """DoD — 재시작 후 잔고 보존: 새 어댑터 인스턴스(= 새 프로세스 가정)가
    같은 DB를 보고도 동일한 진실을 봐야 한다(인메모리 캐시 없음 증명)."""
    account_id = uuid4()
    adapter_a = _make_adapter(pool, account_id)
    await _seed_usdt(pool, account_id, Decimal("100000"))
    order = _make_order(
        "restart-1", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("1")
    )
    await adapter_a.place_order(order)

    adapter_b = _make_adapter(pool, account_id)  # 새 인스턴스 — 아무 상태도 공유 안 함
    found = await adapter_b.find_order_by_client_id("restart-1")
    assert found is not None
    assert found.status is OrderStatus.FILLED
    balances = {b.asset: b for b in await adapter_b.get_balance()}
    assert balances["BTC"].total == Decimal("1")


async def test_insufficient_balance_raises_and_persists_nothing(pool) -> None:
    """negative — 잔고 부족은 fail-closed, 트랜잭션 전체 롤백(주문행도 안 남는다)."""
    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    await _seed_usdt(pool, account_id, Decimal("1"))

    order = _make_order(
        "insuff-1", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("1")
    )
    with pytest.raises(InsufficientBalanceError):
        await adapter.place_order(order)

    assert await adapter.find_order_by_client_id("insuff-1") is None


async def test_cancel_open_order_succeeds_filled_order_cannot_cancel(pool) -> None:
    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    await _seed_usdt(pool, account_id, Decimal("100000"))

    open_order = _make_order(
        "cancel-open",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
    )
    placed_open = await adapter.place_order(open_order)
    assert await adapter.cancel_order(placed_open.exchange_order_id) is True

    filled_order = _make_order(
        "cancel-filled", side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("1")
    )
    placed_filled = await adapter.place_order(filled_order)
    assert await adapter.cancel_order(placed_filled.exchange_order_id) is False


async def _p95_ms(reps: int, step) -> float:
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        await step()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return samples[int(len(samples) * 0.95) - 1]


@pytest.mark.perf
async def test_place_order_market_fill_latency_within_normalized_budget(pool) -> None:
    """수치 성능 단언(D2 보강, DEPTH_L4_BR #1605) — 시장가 체결
    자기루프(오더북 조회 → 체결 시뮬레이션 → 원장 insert → 정산 tx)의 p95
    지연이 같은 풀의 기준 왕복비용(`SELECT 1`) 대비 정규화한 임계를 넘지
    않는다. 매 반복마다 새 `client_order_id`를 써서 멱등 재조회 경로(빠른
    조기 반환)가 아니라 실제 체결·정산 경로를 측정한다."""
    reps = 15
    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(reps, lambda: conn.fetchval("SELECT 1"))

    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    await _seed_usdt(pool, account_id, Decimal("100000000"))

    counter = {"n": 0}

    async def _one_place() -> None:
        counter["n"] += 1
        order = _make_order(
            f"perf-{counter['n']}",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
        )
        await adapter.place_order(order)

    fill_p95 = await _p95_ms(reps, _one_place)

    budget_ms = max(400.0, 40.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nplace_order market-fill self-loop p95={fill_p95:.3f}ms "
        f"baseline(SELECT 1) p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert fill_p95 < budget_ms
