"""L22 D2 증빙 — PortfolioEngine(src/core/portfolio/engine.py).

engine.py는 FROZEN_PAPER_ONLY이고 이 task(decision)에 명시적 FROZEN 승인이
없어 소스는 수정하지 않는다. 이미 존재하던 위 5건(FD-8.2 회귀)은 negative
3건(가격 없음, 기보유 상태 진입, 무포지션 청산)을 갖췄지만 실패 주입·수치
성능 단언·게이트 적색 재현이 없어 D2 하한(ADR-2026-09-09-C Decision 1)에
미달이었다. 아래에 그 세 가지와 negative 2건(0가·음가)을 추가한다.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L22.
"""

import time
from datetime import datetime, timezone
from decimal import Decimal, DivisionByZero
from typing import Any

import pytest

from src.core.portfolio.engine import PortfolioEngine, PortfolioEngineError
from src.core.strategy.models import Signal
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide


def _signal(direction: OrderSide) -> Signal:
    to_state = (
        FSMState.BUY_ORDER_PENDING if direction == OrderSide.BUY else FSMState.SELL_ORDER_PENDING
    )
    return Signal(
        strategy_id="strat-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        direction=direction,
        confidence=1.0,
        target_position=Decimal("0"),
        stop_loss=None,
        take_profit=None,
        timestamp=datetime.now(timezone.utc),
        to_state=to_state,
    )


def test_entry_computes_quantity_from_allocated_capital_and_price():
    engine = PortfolioEngine()

    decision = engine.allocate(
        _signal(OrderSide.BUY),
        {
            "allocated_capital": Decimal("1000"),
            "position_quantity": Decimal("0"),
            "current_price": Decimal("50000"),
            "total_equity": Decimal("10000"),
        },
    )

    assert decision is not None
    assert decision.approved_quantity == Decimal("1000") / Decimal("50000")
    assert decision.capital_pct == Decimal("10")  # 1000/10000*100


def test_exit_liquidates_full_position():
    engine = PortfolioEngine()

    decision = engine.allocate(
        _signal(OrderSide.SELL),
        {
            "allocated_capital": Decimal("1000"),
            "position_quantity": Decimal("0.5"),
            "current_price": Decimal("50000"),
            "total_equity": Decimal("10000"),
        },
    )

    assert decision is not None
    assert decision.approved_quantity == Decimal("0.5")


def test_missing_current_price_skips_tick_returns_none():
    engine = PortfolioEngine()

    decision = engine.allocate(
        _signal(OrderSide.BUY),
        {
            "allocated_capital": Decimal("1000"),
            "position_quantity": Decimal("0"),
            "current_price": None,
            "total_equity": Decimal("10000"),
        },
    )

    assert decision is None


def test_entry_with_existing_position_raises_logic_error():
    engine = PortfolioEngine()

    with pytest.raises(PortfolioEngineError):
        engine.allocate(
            _signal(OrderSide.BUY),
            {
                "allocated_capital": Decimal("1000"),
                "position_quantity": Decimal("0.1"),
                "current_price": Decimal("50000"),
                "total_equity": Decimal("10000"),
            },
        )


def test_exit_without_position_raises_logic_error():
    engine = PortfolioEngine()

    with pytest.raises(PortfolioEngineError):
        engine.allocate(
            _signal(OrderSide.SELL),
            {
                "allocated_capital": Decimal("1000"),
                "position_quantity": Decimal("0"),
                "current_price": Decimal("50000"),
                "total_equity": Decimal("10000"),
            },
        )


# --- negative (추가) --------------------------------------------------------


def test_zero_current_price_skips_tick_returns_none():
    engine = PortfolioEngine()

    decision = engine.allocate(
        _signal(OrderSide.BUY),
        {
            "allocated_capital": Decimal("1000"),
            "position_quantity": Decimal("0"),
            "current_price": Decimal("0"),
            "total_equity": Decimal("10000"),
        },
    )

    assert decision is None


def test_negative_current_price_skips_tick_returns_none():
    engine = PortfolioEngine()

    decision = engine.allocate(
        _signal(OrderSide.BUY),
        {
            "allocated_capital": Decimal("1000"),
            "position_quantity": Decimal("0"),
            "current_price": Decimal("-1"),
            "total_equity": Decimal("10000"),
        },
    )

    assert decision is None


# --- 실패 주입 (1) --------------------------------------------------------


