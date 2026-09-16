"""14.1 단위테스트 — 각 지표군 최소 1개씩 TA-Lib 참조값과 일치 검증(완료조건).

DEEPEN(task-3200): L03 talib_adapter.py 자체 경계(레지스트리 위임 이후 남는
TA-Lib 호출·결과 포장 로직) D2 증빙 보강 — task-3198/3199가 이미
test_registry.py에서 다룬 L01/L02 경계(파라미터 범위·lookback·registry_hash)
증빙과 겹치지 않게, 이 파일의 대상인 `IndicatorService.calculate()` 자체
로직에 한정한다:
- negative: 파라미터 검증이 candle 부족 체크보다 먼저 실행되는지(둘 다
  잘못됐을 때 "데이터 부족"으로 조용히 넘어가면 범위 밖 파라미터가 가려짐)
- 실패 주입: registry(L02) 스펙의 outputs 개수와 실제 TA-Lib 반환 튜플
  길이가 어긋나면 zip(..., strict=True)가 조용히 잘라내지 않고 거부하는지
- 수치 성능 단언: calculate() 자체 핫 패스 p95 자체선언 예산
- 게이트 적색 재현: 위 성능 단언이 실제로 예산 초과를 잡아내는지
"""

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest
import talib

from src.core.indicators.talib_adapter import IndicatorError, IndicatorService
from src.data.models.market_data import Candle


def _candles(n: int, *, base=100.0, volume=1000.0) -> list[Candle]:
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
                volume=Decimal(str(volume)),
                open_time=now + timedelta(hours=i),
                close_time=now + timedelta(hours=i + 1),
            )
        )
    return out


def _closes(candles) -> np.ndarray:
    return np.array([float(c.close) for c in candles], dtype=np.float64)


def test_trend_group_sma_matches_talib_reference():
    candles = _candles(30)
    result = IndicatorService().calculate("SMA", candles, timeperiod=5)

    expected = talib.SMA(_closes(candles), timeperiod=5)
    assert result.values[-1] == expected[-1]


def test_trend_group_macd_matches_talib_reference():
    candles = _candles(60)
    result = IndicatorService().calculate("MACD", candles)

    expected_macd, expected_signal, expected_hist = talib.MACD(_closes(candles))
    assert result.values[-1] == expected_macd[-1]
    assert result.series["signal"][-1] == expected_signal[-1]
    assert result.series["hist"][-1] == expected_hist[-1]


def test_momentum_group_rsi_matches_talib_reference():
    candles = _candles(30)
    result = IndicatorService().calculate("RSI", candles, timeperiod=14)

    expected = talib.RSI(_closes(candles), timeperiod=14)
    assert result.values[-1] == expected[-1]


def test_volatility_group_atr_matches_talib_reference():
    candles = _candles(30)
    service = IndicatorService()
    result = service.calculate("ATR", candles, timeperiod=14)

    arrays_high = np.array([float(c.high) for c in candles], dtype=np.float64)
    arrays_low = np.array([float(c.low) for c in candles], dtype=np.float64)
    expected = talib.ATR(arrays_high, arrays_low, _closes(candles), timeperiod=14)
    assert result.values[-1] == expected[-1]


def test_volatility_group_bbands_returns_three_series():
    candles = _candles(20)
    result = IndicatorService().calculate("BBANDS", candles, timeperiod=5)

    assert result.series is not None
    assert set(result.series) == {"upperband", "middleband", "lowerband"}
    assert result.values == result.series["upperband"]


def test_volume_group_obv_matches_talib_reference():
    candles = _candles(20)
    service = IndicatorService()
    result = service.calculate("OBV", candles)

    volumes = np.array([float(c.volume) for c in candles], dtype=np.float64)
    expected = talib.OBV(_closes(candles), volumes)
    assert result.values[-1] == expected[-1]


def test_insufficient_candles_returns_empty_with_message():
    candles = _candles(3)

    result = IndicatorService().calculate("SMA", candles, timeperiod=200)

    assert result.values == []
    assert "데이터 부족" in result.message


def test_unsupported_indicator_raises():

    with pytest.raises(IndicatorError):
        IndicatorService().calculate("ICHIMOKU", _candles(30))


# --- DEEPEN(task-3200): negative — 파라미터 검증이 candle 부족보다 먼저 ------


