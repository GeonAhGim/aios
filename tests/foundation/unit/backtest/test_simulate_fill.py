"""simulate_fill() 단위테스트 — 체결가/수수료/슬리피지 계산 검증."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

import src.foundation.backtest.application.simulate_fill as simulate_fill_module
from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.simulate_fill import simulate_fill
from src.foundation.backtest.domain.models import CostModel
from tests.conftest import PerfBudget

_NOW = datetime(2026, 1, 2, tzinfo=UTC)


def _bar(*, open_price: str = "100") -> Candle:
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(open_price),
        high=Decimal(open_price),
        low=Decimal(open_price),
        close=Decimal(open_price),
        volume=Decimal("1"),
        open_time=_NOW,
        close_time=_NOW,
    )


def test_buy_fill_pays_slippage_above_open() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("10"))  # 0.1%
    fill = simulate_fill(
        bar=_bar(open_price="100"),
        bar_index=1,
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("100.1")
    assert fill.slippage_cost == Decimal("0.2")  # (100.1-100) * 2
    assert fill.fee == Decimal("0")


def test_sell_fill_receives_slippage_below_open() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("10"))
    fill = simulate_fill(
        bar=_bar(open_price="100"),
        bar_index=1,
        side=OrderSide.SELL,
        quantity=Decimal("2"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("99.9")
    assert fill.slippage_cost == Decimal("0.2")


def test_fee_applied_on_effective_price_not_base_price() -> None:
    cost_model = CostModel(fee_bps=Decimal("100"), slippage_bps=Decimal("0"))  # 1%
    fill = simulate_fill(
        bar=_bar(open_price="100"),
        bar_index=0,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("100")
    assert fill.fee == Decimal("1")


def test_fill_timestamp_and_symbol_come_from_bar() -> None:
    bar = _bar(open_price="50")
    fill = simulate_fill(
        bar=bar,
        bar_index=3,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
    )
    assert fill.timestamp == bar.open_time
    assert fill.symbol == bar.symbol
    assert fill.bar_index == 3


def test_zero_quantity_is_rejected() -> None:
    """수량 0은 체결로 성립하지 않는다 -- BarFillSimulator의 명시적 불변식."""
    with pytest.raises(ValueError, match="quantity"):
        simulate_fill(
            bar=_bar(open_price="100"),
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("0"),
            cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        )


def test_negative_quantity_is_rejected() -> None:
    """음수 수량은 매도/매수 방향을 side로 이미 표현하므로 부호로 또
    표현하면 이중 의미가 되어 거부한다."""
    with pytest.raises(ValueError, match="quantity"):
        simulate_fill(
            bar=_bar(open_price="100"),
            bar_index=1,
            side=OrderSide.SELL,
            quantity=Decimal("-1"),
            cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        )


def test_negative_slippage_bps_is_rejected() -> None:
    """음수 slippage_bps는 "항상 유리한 방향으로만 체결된다"는 비현실적
    가정을 인코딩하게 되므로 거부한다(46번 §2 비용모델 원칙)."""
    with pytest.raises(ValueError, match="slippage_bps"):
        simulate_fill(
            bar=_bar(open_price="100"),
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("-10")),
        )


def test_underlying_simulator_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """simulate_fill()은 109 SS5 문서대로 BarFillSimulator에 대한 얇은
    위임 래퍼일 뿐이다 -- 어댑터 의존성이 예외를 던지면 삼키지 않고
    그대로 전파해야 한다(실패주입)."""

    class _ExplodingSimulator:
        def simulate(self, **_kwargs: object) -> None:
            raise RuntimeError("adapter dependency failure")

    monkeypatch.setattr(simulate_fill_module, "_SIMULATOR", _ExplodingSimulator())

    with pytest.raises(RuntimeError, match="adapter dependency failure"):
        simulate_fill(
            bar=_bar(open_price="100"),
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        )


@pytest.mark.perf
def test_simulate_fill_completes_within_latency_budget(perf_budget: PerfBudget) -> None:
    """단일 체결 계산은 순수 산술이라 p95 1ms 예산을 크게 밑돌아야
    한다 -- 숫자 성능 단언(D2 floor).

    raw time.perf_counter() → perf_budget.assert_within(process_time 기반) 전환(task-10951)."""
    cost_model = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5"))
    bar = _bar(open_price="100")

    perf_budget.assert_within(
        lambda: simulate_fill(
            bar=bar,
            bar_index=0,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            cost_model=cost_model,
        ),
        budget_ms=1.0,
        batch=200,
        label="simulate_fill avg",
    )


# ---- gate-red 재현 ----


def test_gate_red_budget_actually_fails_past_budget(perf_budget: PerfBudget) -> None:
    """PerfBudget.assert_within 이 tautology가 아님을 증명 -- 동일한
    연산에 터무니없이 낮은 예산(budget_ms=-1.0)을 주면 반드시 적색으로
    전환된다(task-11539 감사 후속)."""

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: simulate_fill(
                bar=_bar(open_price="100"),
                bar_index=0,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                cost_model=CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5")),
            ),
            budget_ms=-1.0,
            n=5,
            batch=200,
            label="gate_red simulate_fill",
        )


# ---- failure-injection: CPU 시간 주입 → 단언이 적색으로 전환됨 ----
# PerfBudget.sample 은 time.process_time() 을 사용하므로, sleep() 주입은
# CPU 측정에 보이지 않는다. 대신 time.process_time 를 monkeypatch 해 실제
# CPU 시간보다 큰 값을 반환하게 하면 예산 단언이 반드시 실패해야 한다.


@pytest.mark.perf
def test_simulate_fill_budget_assertion_fails_on_cpu_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정상 입력이 예산을 통과하는 측정 경로를, CPU 시간을 주입하면
    적색으로 전환한다 -- 성능 게이트가 지연 회귀를 실제로 적색으로
    포착한다는 것을 단언 수준에서 증명 (D2 DoD 실패주입 1 건).

    `PerBudget` 은 `time.process_time()` 을 쓰므로, `sleep()` 주입은
    CPU 시간 측정에 보이지 않는다. `PerfBudget` 이 `time` 모듈을
    import 하므로, `tests.support.perf_budget.time.process_time` 을
    monkeypatch 해 실제 CPU 시간基础上 고정 지연을 더한다.

    simulate_fill 는 실제 CPU 시간이 ~0ms 로 너무 작아 단순 배율 곱셈으로
    Windows process_time 양자화(~15.6ms tick) 를 넘지 못한다.
    대신 실제 측정값에 5ms 고정 지연을 더하면 original_budget(1.0ms) 을
    명확히 초과하므로, budget=1.0ms 단언이 반드시 적색으로 전환된다.
    """
    import tests.support.perf_budget as _perf_budget_mod

    original_process_time = _perf_budget_mod.time.process_time
    # call counter — 매 호출마다 process_time 반환값에 누적 지연 추가
    _call_count = [0]

    def _slow_process_time() -> float:
        _call_count[0] += 1
        # 각 호출마다 0.01초(10ms) 씩 누적 — 1회 측정당 2회 호출이므로
        # 20ms 누적 → cpu_ms = 20 * 1000 / batch >> budget(1.0ms).
        return original_process_time() + _call_count[0] * 0.01

    monkeypatch.setattr(_perf_budget_mod.time, "process_time", _slow_process_time)

    try:
        budget_ms = 1.0
        samples = perf_budget.samples(
            lambda: simulate_fill(
                bar=_bar(open_price="100"),
                bar_index=0,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                cost_model=CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5")),
            ),
            n=200,
            batch=8,
        )
        cpu_values_ms = sorted(s.cpu_ms for s in samples)
        p95_ms = cpu_values_ms[min(int(len(cpu_values_ms) * 0.95), len(cpu_values_ms) - 1)]
        assert p95_ms < budget_ms, (
            f"주입된 CPU 시간이 예산을 초과해야 함 — p95={p95_ms:.4f}ms budget={budget_ms:.1f}ms"
        )
        pytest.fail(
            f"assert_within 이 적색을 raised 하지 않음 — "
            f"p95={p95_ms:.4f}ms budget={budget_ms:.1f}ms "
            f"주입이 감지되지 않음"
        )
    except AssertionError as exc:
        # 적색이 감지됨 — 기대 동작
        assert "budget" in str(exc) or "p95" in str(exc)
    finally:
        _perf_budget_mod.time.process_time = original_process_time
