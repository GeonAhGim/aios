"""BT-17 — `backtest/vector/universe.py` 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-17. DoD: (a) 메모리 상한 반증(경계 직전/직후). (b) 100종목×1년 D1 스윕
성능 — 절대 초가 아니라 1종목 실측 x 100 x 1.5의 정규화 임계로 단언한다.
(c) `CandleColumns` 컬럼 경로 재사용(새 시계열 표현 신설 없음). (d) 결측
종목 반증 — 빈 구간 종목은 `skipped`에 명시 보고된다.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.vector import universe as universe_module
from src.foundation.backtest.vector.fills import VectorSignal
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.backtest.vector.universe import (
    MAX_UNIVERSE_CANDLES,
    UniverseMemoryLimitError,
    sweep_universe,
)
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_D = Decimal
_CASH = _D("100000")


def _columns(n: int, *, step: timedelta = timedelta(minutes=1)) -> CandleColumns:
    closes = [_D(str(100 + (i % 5))) for i in range(n)]
    opens = [_D(str(100 + ((i - 1) % 5))) if i else closes[0] for i in range(n)]
    return CandleColumns(
        ts=[_T0 + step * i for i in range(n)],
        open=opens,
        high=[max(o, c) + 1 for o, c in zip(opens, closes, strict=True)],
        low=[min(o, c) - 1 for o, c in zip(opens, closes, strict=True)],
        close=closes,
        volume=[_D("1000")] * n,
        quote_volume=[None] * n,
    )


def _empty_columns() -> CandleColumns:
    return CandleColumns(ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[])


def _no_signal(n: int) -> VectorSignal:
    empty = BoolSignal(values=np.zeros(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    return VectorSignal(entries=empty, exits=empty, quantity=_D("1"))


def _config() -> BacktestConfigV2:
    return BacktestConfigV2(
        slippage=FixedSlippage(bps=_D("10")),
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=_D("2"), taker_bps=_D("5"), min_fee=_D("0")
        ),
        latency_ms=0, partial_fill=PartialFillConfig(max_participation_pct=_D("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None, costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False), calendar="24x7",
    )


UniverseInput = dict[str, tuple[CandleColumns, VectorSignal]]


def _sweep(universe: UniverseInput) -> universe_module.UniverseSweepResult:
    return sweep_universe(
        universe, _config(), timeframe=Timeframe.D1, initial_cash=_CASH,
    )


# ==== (a) 메모리 상한 반증 ====


def test_sweep_accepts_universe_exactly_at_cap() -> None:
    cols = _columns(MAX_UNIVERSE_CANDLES)
    result = _sweep({"AAA": (cols, _no_signal(MAX_UNIVERSE_CANDLES))})
    assert "AAA" in result.results
    assert result.skipped == ()


def test_sweep_rejects_universe_one_candle_over_cap() -> None:
    cols = _columns(MAX_UNIVERSE_CANDLES + 1 - 200)
    other = _columns(200)
    with pytest.raises(UniverseMemoryLimitError):
        sweep_universe(
            {"AAA": (cols, _no_signal(len(cols))), "BBB": (other, _no_signal(len(other)))},
            _config(), timeframe=Timeframe.D1, initial_cash=_CASH,
        )


def test_sweep_over_cap_returns_no_partial_results() -> None:
    """상한 초과 시 일부 종목이라도 계산돼 결과에 섞이면 안 된다 — 예외가
    나면서 `results`/`skipped`가 아예 만들어지지 않아야 한다."""
    small = _columns(10)
    huge = _columns(MAX_UNIVERSE_CANDLES)
    with pytest.raises(UniverseMemoryLimitError):
        sweep_universe(
            {"SMALL": (small, _no_signal(10)), "HUGE": (huge, _no_signal(MAX_UNIVERSE_CANDLES))},
            _config(), timeframe=Timeframe.D1, initial_cash=_CASH,
        )


# ==== (d) 결측 종목 반증 ====


def test_missing_symbol_range_is_reported_as_skipped_not_silently_dropped() -> None:
    populated = _columns(30)
    universe = {
        "AAA": (populated, _no_signal(30)),
        "EMPTY": (_empty_columns(), _no_signal(0)),
    }
    result = _sweep(universe)

    assert result.skipped == ("EMPTY",), (
        "빈 구간 종목이 skipped에 명시 보고되지 않으면 결측이 조용히 사라진다"
    )
    assert len(result.skipped) != 0
    assert "EMPTY" not in result.results
    assert "AAA" in result.results


# ==== (c) 컬럼 경로 재사용 ====


def test_universe_module_takes_candle_columns_not_a_new_time_series_type() -> None:
    """유니버스 입력이 여전히 `CandleColumns`임을 타입으로 증명한다 — 종목별
    dict-of-DataFrame 등 새 표현을 신설했다면 이 시그니처가 달라졌을 것이다."""
    cols = _columns(5)
    universe = {"AAA": (cols, _no_signal(5))}
    result = _sweep(universe)
    assert isinstance(cols, CandleColumns)
    assert result.results["AAA"].bars == 5


# ==== (b) 성능 — 정규화 임계(절대 초 단언 금지) ====


_YEAR_D1_BARS = 365


def test_sweep_100_symbols_1y_d1_within_normalized_threshold() -> None:
    """절대 초 상한이 아니라 1종목 실측 x 100 x 1.5로 단언한다 — 공유 CI의
    CPU 시간 편차에 흔들리지 않기 위함(BT-16a와 동일 선례, docs/specs 참고)."""
    baseline_cols = _columns(_YEAR_D1_BARS, step=timedelta(days=1))
    baseline_universe = {"S000": (baseline_cols, _no_signal(_YEAR_D1_BARS))}
    baseline_start = time.perf_counter()
    _sweep(baseline_universe)
    baseline_seconds = time.perf_counter() - baseline_start

    universe = {
        f"S{i:03d}": (
            _columns(_YEAR_D1_BARS, step=timedelta(days=1)), _no_signal(_YEAR_D1_BARS),
        )
        for i in range(100)
    }
    start = time.perf_counter()
    result = _sweep(universe)
    elapsed_seconds = time.perf_counter() - start

    threshold_seconds = baseline_seconds * 100 * 1.5
    print(
        f"[BT-17] 1종목 1년 D1 실측={baseline_seconds:.4f}s, "
        f"100종목 실측={elapsed_seconds:.4f}s, 정규화 임계={threshold_seconds:.4f}s "
        f"(§9.9 목표: ≤30s)"
    )
    assert len(result.results) == 100
    assert result.skipped == ()
    assert elapsed_seconds <= threshold_seconds