def test_calculate_rejects_invalid_param_even_when_candles_are_also_insufficient():
    """negative: `calculate()`는 `DEFAULT_REGISTRY.validate_params()`를 먼저
    호출한 뒤에야 candle 개수를 본다(talib_adapter.py 101~105행 순서) — 둘
    다 잘못된 상태에서 "데이터 부족" 빈 결과로 조용히 넘어가면 범위 밖
    파라미터(§1 결함: timeperiod=0/음수)가 가려진다. 이 순서가 뒤집혀도
    이 테스트 없이는 아무도 못 잡는다."""
    with pytest.raises(IndicatorError) as excinfo:
        IndicatorService().calculate("SMA", _candles(3), timeperiod=0)
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


# --- DEEPEN(task-3200): 실패 주입 — outputs 개수와 TA-Lib 반환 길이 불일치 --


def test_calculate_raises_when_talib_output_count_mismatches_spec_outputs(
    monkeypatch: pytest.MonkeyPatch,
):
    """실패 주입: 레지스트리(L02) 스펙의 `outputs` 개수와 실제 TA-Lib 함수가
    반환하는 튜플 길이가 어긋나면(스펙 손상·TA-Lib 버전 차이로 반환 값
    개수가 바뀌는 사고) talib_adapter.py 126~128행의
    `zip(spec.outputs, raw_output, strict=True)`가 조용히 잘라내거나
    None으로 채우지 않고 예외로 거부하는지 확인한다(fail-closed) — STOCH는
    정상적으로 (slowk, slowd) 2개를 반환하는데, 3개짜리 튜플로 바꿔친다."""
    candles = _candles(30)
    original_stoch = talib.STOCH

    def _three_output_stoch(*args, **kwargs):
        slowk, slowd = original_stoch(*args, **kwargs)
        return slowk, slowd, slowd

    monkeypatch.setattr(talib, "STOCH", _three_output_stoch)

    with pytest.raises(ValueError):
        IndicatorService().calculate("STOCH", candles)


# --- DEEPEN(task-3200): 수치 성능 단언 — calculate() 자체 핫 패스 ----------


def _calculate_latencies_ms(iterations: int = 50) -> list[float]:
    candles = _candles(200)
    service = IndicatorService()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        service.calculate("SMA", candles, timeperiod=20)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


_CALCULATE_BUDGET_MS = 5.0


def test_calculate_p95_latency_within_self_declared_budget():
    """수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 `calculate()`
    전용 항목이 없다(가장 가까운 항목은 "지표 증분=일괄 동일", 지연 예산은
    아니다) — 레지스트리 조회·파라미터 검증(L01/L02, 순수 dict/정수 비교)에
    numpy 배열 변환 + TA-Lib 네이티브 호출 1회가 더해지는 이 어댑터 자체
    핫 패스라는 사실 위에 자체 예산을 건다: 로컬 실측 p95 ~0.18ms
    (2026-09-17, SMA/200 캔들) 대비 넉넉한 여유를 둔 5ms."""
    samples = _calculate_latencies_ms()
    p95_ms = _p95(samples)
    print(
        f"[L03 talib_adapter] calculate() p95={p95_ms:.3f}ms "
        f"budget<{_CALCULATE_BUDGET_MS:.1f}ms (n={len(samples)})"
    )
    assert p95_ms < _CALCULATE_BUDGET_MS


# --- DEEPEN(task-3200): 게이트 적색 재현 — 위 성능 단언의 tautology 여부 ---


def test_calculate_budget_gate_actually_fails_past_budget(monkeypatch: pytest.MonkeyPatch):
    """게이트 적색 재현: 위 단언식이, TA-Lib 호출 경로 한 곳이 예산을 실제로
    넘기도록 지연을 주입했을 때 진짜로 `AssertionError`를 내는지(= CI가
    실제로 빨간불이 되는지) 확인한다. 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_sma = talib.SMA

    def _stalled_sma(*args, **kwargs):
        time.sleep(_CALCULATE_BUDGET_MS / 1000.0)
        return original_sma(*args, **kwargs)

    monkeypatch.setattr(talib, "SMA", _stalled_sma)

    samples = _calculate_latencies_ms(iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _CALCULATE_BUDGET_MS