class _CorruptedPortfolioState(dict):
    """상류(FD-16.1 자본배분 조회) 하이드레이션이 부분적으로 깨진 상황을
    흉내낸다 — `current_price`는 정상 조회됐지만 `total_equity`는 그
    직후 조회가 실패한 손상된 상태(dict가 아니라 지연 조회 프록시라고
    가정). allocate()가 이 예외를 삼키고 기본값(0 등)으로 대체해 버리면
    엉뚱한 수량의 주문이 조용히 나갈 수 있다 — fail-closed 위반."""

    def __getitem__(self, key: str) -> Any:
        if key == "total_equity":
            raise RuntimeError("upstream total_equity hydration failed")
        return super().__getitem__(key)


def test_failure_injection_corrupted_total_equity_lookup_propagates_fail_closed():
    engine = PortfolioEngine()
    state = _CorruptedPortfolioState(
        allocated_capital=Decimal("1000"),
        position_quantity=Decimal("0"),
        current_price=Decimal("50000"),
        total_equity=Decimal("10000"),
    )

    with pytest.raises(RuntimeError, match="upstream total_equity hydration failed"):
        engine.allocate(_signal(OrderSide.BUY), state)


# --- 성능 단언 (1) --------------------------------------------------------


def test_performance_allocate_p99_latency_within_pretrade_gate_budget():
    """ADR-2026-09-09-C Decision 1 예산: 사전거래 게이트 p99 5ms.
    PortfolioEngine.allocate는 주문 제출 이전(수량 승인) 경로이므로 이
    예산이 적용된다."""
    engine = PortfolioEngine()

    n = 500
    latencies_ms: list[float] = []
    for _i in range(n):
        signal = _signal(OrderSide.BUY)
        start = time.perf_counter()
        engine.allocate(
            signal,
            {
                "allocated_capital": Decimal("1000"),
                "position_quantity": Decimal("0"),
                "current_price": Decimal("50000"),
                "total_equity": Decimal("10000"),
            },
        )
        latencies_ms.append((time.perf_counter() - start) * 1000)

    latencies_ms.sort()
    p99 = latencies_ms[int(n * 0.99) - 1]

    assert p99 < 5.0, f"p99={p99:.3f}ms — 사전거래 게이트 5ms 예산 초과"


# --- 게이트 적색 재현 (1) --------------------------------------------------


def test_gate_red_negative_total_equity_silently_yields_nonsensical_capital_pct():
    """engine.py는 `total_equity`의 부호를 검증하지 않는다. `total_equity==0`은
    우연히 `decimal.DivisionByZero`로 fail-closed 되지만(아래에서 대조 확인),
    **음수** `total_equity`(계좌가 이미 깨진 상태를 잘못 보고하는 상류 버그
    등)는 조용히 통과해 승인 수량(`approved_quantity`)은 정상처럼 양수로
    나가고 `capital_pct`만 물리적으로 말이 안 되는 음수가 된다 — 상위
    (FD-8.3 한도 재검증)가 `capital_pct` 부호만 보고 "한도 초과 아님"으로
    오판할 수 있는 실질적 리스크다.

    초록(있어야 할 그림, FD-8.2 DoD가 요구하는 fail-closed 검증)은
    `PortfolioEngineError` 등으로 거부하는 것이지만, engine.py는
    FROZEN_PAPER_ONLY이고 이 task decision에 명시적 FROZEN 승인이 없어
    가드 추가는 하지 않는다(N/A(FROZEN 미승인) — 가드 도입은 별도 FROZEN
    승인 이후 처리).
    """
    engine = PortfolioEngine()

    # 대조군: total_equity == 0은 우연한 fail-closed(연산 예외)를 얻는다.
    with pytest.raises(DivisionByZero):
        engine.allocate(
            _signal(OrderSide.BUY),
            {
                "allocated_capital": Decimal("1000"),
                "position_quantity": Decimal("0"),
                "current_price": Decimal("50000"),
                "total_equity": Decimal("0"),
            },
        )

    # 적색: total_equity < 0은 검증 없이 통과해 승인 결정을 그대로 반환한다.
    decision = engine.allocate(
        _signal(OrderSide.BUY),
        {
            "allocated_capital": Decimal("1000"),
            "position_quantity": Decimal("0"),
            "current_price": Decimal("50000"),
            "total_equity": Decimal("-10000"),
        },
    )

    assert decision is not None  # 적색: 거부돼야 할 입력이 승인 결정을 만든다
    assert decision.approved_quantity > 0  # 실제 주문으로 나갈 양수 수량
    assert decision.capital_pct < 0  # 물리적으로 불가능한 음수 자본비중
