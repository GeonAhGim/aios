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

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.parity_harness import (
    ParityMismatchError,
    check_parity,
)
from src.foundation.backtest.domain.models import SimulatedFill
from src.services.oms.contracts.v1_events import FillEvent

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
        venue_ts=datetime(2026, 9, 1, 0, 0, seq, tzinfo=timezone.utc),
    )


def _backtest_fill(
    *, bar_index: int, side: OrderSide, quantity: Decimal, price: Decimal, fee: Decimal
) -> SimulatedFill:
    return SimulatedFill(
        bar_index=bar_index,
        # 리플레이 타임스탬프는 PAPER venue_ts와 구조적으로 다른 시계열
        # (봉 종가 시각)이다 — 비교에서 제외되는 필드임을 픽스처로도 보여준다.
        timestamp=datetime(2026, 9, 1, 1, bar_index, tzinfo=timezone.utc)
        + timedelta(seconds=1),
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
        seq=0, side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100.00"),
        fee=Decimal("0.10"),
    ),
    _paper_fill(
        seq=1, side=OrderSide.BUY, quantity=Decimal("2"), price=Decimal("101.50"),
        fee=Decimal("0.20"),
    ),
    _paper_fill(
        seq=2, side=OrderSide.SELL, quantity=Decimal("3"), price=Decimal("103.25"),
        fee=Decimal("0.30"),
    ),
]


def _matching_backtest_fills() -> list[SimulatedFill]:
    """같은 아티팩트·구간을 재생했다고 가정한, PAPER 추적과 값이 일치하는 리플레이."""
    return [
        _backtest_fill(
            bar_index=0, side=OrderSide.BUY, quantity=Decimal("1"), price=Decimal("100.00"),
            fee=Decimal("0.10"),
        ),
        _backtest_fill(
            bar_index=1, side=OrderSide.BUY, quantity=Decimal("2"), price=Decimal("101.50"),
            fee=Decimal("0.20"),
        ),
        _backtest_fill(
            bar_index=2, side=OrderSide.SELL, quantity=Decimal("3"), price=Decimal("103.25"),
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
        bar_index=0, side=OrderSide.BUY, quantity=Decimal("1.00"), price=Decimal("100.0000"),
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
        bar_index=0, side=OrderSide.BUY, quantity=Decimal("999"), price=Decimal("100.00"),
        fee=Decimal("0.10"),
    )
    backtest[2] = _backtest_fill(
        bar_index=2, side=OrderSide.SELL, quantity=Decimal("3"), price=Decimal("999.99"),
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
def test_each_compared_field_can_trigger_divergence(
    field: str, update: dict[str, object]
) -> None:
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
    with pytest.raises(ParityMismatchError, match="체결 개수 불일치"):
        report.raise_if_mismatch()


def test_empty_sequences_match() -> None:
    report = check_parity([], [])
    assert report.is_match is True
    assert report.paper_fill_count == report.backtest_fill_count == 0
