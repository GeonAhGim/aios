"""Backtest domain/rules.py 단위테스트 — DB 없이 순수 함수만 검증."""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from src.foundation.backtest.domain.models import CostModel
from src.foundation.backtest.domain.rules import (
    CostModelRequiredError,
    LookaheadViolationError,
    assert_fill_after_signal,
    has_enough_warmup,
    is_look_ahead_safe,
    require_cost_model,
    warn_if_zero_cost,
)
from tests.conftest import PerfBudget


@dataclass
class _FakeEvent:
    bar_index: int


def test_is_look_ahead_safe_rejects_same_bar_fill() -> None:
    assert is_look_ahead_safe(signal_bar_index=5, fill_bar_index=5) is False


def test_is_look_ahead_safe_rejects_earlier_bar_fill() -> None:
    assert is_look_ahead_safe(signal_bar_index=5, fill_bar_index=4) is False


def test_is_look_ahead_safe_accepts_next_bar_fill() -> None:
    assert is_look_ahead_safe(signal_bar_index=5, fill_bar_index=6) is True


def test_warn_if_zero_cost_warns_on_fully_zero_model() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    assert warn_if_zero_cost(cost_model) is not None


def test_warn_if_zero_cost_silent_when_fee_present() -> None:
    cost_model = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("0"))
    assert warn_if_zero_cost(cost_model) is None


def test_has_enough_warmup_rejects_when_no_bars_left() -> None:
    assert has_enough_warmup(total_bars=20, warmup_bars=20) is False


def test_has_enough_warmup_accepts_when_bars_remain() -> None:
    assert has_enough_warmup(total_bars=21, warmup_bars=20) is True


def test_assert_fill_after_signal_raises_on_same_bar_fill() -> None:
    order_ev = _FakeEvent(bar_index=5)
    fill_ev = _FakeEvent(bar_index=5)
    with pytest.raises(LookaheadViolationError) as exc_info:
        assert_fill_after_signal(order_ev, fill_ev)
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


def test_assert_fill_after_signal_raises_on_earlier_bar_fill() -> None:
    order_ev = _FakeEvent(bar_index=5)
    fill_ev = _FakeEvent(bar_index=3)
    with pytest.raises(LookaheadViolationError) as exc_info:
        assert_fill_after_signal(order_ev, fill_ev)
    assert exc_info.value.signal_bar_index == 5
    assert exc_info.value.fill_bar_index == 3


def test_assert_fill_after_signal_passes_on_next_bar_fill() -> None:
    order_ev = _FakeEvent(bar_index=5)
    fill_ev = _FakeEvent(bar_index=6)
    assert assert_fill_after_signal(order_ev, fill_ev) is None


def test_require_cost_model_rejects_all_zero_when_disallowed() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    with pytest.raises(CostModelRequiredError) as exc_info:
        require_cost_model(cost_model, allow_zero=False)
    assert exc_info.value.error_code == "VALIDATION_COST_MODEL_REQUIRED"


def test_require_cost_model_allows_all_zero_when_permitted() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    assert require_cost_model(cost_model, allow_zero=True) is None


# -- D2 보강: failure injection ------------------------------------------------


class _PoisonedEvent:
    """`bar_index` 접근 시 예외를 던지는 손상된 이벤트 -- 업스트림(이벤트
    루프)이 직렬화 오류 등으로 필드를 채우지 못한 상황을 흉내낸다."""

    @property
    def bar_index(self) -> int:
        raise RuntimeError("corrupted event: bar_index unavailable")

    @bar_index.setter
    def bar_index(self, value: int) -> None:
        raise RuntimeError("corrupted event: bar_index unavailable")


def test_assert_fill_after_signal_propagates_poisoned_event_exception() -> None:
    """실패 주입: `bar_index` 접근이 예외를 던지는 손상된 이벤트가 들어오면
    `assert_fill_after_signal`이 예외를 삼키지 않고 그대로 전파해야 한다 --
    손상된 이벤트를 조용히 "통과"로 오판하는 것보다 루프 중단이 안전하다
    (CLAUDE.md §3 기본 태세 fail-closed)."""
    good_order = _FakeEvent(bar_index=5)
    poisoned_fill = _PoisonedEvent()
    with pytest.raises(RuntimeError, match="corrupted event"):
        assert_fill_after_signal(good_order, poisoned_fill)


# -- D2 보강: 수치 성능 단언 -----------------------------------------------------


@pytest.mark.perf
def test_assert_fill_after_signal_throughput_within_budget(perf_budget: PerfBudget) -> None:
    """수치 성능 단언: 백테스트 이벤트 루프는 매 bar마다 이 순수 검증을
    호출한다(spec §9 L29, I2) -- 100,000회 호출이 200ms 예산 안에 들어
    루프 병목이 아님을 보증한다. task-7434: wall-clock `perf_counter()` 대신
    공용 `perf_budget`(process_time 기반)으로 측정해 xdist 코어 경합
    노이즈를 배제한다. `batch=10`은 Windows `GetProcessTimes()` 틱(15.625ms)
    양자화 오차를 호출당 ~1.6ms로 줄인다(200ms 예산 대비 안전)."""
    order_ev = _FakeEvent(bar_index=1)
    fill_ev = _FakeEvent(bar_index=2)

    def _run_once() -> None:
        for _ in range(100_000):
            assert_fill_after_signal(order_ev, fill_ev)

    perf_budget.assert_within(
        _run_once, budget_ms=200.0, batch=10, label="100k assert_fill_after_signal calls"
    )


# -- D2 보강: 게이트 적색 재현 ----------------------------------------------------


def test_gate_red_repro_off_by_one_would_allow_same_bar_fill() -> None:
    """게이트 적색 재현: `fill_bar_index > signal_bar_index`를 `>=`로 바꾼
    off-by-one 회귀본을 재현한다. `>=`면 같은 bar 체결(look-ahead
    violation)을 "안전"으로 오판해 그대로 통과시킨다(I2 위반을 놓침) --
    실제 구현은 엄격한 `>`로 이를 거부한다."""

    def _regressed_is_look_ahead_safe(*, signal_bar_index: int, fill_bar_index: int) -> bool:
        return fill_bar_index >= signal_bar_index  # off-by-one 회귀

    # 회귀본: 같은 bar 체결을 "안전"으로 오판 -- 적색
    assert _regressed_is_look_ahead_safe(signal_bar_index=5, fill_bar_index=5) is True

    # 실제 구현: 같은 bar 체결을 거부 -- 녹색
    assert is_look_ahead_safe(signal_bar_index=5, fill_bar_index=5) is False
    with pytest.raises(LookaheadViolationError):
        assert_fill_after_signal(_FakeEvent(bar_index=5), _FakeEvent(bar_index=5))
