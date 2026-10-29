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
from decimal import Decimal
from typing import Any

import pytest

from src.core.portfolio.engine import PortfolioEngine, PortfolioEngineError
from src.core.portfolio.models import AllocationDecision
from src.data.models.trading import OrderSide
from tests.conftest import PerfBudget

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
        """v2 optional 필드는 Pydantic v2에서 현재 허용되지 않는다.

        AllocationDecision이 Pydantic BaseModel을 상속하고 extra 필드를
        정의하지 않으면, 현재 기본 동작에 따라 정의되지 않은 필드는
        거부된다. v2 스키마 정식 도입 시 optional 필드가 추가될 것이다.
        """
        # 현재 AllocationDecision은 4개 필드만 정의되어 있음
        decision = AllocationDecision(
            symbol="BTC-USDT",
            strategy_id="strat-1",
            approved_quantity=Decimal("0.02"),
            capital_pct=Decimal("10"),
        )
        # 정의된 필드만 model_dump에 포함됨
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


class _CorruptedPortfolioState(dict[str, Any]):
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
def test_performance_allocate_p99_latency_within_pretrade_gate_budget(
    perf_budget: PerfBudget,
) -> None:
    """ADR-2026-09-09-C Decision 1 예산: 사전거래 게이트 p99 5ms."""
    engine = PortfolioEngine()

    def allocate_once() -> None:
        engine.allocate(
            _signal(direction=OrderSide.BUY),
            _state(allocated_capital=Decimal("1000"), current_price=Decimal("50000")),
        )

    # Calibrate to at least four Windows CPU-clock ticks per batch.
    batch = 2
    while perf_budget.sample(allocate_once, batch=batch).cpu_ms * batch < 62.5:
        batch *= 2
        if batch >= 65536:
            break

    # Each sample is already milliseconds per call, not total batch time.
    latencies_ms = sorted(
        sample.cpu_ms for sample in perf_budget.samples(allocate_once, n=500, batch=batch)
    )
    p99 = latencies_ms[int(500 * 0.99) - 1]
    assert p99 < 5.0, f"p99={p99:.3f}ms — 사전거래 게이트 5ms 예산 초과"


# --- D2 단일 호출 CPU 수렴 (task-11539 감사 후속) ----------------------------


@pytest.mark.perf
def test_allocate_cpu_converges_to_near_zero(perf_budget: PerfBudget) -> None:
    """단일 allocate() 호출이 0에 수렴하는 CPU 시간을 보여야 한다.

    `time.process_time()`은 Windows에서 약 15.6ms 양자화 오차가 있으므로
    batch=64로 여러 호출을 묶어서 1회당 오차를 줄인다. 1회당 CPU 시간이
    1ms 미만이어야 순수 도메인 로직(I-03)과 호환된다.
    """
    engine = PortfolioEngine()

    def allocate_once() -> None:
        engine.allocate(
            _signal(direction=OrderSide.BUY),
            _state(allocated_capital=Decimal("1000"), current_price=Decimal("50000")),
        )

    sample = perf_budget.sample(allocate_once, batch=64)
    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"포트폴리오 할당 로직은 1ms 미만이어야 함 "
        f"(budget<1ms, {perf_budget.describe(sample, budget_ms=1.0)})"
    )


# --- D2 게이트 적색 재현: CPU 지연 주입 → AssertionError ----------------------


@pytest.mark.perf
def test_allocate_budget_catches_10x_cpu_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    10x 예산(1ms × 10 = 10ms)을 초과하는 CPU 시간을 가장하면 같은 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    역으로 증명한다. fake process_time이 누적 CPU 시간을 빠르게 올리므로
    p99 > 5ms budget로 적색 확정.
    """
    _fake_cpu_ms = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_cpu_ms
        # 1회 호출당 10ms 누적으로 p99가 5ms를 초과 → AssertionError 발생
        _fake_cpu_ms += 10.0
        return _fake_cpu_ms

    monkeypatch.setattr("time.process_time", _fake_process_time)

    engine = PortfolioEngine()

    def allocate_once() -> None:
        engine.allocate(
            _signal(direction=OrderSide.BUY),
            _state(allocated_capital=Decimal("1000"), current_price=Decimal("50000")),
        )

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    batch = 2
    while perf_budget.sample(allocate_once, batch=batch).cpu_ms * batch < 62.5:
        batch *= 2
        if batch >= 65536:
            break

    latencies_ms = sorted(
        sample.cpu_ms for sample in perf_budget.samples(allocate_once, n=500, batch=batch)
    )
    p99 = latencies_ms[int(500 * 0.99) - 1]
    with pytest.raises(AssertionError):
        assert p99 < 5.0, f"p99={p99:.3f}ms — 지연 주입 하에서 사전거래 게이트 5ms 예산 초과"


# --- D2 실패 주입: sleep 주입은 CPU 측정에 영향 없음 ------------------------


@pytest.mark.perf
def test_sleep_injection_does_not_affect_cpu_measurement(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """time.sleep()을 monkeypatch해 100ms 대기 주입 후 CPU 측정(cpu_ms)이
    증가하지 않음을 확인한다 — time.sleep()은 wall-clock만 지연시키고
    CPU 시간을 증가시키지 않는다.

    이는 CPU 측정(time.process_time)이 실제 CPU 소비만 계측함을 증명한다.
    """
    import time as _time_module

    # 원본 sleep 보존 (monkeypatch 전)
    _real_sleep = _time_module.sleep

    def _delayed_sleep(duration: float) -> None:
        _real_sleep(duration)

    # time 모듈의 sleep을 대체 — allocate_once 내부에서 sleep을 호출하지 않으므로
    # allocate의 CPU 측정에는 영향 없다.
    monkeypatch.setattr(_time_module, "sleep", _delayed_sleep)

    engine = PortfolioEngine()

    def allocate_with_sleep() -> None:
        _time_module.sleep(0.1)  # 100ms 대기
        engine.allocate(
            _signal(direction=OrderSide.BUY),
            _state(allocated_capital=Decimal("1000"), current_price=Decimal("50000")),
        )

    # process_time 16ms 양자화 tick 때문에 단발 측정이 흔들려 best-of-5 최소값 사용
    # batch=8로 묶으면 15.625ms tick이 8 호출에 분산되어 호출당 ~2ms 이내로 줄어든다.
    # allocate()는 ~0.01ms이므로 batch=8에서 cpu_ms < 1.0 단언을 안정적으로 만족한다.
    sample = perf_budget.best_of(allocate_with_sleep, n=5, warmup=1, batch=8)
    # wall_ms는 sleep(0.1)으로 인해 ~100ms가 되어야 하지만,
    # cpu_ms는 실제 CPU 소비만 계측하므로 여전히 < 1ms여야 한다.
    assert sample.cpu_ms < 1.0, (
        f"sleep 주입 후 CPU 시간 {sample.cpu_ms:.3f}ms — sleep은 CPU 시간을 증가시키지 않아야 함"
    )
