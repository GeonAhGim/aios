"""R-29 — execution_loop/correlation_service.py 단위테스트.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-29.
`correlated_exposure`가 상관행렬 계산을 재구현하지 않고
risk_stats.correlation_matrix로 위임하는지, 미지 페어·최소 중첩 미달을
0.0(무상관)으로 암묵 치환하지 않고 None을 반환하는지(레거시
`correlation.py`의 하드코딩 표 결함 재발 방지, R3 fail-closed)를
검증한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.data.models.market_data import Candle
from src.services.execution_loop.correlation_service import correlated_exposure

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_THRESHOLD = 0.7
_MIN_OVERLAP = 10


def _make_candles(closes: list[Decimal], *, symbol: str) -> list[Candle]:
    return [
        Candle(
            symbol=symbol,
            exchange="bitget",
            timeframe="1d",
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1"),
            open_time=_BASE + timedelta(days=i),
            close_time=_BASE + timedelta(days=i + 1),
        )
        for i, close in enumerate(closes)
    ]


def _proportional_closes(n: int, *, start: Decimal, factor: Decimal) -> list[Decimal]:
    """`factor`가 같은 부호면 원본과 완전 양의 상관(+1.0), 반대 부호면
    완전 음의 상관(-1.0)이 되는 종가열 — 실제 수익률 기반 상관 계산이
    올바로 위임되는지 결정론적으로 증명하기 위한 헬퍼."""
    shocks = [Decimal("0.01") if i % 2 == 0 else Decimal("-0.006") for i in range(n)]
    closes = [start]
    for shock in shocks:
        closes.append(closes[-1] * (Decimal("1") + shock * factor))
    return closes


def test_no_positions_returns_zero_exposure_and_zero_correlation():
    exposure, max_corr = correlated_exposure(
        {}, [], "BTC/USDT", threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP
    )
    assert exposure == Decimal("0")
    assert max_corr == 0.0


def test_self_symbol_position_needs_no_history():
    """대상 심볼 자신에 대한 기존 포지션은 상관 1.0이 자명하므로 histories가
    비어 있어도(fail-closed로 None이 되지 않고) 그대로 노출로 잡혀야 한다."""
    exposure, max_corr = correlated_exposure(
        {}, [("BTC/USDT", Decimal("500"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )
    assert exposure == Decimal("500")
    assert max_corr == 1.0


def test_correlated_position_above_threshold_is_summed_via_risk_stats_delegation():
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    other_closes = _proportional_closes(30, start=Decimal("50"), factor=Decimal("1"))
    histories = {
        "BTC/USDT": _make_candles(target_closes, symbol="BTC/USDT"),
        "ETH/USDT": _make_candles(other_closes, symbol="ETH/USDT"),
    }

    exposure, max_corr = correlated_exposure(
        histories, [("ETH/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert max_corr is not None and max_corr > 0.99  # 비례 수익률 → 상관 ≈ 1.0
    assert exposure == Decimal("300")


def test_anti_correlated_position_below_threshold_excluded_from_exposure():
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    other_closes = _proportional_closes(30, start=Decimal("50"), factor=Decimal("-1"))
    histories = {
        "BTC/USDT": _make_candles(target_closes, symbol="BTC/USDT"),
        "ETH/USDT": _make_candles(other_closes, symbol="ETH/USDT"),
    }

    exposure, max_corr = correlated_exposure(
        histories, [("ETH/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert max_corr is not None and max_corr < -0.99  # 완전 반비례 → 상관 ≈ -1.0
    assert exposure == Decimal("0")  # threshold(0.7) 미만이라 노출 합산 제외


def test_missing_history_for_other_symbol_returns_none_not_zero():
    """레거시 `correlation_with()`는 표에 없는 페어를 0.0(무상관)으로 조용히
    치환해 통과시켰다 — 여기선 히스토리가 아예 없는 심볼(XRP/USDT)은
    "판단 불가"이지 "무상관"이 아니므로 반드시 None을 반환해야 한다."""
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    histories = {"BTC/USDT": _make_candles(target_closes, symbol="BTC/USDT")}

    exposure, max_corr = correlated_exposure(
        histories, [("XRP/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert exposure is None
    assert max_corr is None


def test_missing_target_history_with_other_positions_returns_none_not_zero():
    other_closes = _proportional_closes(30, start=Decimal("50"), factor=Decimal("1"))
    histories = {"ETH/USDT": _make_candles(other_closes, symbol="ETH/USDT")}

    exposure, max_corr = correlated_exposure(
        histories, [("ETH/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert exposure is None
    assert max_corr is None


def test_overlap_below_min_overlap_returns_none_not_zero():
    """중첩 표본이 `min_overlap` 미만이면(짧은 히스토리) 상관을 신뢰할 수
    없으므로 0.0이 아니라 None — 호출자가 DENY로 처리해야 한다."""
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    short_other_closes = _proportional_closes(3, start=Decimal("50"), factor=Decimal("1"))
    histories = {
        "BTC/USDT": _make_candles(target_closes, symbol="BTC/USDT"),
        "ETH/USDT": _make_candles(short_other_closes, symbol="ETH/USDT"),
    }

    exposure, max_corr = correlated_exposure(
        histories, [("ETH/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert exposure is None
    assert max_corr is None


def test_multiple_positions_aggregate_same_symbol_before_correlation_lookup():
    exposure, max_corr = correlated_exposure(
        {}, [("BTC/USDT", Decimal("200")), ("BTC/USDT", Decimal("100"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )
    assert exposure == Decimal("300")
    assert max_corr == 1.0
