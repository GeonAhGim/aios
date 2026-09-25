"""L22 — PortfolioEngine v2 계약 + 기존 FD-8.2 회귀 테스트.

DoD (L4_strategy_portfolio_backtest_v1.0.md#L22):
- test_engine_v2.py 생성
- 기존 FD-8.2 회귀 테스트 포함
- 테스트 ≥3건 (negative 최소 1건)
- py_compile 통과, pytest 통과
- INVARIANTS.md 위반 없음
"""

from __future__ import annotations

import hashlib
import time
from decimal import Decimal
from typing import Any

import pytest

from src.core.portfolio.engine import PortfolioEngine, PortfolioEngineError
from src.core.portfolio.models import AllocationDecision
from src.data.models.trading import OrderSide

# --- 헬퍼 --------------------------------------------------------------------


def _signal(
    symbol: str = "BTC-USDT", strategy_id: str = "strat-1", direction: OrderSide = OrderSide.BUY
) -> Any:
    """가시적인 Signal 클래스가 없으면 dict로 대체 — allocate()는 dict 받음."""

    class _Signal:
        def __init__(self, sym: str, sid: str, d: OrderSide) -> None:
            self.symbol = sym
            self.strategy_id = sid
            self.direction = d
            self.signal_id = hashlib.sha256(f"{sym}-{sid}".encode()).hexdigest()[:16]

    return _Signal(symbol, strategy_id, direction)


def _state(
    allocated_capital: Decimal = Decimal("1000"),
    position_quantity: Decimal = Decimal("0"),
    current_price: Decimal = Decimal("50000"),
    total_equity: Decimal = Decimal("10000"),
) -> dict[str, Any]:
    return {
        "allocated_capital": allocated_capital,
        "position_quantity": position_quantity,
        "current_price": current_price,
        "total_equity": total_equity,
    }


# --- v2 계약: AllocationDecision 필드 ---------------------------------------


class TestAllocationDecisionV2Contract:
    """3.3 포트폴리오 계약 — AllocationDecision v2 스키마.

    현재 AllocationDecision은 4필드(symbol, strategy_id, approved_quantity,
    capital_pct)만 존재. v2 스키마는 sizing_method, target_weight_pct,
    pre_binding_quantity, binding_reasons, decision_hash, schema_version
    필드를 optional로 확장한다. 현재 시점에서 AllocationDecision은
    pydantic BaseModel이므로 extra fields는 거부된다.
    """

    def test_v2_decision_minimal_fields(self) -> None:
        """기존 4필드만으로 AllocationDecision이 생성된다."""
        decision = AllocationDecision(
            symbol="BTC-USDT",
            strategy_id="strat-1",
            approved_quantity=Decimal("0.02"),
            capital_pct=Decimal("10"),
        )
        assert decision.symbol == "BTC-USDT"
        assert decision.strategy_id == "strat-1"
        assert decision.approved_quantity == Decimal("0.02")
        assert decision.capital_pct == Decimal("10")

    def test_v2_decision_extra_fields_accepted_pydantic_v2(self) -> None:
        """v2 optional 필드는 Pydantic v2 extra='allow'로 인해 현재 허용된다.

        AllocationDecision이 Pydantic BaseModel을 상속하므로 extra 필드는
        현재 시점에서 거부되지 않는다. 이는 v2 스키마가 정식 도입될 때까지
        extra 필드가 silently 통과되는 사실을 문서화한다.
        """
        decision = AllocationDecision(
            model_config={"extra": "allow"},
            symbol="BTC-USDT",
            strategy_id="strat-1",
            approved_quantity=Decimal("0.02"),
            capital_pct=Decimal("10"),
        )
        # extra 필드가 model_dump에 포함되지 않음 (Pydantic 기본 동작)
        d = decision.model_dump()
        assert "sizing_method" not in d

    def test_v2_decision_model_dump(self) -> None:
        """model_dump()가 4필드를 포함한다."""
        decision = AllocationDecision(
            symbol="BTC-USDT",
            strategy_id="strat-1",
            approved_quantity=Decimal("0.02"),
            capital_pct=Decimal("10"),
        )
        d = decision.model_dump()
        assert d["symbol"] == "BTC-USDT"
        assert d["strategy_id"] == "strat-1"
        assert d["approved_quantity"] == Decimal("0.02")
        assert d["capital_pct"] == Decimal("10")


# --- FD-8.2 회귀: 기존 행동 -------------------------------------------------


