"""BT-19 `parity_harness.py` 테스트 — I-05(백테스트=라이브 패리티) 강제.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.5
BT-19, docs/design/INVARIANTS.md I-05. DoD: 고정 PAPER 추적 1건에 대해
체결 시퀀스가 바이트 동일(타임스탬프 제외), 인위적 1틱 어긋남을 주입하면
실패, 불일치 리포트에 첫 발산 지점 출력.

"PAPER 실행 추적"(FillEvent 순서열)과 "백테스트 리플레이"(SimulatedFill
순서열)는 서로 다른 두 도메인(OMS/백테스트)의 타입이라 실제 운영에서는
서로 다른 경로로 채워진다 — 이 통합 테스트는 그 경계를 넘나드는 대조
자체가 맞는지 증명한다(단위 테스트가 아니라 integration으로 분류한
이유: OMS 계약 타입 + 백테스트 도메인 타입을 함께 다룬다).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.parity_harness import (
    ParityMismatchError,
    check_parity,
)
from src.foundation.backtest.domain.models import SimulatedFill
from src.services.oms.contracts.v1_events import FillEvent
from tests.support.perf_inject import virtual_delayed_sync

_SYMBOL = "BTC-USDT"
_ONE_TICK = Decimal("0.01")


def _paper_fill(
    *, seq: int, side: OrderSide, quantity: Decimal, price: Decimal, fee: Decimal
) -> FillEvent:
    return FillEvent(
        provider_fill_id=f"paper-fill-{seq}",
        venue="paper",
        order_id=uuid4(),
        exchange_order_id=f"paper-order-{seq}",
        symbol=_SYMBOL,
        side=side,
        quantity=quantity,
        price=price,
        fee=fee,
        fee_currency="USDT",
        liquidity="TAKER",
        venue_ts=datetime(2026, 9, 1, 0, 0, seq, tzinfo=UTC),
    )


def _backtest_fill(
    *, bar_index: int, side: OrderSide, quantity: Decimal, price: Decimal, fee: Decimal
) -> SimulatedFill:
    return SimulatedFill(
        bar_index=bar_index,
        # 리플레이 타임스탬프는 PAPER venue_ts와 구조적으로 다른 시계열
        # (봉 종가 시각)이다 — 비교에서 제외되는 필드임을 픽스처로도 보여준다.
        timestamp=datetime(2026, 9, 1, 1, bar_index, tzinfo=UTC) + timedelta(seconds=1),
        symbol=_SYMBOL,
        side=side,
        price=price,
        quantity=quantity,
        fee=fee,
        slippage_cost=Decimal("0"),
    )


# 고정 PAPER 추적 1건(§DoD "고정 PAPER 추적 1건") — 3개 체결, 매수 2 + 매도 1.
_PAPER_TRACE = [
    _paper_fill(
        seq=0,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Decimal("100.00"),
        fee=Decimal("0.10"),
    ),
    _paper_fill(
        seq=1,
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        price=Decimal("101.50"),
        fee=Decimal("0.20"),
    ),
    _paper_fill(
        seq=2,
        side=OrderSide.SELL,
        quantity=Decimal("3"),
        price=Decimal("103.25"),
        fee=Decimal("0.30"),
    ),
]


def _matching_backtest_fills() -> list[SimulatedFill]:
    """같은 아티팩트·구간을 재생했다고 가정한, PAPER 추적과 값이 일치하는 리플레이."""
    return [
        _backtest_fill(
            bar_index=0,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100.00"),
            fee=Decimal("0.10"),
        ),
        _backtest_fill(
            bar_index=1,
            side=OrderSide.BUY,
            quantity=Decimal("2"),
            price=Decimal("101.50"),
            fee=Decimal("0.20"),
        ),
        _backtest_fill(
            bar_index=2,
            side=OrderSide.SELL,
            quantity=Decimal("3"),
            price=Decimal("103.25"),
            fee=Decimal("0.30"),
        ),
    ]


def test_matching_trace_reports_parity_excluding_timestamps() -> None:
    report = check_parity(_PAPER_TRACE, _matching_backtest_fills())
    assert report.is_match is True
    assert report.first_divergence is None
    assert report.paper_fill_count == report.backtest_fill_count == 3
    report.raise_if_mismatch()  # 통과 — 예외 없음


def test_decimal_representation_difference_alone_does_not_break_parity() -> None:
    """같은 값이면 `Decimal` 지수 표현이 달라도(예: "3" vs "3.00") 일치로 본다."""
    backtest = _matching_backtest_fills()
    backtest[0] = _backtest_fill(
        bar_index=0,
        side=OrderSide.BUY,
        quantity=Decimal("1.00"),
        price=Decimal("100.0000"),
        fee=Decimal("0.10"),
    )
    report = check_parity(_PAPER_TRACE, backtest)
    assert report.is_match is True


def test_one_tick_price_divergence_fails_and_reports_first_divergence() -> None:
    """DoD: 인위적 1틱 어긋남을 주입하면 실패하고, 그 지점이 첫 발산으로 보고된다."""
    backtest = _matching_backtest_fills()
    perturbed = backtest[1]
    backtest[1] = _backtest_fill(
        bar_index=perturbed.bar_index,
        side=perturbed.side,
        quantity=perturbed.quantity,
        price=perturbed.price + _ONE_TICK,  # 인위적 1틱 어긋남
        fee=perturbed.fee,
    )

    report = check_parity(_PAPER_TRACE, backtest)

    assert report.is_match is False
    assert report.first_divergence is not None
    assert report.first_divergence.index == 1
    assert report.first_divergence.field == "price"
    assert report.first_divergence.paper_value == "101.50"
    assert report.first_divergence.backtest_value == "101.51"
    with pytest.raises(ParityMismatchError, match="index 1") as exc_info:
        report.raise_if_mismatch()
    assert "price" in str(exc_info.value)


def test_divergence_at_earlier_index_reported_even_if_later_also_diverges() -> None:
    """뒤쪽에도 발산이 있어도 리포트는 항상 '첫' 발산 지점만 낸다."""
    backtest = _matching_backtest_fills()
    backtest[0] = _backtest_fill(
        bar_index=0,
        side=OrderSide.BUY,
        quantity=Decimal("999"),
        price=Decimal("100.00"),
        fee=Decimal("0.10"),
    )
    backtest[2] = _backtest_fill(
        bar_index=2,
        side=OrderSide.SELL,
        quantity=Decimal("3"),
        price=Decimal("999.99"),
        fee=Decimal("0.30"),
    )

    report = check_parity(_PAPER_TRACE, backtest)

    assert report.is_match is False
    assert report.first_divergence is not None
    assert report.first_divergence.index == 0
    assert report.first_divergence.field == "quantity"


@pytest.mark.parametrize(
    "field,update",
    [
        ("symbol", {"symbol": "ETH-USDT"}),
        ("side", {"side": OrderSide.SELL}),
        ("fee", {"fee": Decimal("0.11")}),
    ],
)
def test_each_compared_field_can_trigger_divergence(field: str, update: dict[str, object]) -> None:
    backtest = _matching_backtest_fills()
    backtest[0] = backtest[0].model_copy(update=update)

    report = check_parity(_PAPER_TRACE, backtest)

    assert report.is_match is False
    assert report.first_divergence is not None
    assert report.first_divergence.index == 0
    assert report.first_divergence.field == field


def test_length_mismatch_reports_shorter_length_as_divergence_index() -> None:
    backtest = _matching_backtest_fills()[:2]  # PAPER는 3건, 백테스트는 2건(누락)

    report = check_parity(_PAPER_TRACE, backtest)

    assert report.is_match is False
    assert report.paper_fill_count == 3
    assert report.backtest_fill_count == 2
    assert report.first_divergence is not None
    assert report.first_divergence.index == 2
    assert report.first_divergence.field == "__length__"
    with pytest.raises(ParityMismatchError, match="fill count mismatch"):
        report.raise_if_mismatch()


def test_empty_sequences_match() -> None:
    report = check_parity([], [])
    assert report.is_match is True
    assert report.paper_fill_count == report.backtest_fill_count == 0


def test_negative_reordered_fills_rejected_even_with_same_totals() -> None:
    """I-05/I-07: identical totals cannot hide a changed execution sequence."""
    backtest = _matching_backtest_fills()
    backtest[0], backtest[1] = backtest[1], backtest[0]

    report = check_parity(_PAPER_TRACE, backtest)

    with pytest.raises(ParityMismatchError, match="index 0") as exc_info:
        report.raise_if_mismatch()
    assert exc_info.value.report is report
    assert report.first_divergence is not None
    assert report.first_divergence.field == "quantity"
    assert report.first_divergence.paper_value == "1"
    assert report.first_divergence.backtest_value == "2"


@pytest.mark.parametrize("empty_paper", [True, False])
def test_negative_one_empty_trace_rejected(empty_paper: bool) -> None:
    report = check_parity(
        [] if empty_paper else _PAPER_TRACE,
        _matching_backtest_fills() if empty_paper else [],
    )

    with pytest.raises(ParityMismatchError, match="index 0"):
        report.raise_if_mismatch()
    assert report.first_divergence is not None
    assert report.first_divergence.field == "__length__"
    assert (report.paper_fill_count, report.backtest_fill_count) == (
        (0, 3) if empty_paper else (3, 0)
    )


def test_negative_extra_replay_fill_rejected() -> None:
    backtest = _matching_backtest_fills()
    backtest.append(backtest[-1])

    report = check_parity(_PAPER_TRACE, backtest)

    with pytest.raises(ParityMismatchError, match="index 3"):
        report.raise_if_mismatch()
    assert report.first_divergence is not None
    assert report.first_divergence.field == "__length__"
    assert report.first_divergence.paper_value == "3"
    assert report.first_divergence.backtest_value == "4"


@pytest.mark.parametrize(
    "converter_name", ["paper_fill_to_comparable", "backtest_fill_to_comparable"]
)
def test_failure_injection_conversion_error_propagates_without_partial_success(
    monkeypatch: pytest.MonkeyPatch, converter_name: str
) -> None:
    from src.foundation.backtest.application import parity_harness

    original = getattr(parity_harness, converter_name)
    failure = RuntimeError("injected conversion failure on second fill")
    calls = 0

    def fail_second(fill: FillEvent | SimulatedFill) -> parity_harness.ComparableFill:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure
        return original(fill)

    comparison = Mock(wraps=parity_harness._first_field_divergence)
    with monkeypatch.context() as patch:
        patch.setattr(parity_harness, converter_name, fail_second)
        patch.setattr(parity_harness, "_first_field_divergence", comparison)
        with pytest.raises(RuntimeError, match="injected conversion failure") as exc_info:
            check_parity(_PAPER_TRACE, _matching_backtest_fills())
        assert exc_info.value is failure
        assert calls == 2
        comparison.assert_not_called()

    check_parity(_PAPER_TRACE, _matching_backtest_fills()).raise_if_mismatch()


@pytest.mark.perf
def test_parity_throughput_within_monthly_backtest_budget(perf_budget) -> None:
    """ADR-2026-09-09-C: comparison alone must fit the 3s monthly M1 budget.

    CI `pytest_perf_parallel` 단계에서는 xdist 워커 간 CPU 경합과
    Windows `time.process_time()` ~15.6ms 양자화 오차가 겹쳐
    단일 샘플이 spike 한 번에 실패했다(task-15506, task-15364).
    `n=3`(best-of-3)로 경합 노이즈를, `batch=8`로 양자화 오차(15.6→~2ms)를
    각각 걷어내고 최소값으로 판정한다.
    """
    repetitions = 30 * 24 * 60 // len(_PAPER_TRACE)
    paper = _PAPER_TRACE * repetitions
    backtest = _matching_backtest_fills() * repetitions

    sample = perf_budget.assert_within(
        lambda: check_parity(paper, backtest),
        budget_ms=3000.0,
        n=3,
        warmup=0,
        batch=8,
        label="43,200 fill pair parity comparison — best-of-3 for CI parallel stability",
    )

    report = sample.result
    report.raise_if_mismatch()
    assert report.paper_fill_count == report.backtest_fill_count == 43_200


@pytest.mark.perf
def test_parity_cpu_converges_to_zero_under_best_of(perf_budget) -> None:
    """Windows `time.process_time()` ~15.6ms 양자화 tick이 단일 샘플을 흔들어도
    `best_of`(여러 번 중 최소)는 CPU 0 수렴을 안정적으로 보여준다.

    단발 sample이 15.6ms tick에 걸리면 예산 단언이 거짓으로 실패한다(task-15506).
    `best_of(n=10)`는 10회 측정 중 최소값을 돌려 그 흔들림을 걷어낸다.
    """
    sample = perf_budget.best_of(
        lambda: check_parity(_PAPER_TRACE, _matching_backtest_fills()),
        n=10,
        warmup=2,
        batch=8,
    )
    # 예산 단언: 3건 비교는 마이크로초 단위여야 한다(예산 3s의 0.001% 미만)
    assert sample.cpu_ms < 3.0, perf_budget.describe(sample, budget_ms=3.0)
    assert sample.wall_ms < 50.0  # wall도 양자화tick 4회 분以内

    report = sample.result
    report.raise_if_mismatch()


@pytest.mark.perf
def test_parity_virtual_delay_injection_detects_budget_exceedance(perf_budget) -> None:
    """가상 지연 주입으로 게이트 적색 재현 — 실제 sleep/busy-wait 없이.

    정상 입력은 녹색, 예산 10배 지연 주입은 적색(예상 실패)이어야 한다.
    `virtual_delayed_sync`는 CPU+wall ledger에 지연만 더하고 실제 대기하지 않는다.
    """
    # 정상 입력 — 녹색
    normal = perf_budget.assert_within(
        lambda: check_parity(_PAPER_TRACE, _matching_backtest_fills()),
        budget_ms=3000.0,
        n=3,
        batch=8,
        label="3-fill parity normal (green)",
    )
    assert normal.cpu_ms < 100.0  # 3건 비교는 100ms 미만

    # 예산 10배 지연 주입 — 적색
    delayed = virtual_delayed_sync(
        lambda: check_parity(_PAPER_TRACE, _matching_backtest_fills()),
        seconds=30.0,  # 예산 3s의 10배
    )
    with pytest.raises(AssertionError) as exc_info:
        perf_budget.assert_within(
            delayed,
            budget_ms=3000.0,
            n=3,
            batch=8,
            label="3-fill parity 10x virtual delay (red)",
        )
    # 적색 메시지에 지연이 반영됐음을 확인
    assert "cpu=" in str(exc_info.value)


@pytest.mark.perf
def test_parity_no_virtual_injection_leaves_measurement_untouched(perf_budget) -> None:
    """지연 주입이 없으면 측정치와 virtual ledger가 변경되지 않는다.

    injection failure test: 주입 함수가 호출되지 않았음을 ledger 변화 0으로 단언.
    """
    from tests.support.perf_inject import virtual_totals

    before = virtual_totals()
    sample = perf_budget.sample(lambda: check_parity(_PAPER_TRACE, _matching_backtest_fills()))
    after = virtual_totals()

    assert after == before, "virtual ledger must not move without injection"
    assert sample.cpu_ms < 100.0  # 정상 측정치
    sample.result.raise_if_mismatch()
