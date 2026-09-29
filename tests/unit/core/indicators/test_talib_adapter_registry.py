"""L03 talib_adapter.py: registry 위임 계약 테스트.

Split from `test_registry.py` (500-LOC 정책, ADR-2026-09-10-C §7) —
`IndicatorService.calculate()`가 L01/L02(spec/registry)에 올바르게 위임하는지,
TA-Lib 네이티브 실패를 감추지 않고 그대로 전파하는지 확인한다.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import talib

from src.core.indicators import talib_adapter
from src.core.indicators.registry import IndicatorError, IndicatorRegistry
from src.core.indicators.spec import REGISTRY_VERSION
from src.core.indicators.specs_talib import TALIB_SPECS
from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle


def _candles(n: int, *, base: float = 100.0) -> list[Candle]:
    now = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        price = base + i
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=Decimal("1000"),
                open_time=now + timedelta(hours=i),
                close_time=now + timedelta(hours=i + 1),
            )
        )
    return out


def test_talib_adapter_has_no_leftover_specs_or_period_param_name() -> None:
    """옛 지표별 딕셔너리(`_SPECS`) 잔재 검사 — IND-10 이후 정식 이름
    `TALIB_SPECS`(레지스트리 카탈로그) 언급은 이 검사 대상이 아니다."""
    source = inspect.getsource(talib_adapter)
    assert "_SPECS" not in source.replace("TALIB_SPECS", "")
    assert "period_param_name" not in source


@pytest.mark.parametrize("timeperiod", [0, -1, -5, 501])
def test_calculate_rejects_out_of_range_param_via_registry(timeperiod: int) -> None:
    with pytest.raises(IndicatorError) as excinfo:
        IndicatorService().calculate("SMA", _candles(30), timeperiod=timeperiod)
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_calculate_rejects_unknown_indicator_via_registry() -> None:
    with pytest.raises(IndicatorError) as excinfo:
        IndicatorService().calculate("ICHIMOKU", _candles(30))
    assert excinfo.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_indicator_result_registry_version_matches_registry() -> None:
    result = IndicatorService().calculate("SMA", _candles(30), timeperiod=5)
    assert result.registry_version == REGISTRY_VERSION
    assert result.registry_version == "ind-v1"


@pytest.mark.parametrize("name", ["MACD", "BBANDS", "STOCH"])
def test_calculate_min_required_bars_matches_registry_lookback(name: str) -> None:
    registry = IndicatorRegistry()
    spec = TALIB_SPECS[name]
    default_params = {p.name: p.default for p in spec.params}
    lookback = registry.lookback(name, default_params)

    too_few = IndicatorService().calculate(name, _candles(lookback))
    assert too_few.values == []

    just_enough = IndicatorService().calculate(name, _candles(lookback + 1))
    assert just_enough.values != []


# --- DEEPEN(task-3198): 실패 주입 — TA-Lib 네이티브 실패가 성공으로 위장되지 않음 ---


def test_calculate_propagates_talib_runtime_failure_instead_of_masking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: TA-Lib 네이티브 함수가 예외를 던지면(손상된 빌드·SIMD 버전
    불일치 등) `IndicatorService.calculate()`가 그 예외를 삼키고 빈 결과나
    기본값으로 위장하지 않고 그대로 전파하는지 확인한다(fail-closed, L03이
    L01/L02에 위임하는 경계 — 계산 실패를 검증 실패처럼 감추면 안 된다)."""

    def _broken_sma(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated TA-Lib native failure")

    monkeypatch.setattr(talib, "SMA", _broken_sma)

    with pytest.raises(RuntimeError, match="simulated TA-Lib native failure"):
        IndicatorService().calculate("SMA", _candles(30), timeperiod=5)