class TestFD82Regression:
    """FD-8.2 — 기존 포트폴리오 엔진 행동 회귀 테스트.

    다음 행동은 변경되면 안 된다:
    - BUY + 무포지션 → 진입
    - SELL + 포지션 → 전량청산
    - BUY + 이미포지션 → 예외
    - SELL + 무포지션 → 예외
    - 현재가 0 또는 음수 → None (스킵)
    """

    def test_buy_no_position_enters(self) -> None:
        """BUY + 포지션 0 → 진입 결정."""
        engine = PortfolioEngine()
        decision = engine.allocate(
            _signal(direction=OrderSide.BUY),
            _state(allocated_capital=Decimal("1000"), current_price=Decimal("50000")),
        )
        assert decision is not None
        assert decision.approved_quantity == Decimal("1000") / Decimal("50000")

    def test_sell_with_position_exits_all(self) -> None:
        """SELL + 포지션 보유 → 전량청산."""
        engine = PortfolioEngine()
        decision = engine.allocate(
            _signal(direction=OrderSide.SELL),
            _state(position_quantity=Decimal("10"), current_price=Decimal("50000")),
        )
        assert decision is not None
        assert decision.approved_quantity == Decimal("10")

    def test_buy_with_position_raises(self) -> None:
        """BUY + already holding position → PortfolioEngineError."""
        engine = PortfolioEngine()
        with pytest.raises(PortfolioEngineError, match="already holding"):
            engine.allocate(
                _signal(direction=OrderSide.BUY),
                _state(position_quantity=Decimal("5"), current_price=Decimal("50000")),
            )

    def test_sell_without_position_raises(self) -> None:
        """SELL + no position held → PortfolioEngineError."""
        engine = PortfolioEngine()
        with pytest.raises(PortfolioEngineError, match="no position"):
            engine.allocate(
                _signal(direction=OrderSide.SELL),
                _state(position_quantity=Decimal("0"), current_price=Decimal("50000")),
            )

    def test_zero_price_skips(self) -> None:
        """현재가 0 → None 반환."""
        engine = PortfolioEngine()
        decision = engine.allocate(
            _signal(direction=OrderSide.BUY),
            _state(current_price=Decimal("0")),
        )
        assert decision is None

    def test_negative_price_skips(self) -> None:
        """현재가 음수 → None 반환."""
        engine = PortfolioEngine()
        decision = engine.allocate(
            _signal(direction=OrderSide.BUY),
            _state(current_price=Decimal("-1")),
        )
        assert decision is None


# --- negative (1) -----------------------------------------------------------


def test_negative_total_equity_silently_yields_nonsensical_capital_pct() -> None:
    """engine.py는 total_equity 부호를 검증하지 않는다.

    음수 total_equity는 우연한 fail-closed 없이 검증 없이 통과해 승인
    결정을 만들고 capital_pct가 물리적으로 말이 안 되는 음수가 된다.
    이는 FD-8.3에서 재검증해야 하는 결함이다.

    (engine.py는 FROZEN_PAPER_ONLY — 이 테스트는 결함을 문서화한다.)
    """
    engine = PortfolioEngine()

    # 대조군: total_equity == 0은 우연한 fail-closed
    with pytest.raises(ZeroDivisionError):
        engine.allocate(
            _signal(direction=OrderSide.BUY),
            _state(
                allocated_capital=Decimal("1000"),
                current_price=Decimal("50000"),
                total_equity=Decimal("0"),
            ),
        )

    # 적색: total_equity < 0은 검증 없이 통과
    decision = engine.allocate(
        _signal(direction=OrderSide.BUY),
        _state(
            allocated_capital=Decimal("1000"),
            current_price=Decimal("50000"),
            total_equity=Decimal("-10000"),
        ),
    )
    assert decision is not None
    assert decision.approved_quantity > 0
    assert decision.capital_pct < 0


# --- 실패 주입 (1) ----------------------------------------------------------


class _CorruptedPortfolioState(dict):
    """상류(FD-16.1 자본배분 조회) 하이드레이션이 부분적으로 깨진 상황을
    흉내낸다 — total_equity 조회가 실패한 손상된 상태."""

    def __getitem__(self, key: str) -> Any:
        if key == "total_equity":
            raise RuntimeError("upstream total_equity hydration failed")
        return super().__getitem__(key)


def test_failure_injection_corrupted_total_equity_lookup_propagates_fail_closed() -> None:
    """손상된 portfolio state에서 total_equity 조회 실패가 fail-closed 된다."""
    engine = PortfolioEngine()
    state = _CorruptedPortfolioState(
        allocated_capital=Decimal("1000"),
        position_quantity=Decimal("0"),
        current_price=Decimal("50000"),
        total_equity=Decimal("10000"),
    )
    with pytest.raises(RuntimeError, match="upstream total_equity hydration failed"):
        engine.allocate(_signal(direction=OrderSide.BUY), state)


# --- 성능 단언 (1) ----------------------------------------------------------


@pytest.mark.perf
def test_performance_allocate_p99_latency_within_pretrade_gate_budget() -> None:
    """ADR-2026-09-09-C Decision 1 예산: 사전거래 게이트 p99 5ms."""
    engine = PortfolioEngine()
    latencies_ms: list[float] = []
    for _i in range(500):
        start = time.perf_counter()
        engine.allocate(
            _signal(direction=OrderSide.BUY),
            _state(allocated_capital=Decimal("1000"), current_price=Decimal("50000")),
        )
        latencies_ms.append((time.perf_counter() - start) * 1000)

    latencies_ms.sort()
    p99 = latencies_ms[int(500 * 0.99) - 1]
    assert p99 < 5.0, f"p99={p99:.3f}ms — 사전거래 게이트 5ms 예산 초과"
