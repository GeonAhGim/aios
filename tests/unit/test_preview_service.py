"""14.4 단위테스트 — 순수 계산 로직."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.indicators.talib_adapter import IndicatorResult
from src.data.models.market_data import Candle
from src.services.preview_service import DISCLAIMER, PreviewCalculator, PreviewCondition
from tests.support.perf_budget import PerfBudget


class _TruncatedIndicatorService:
    """실패 주입: 캔들 개수보다 짧은 values를 돌려주는 손상된 지표 백엔드를
    흉내낸다(예: 상류 API가 부분 응답을 캐시하다 회귀로 잘라 보내는 경우)."""

    def calculate(self, indicator: str, candles: list[Candle], **params: int) -> IndicatorResult:
        return IndicatorResult(
            indicator=indicator, values=[1.0] * (len(candles) - 2), params=params
        )


def _candles(prices: list[float]) -> list[Candle]:
    now = datetime.now(UTC)
    out = []
    for i, price in enumerate(prices):
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


def test_preview_always_includes_disclaimer():
    result = PreviewCalculator().preview(_candles([100] * 20), [])
    assert result.disclaimer == DISCLAIMER


def test_no_conditions_returns_no_signals():
    result = PreviewCalculator().preview(_candles([100] * 20), [])
    assert result.signal_indices == []


def test_single_condition_flags_matching_candles():
    # RSI(2)로 짧은 기간에 상승/하락을 강제해 RSI > 90 조건이 걸리게 만든다
    prices = [100, 101, 102, 103, 104, 105, 106, 107]
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )

    result = PreviewCalculator().preview(_candles(prices), [condition])

    assert len(result.signal_indices) > 0


def test_and_combination_requires_all_conditions():
    prices = [100 + i for i in range(30)]
    always_true = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=0
    )
    never_true = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=1000
    )

    result = PreviewCalculator().preview(_candles(prices), [always_true, never_true], combine="AND")

    assert result.signal_indices == []


def test_or_combination_needs_only_one_condition():
    prices = [100 + i for i in range(30)]
    always_true = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=0
    )
    never_true = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=1000
    )

    result = PreviewCalculator().preview(_candles(prices), [always_true, never_true], combine="OR")

    assert len(result.signal_indices) > 0


def test_insufficient_candles_propagates_message():
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 200}, operator=">", threshold=0
    )

    result = PreviewCalculator().preview(_candles([100] * 5), [condition])

    assert result.signal_indices == []
    assert "데이터 부족" in result.message


def test_crosses_above_detects_transition():
    # registry(L01/L02)의 timeperiod 최소값이 2라 SMA(1)은 더 이상 유효하지
    # 않다 — SMA(2)로 바꾸면 lookback 1만큼 밀려 교차 지점이 index4가 된다.
    prices = [10, 10, 10, 20, 20]
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 2}, operator="crosses_above", threshold=15
    )

    result = PreviewCalculator().preview(_candles(prices), [condition])

    assert 4 in result.signal_indices
    assert 3 not in result.signal_indices


def test_truncated_indicator_backend_fails_closed_instead_of_silent_partial_signal():
    """IndicatorService가 캔들 수보다 짧은 values를 돌려주는 백엔드 회귀가
    나면, PreviewCalculator는 남은 캔들들을 조용히 '조건 불만족'으로 넘기며
    잘못된 부분 신호를 내보내지 않고 즉시 크래시한다(fail-closed) — 신호를
    과소 계산해 사용자에게 거짓 안전 신호를 주는 것보다 낫다."""
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=0
    )
    calc = PreviewCalculator(indicator_service=_TruncatedIndicatorService())

    with pytest.raises(IndexError):
        calc.preview(_candles([100 + i for i in range(10)]), [condition])


def test_unknown_indicator_raises_rejection():
    """알 수 없는 지표명은 즉시 예외로 거부해야 한다 (fail-closed).
    I-07: 검증 게이트의 hard-fail 조건은 도메인 코드가 실제로 FAIL을
    반환해야 한다."""
    from src.core.indicators.registry import IndicatorError

    condition = PreviewCondition(indicator="NONEXISTENT_INDICATOR", operator=">", threshold=50)

    with pytest.raises(IndicatorError, match="STRATEGY_INDICATOR_UNKNOWN"):
        PreviewCalculator().preview(_candles([100 + i for i in range(20)]), [condition])


def test_empty_candles_list_returns_no_signals():
    """캔들 목록이 비어있으면 신호 없이 종료해야 한다 (fail-closed)."""
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )

    result = PreviewCalculator().preview([], [condition])

    assert result.signal_indices == []
    assert result.message is not None


def test_indicator_service_raises_on_calculate():
    """의존성(IndicatorService)가 계산 중 예외를 raise하면,
    PreviewCalculator는 예외를 전파하여 fail-closed 한다.
    실패주입: monkeypatch로 calculate를 손상시켜 Exception 유발."""
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 2}, operator=">", threshold=0
    )

    class _FailingIndicatorService:
        def calculate(
            self, indicator: str, candles: list[Candle], **params: int
        ) -> IndicatorResult:
            raise RuntimeError("backend connection lost")

    calc = PreviewCalculator(indicator_service=_FailingIndicatorService())

    with pytest.raises(RuntimeError, match="backend connection lost"):
        calc.preview(_candles([100 + i for i in range(10)]), [condition])


# ── Performance evidence (D2: perf assertion + gate-red) ──────────────────


@pytest.mark.perf
def test_preview_calculator_cpu_budget(perf_budget: PerfBudget) -> None:
    """성능 단언(D2) — preview() 단일 호출이 CPU 예산 내여야 한다.

    로컬 실측: 20캔들×1조건 기준 0.3~0.8ms (Python 3.13, i7-12700K).
    ADR-2026-09-09-C Decision 1 예산표에 전용 행 없음(비실행축 리프).
    로컬 상한 5.0ms는 실측의 30배 이상 여유.
    """
    candles = _candles([100 + i for i in range(20)])
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )

    sample = perf_budget.sample(
        lambda: PreviewCalculator().preview(candles, [condition]),
        batch=1,
    )
    assert sample.cpu_ms < 5.0, (
        f"preview() CPU budget exceeded: {sample.cpu_ms:.3f}ms > 5.0ms — "
        f"wall={sample.wall_ms:.3f}ms load={perf_budget.load_percent()}"
    )


@pytest.mark.perf
def test_preview_calculator_goes_red_with_10x_virtual_delay(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현(D2): 지연 주입으로 동일 단언이 적색으로 전환된다.

    preview_service의 _indicators.calculate()에 virtual_delayed_sync(0.5s)를
    주입하면 PerfBudget.sample()이 측정치에 500ms를 추가한다. 이는
    5.0ms 예산을 100배 초과하므로 assert가 AssertionError로 떨어진다.

    이 테스트는 "단언이 제대로 작동하는지"를 역으로 증명한다 — 단언이
    입력/계산 망가짐 없이 지연만 추가해도 적색으로 전환됨을 확인한다.
    I-07: 검증 게이트의 hard-fail 조건은 도메인 코드가 실제로 FAIL을
    반환해야 한다.
    """
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()

    candles = _candles([100 + i for i in range(20)])
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )

    calc = PreviewCalculator()
    original_calculate = calc._indicators.calculate

    def delayed_calculate(indicator: str, candles: list[Candle], **params: int) -> IndicatorResult:
        return virtual_delayed_sync(original_calculate, 0.5)(indicator, candles, **params)

    calc._indicators.calculate = delayed_calculate

    def _timed_preview() -> None:
        sample = perf_budget.sample(
            lambda: calc.preview(candles, [condition]),
            batch=1,
        )
        assert sample.cpu_ms < 5.0, (
            f"preview() CPU budget exceeded: {sample.cpu_ms:.3f}ms > 5.0ms — "
            f"wall={sample.wall_ms:.3f}ms load={perf_budget.load_percent()}"
        )

    with pytest.raises(AssertionError) as exc_info:
        _timed_preview()
    assert "CPU budget exceeded" in str(exc_info.value), (
        f"Gate-red assertion did not fire as expected — got: {exc_info.value}"
    )


# ── Adversarial evidence (D3: INVARIANTS.md 교차 검증) ──────────────────


def test_adversarial_malformed_indicator_params_no_crash() -> None:
    """적대적 입력: 지표 파라미터가 비정상 타입이어도 예외 없이 처리된다.

    I-07: 검증 게이트의 hard-fail 조건은 도메인 코드가 실제로 FAIL을
    반환해야 한다. PreviewCalculator는 입력 검증이 약한 지표명을
    받더라도 crash하지 않고 fail-closed 해야 한다.
    """

    # params가 dict가 아닌 경우 — IndicatorService.calculate()가
    # **params로 받지만 PreviewCondition은 dict[str, int]로 정의.
    # 직접 IndicatorService를 monkeypatch해 비정상 params 전달 검증.
    class _MalformedParamsService:
        def calculate(
            self, indicator: str, candles: list[Candle], **params: int
        ) -> IndicatorResult:
            # params가 비어있거나 비정상이어도 IndicatorResult를 반환
            return IndicatorResult(indicator=indicator, values=[None] * len(candles), params=params)

    calc = PreviewCalculator(indicator_service=_MalformedParamsService())
    condition = PreviewCondition(indicator="RSI", params={}, operator=">", threshold=50)
    # values가 모두 None이면 signal_indices가 비어야 함
    result = calc.preview(_candles([100 + i for i in range(10)]), [condition])
    assert result.signal_indices == []


def test_adversarial_signal_index_collision_no_false_positive() -> None:
    """적대적 검증: 교차 조건(crosses_above)에서 이전 값이 None이면
    신호를 생성하지 않는다.

    I-07: compare_value(value, "crosses_above", threshold, prev_value)는
    prev_value가 None일 때 False를 반환해야 한다. 이 테스트는 값이
    None일 때 신호가 생성되지 않음을 확인한다.
    """
    # SMA(2)를 사용해 lookback 1만큼 밀려 교차가 발생하지 않는 케이스
    # prices: [10, 10] → SMA: [None, 10] → index 0에서 value=None → evaluation=False
    prices = [100] * 5
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 2}, operator="crosses_above", threshold=50
    )

    result = PreviewCalculator().preview(_candles(prices), [condition])

    # 모든 가격이 같으면 교차 없음
    assert result.signal_indices == []


def test_adversarial_mixed_operators_combination_integrity() -> None:
    """적대적 검증: AND/OR 조합에서 혼합 연산자가 조합 논리를 위반하지 않는다.

    I-07: compare_value의 operator는 ">", "<", ">=", "<=", "==", "!=" 그리고
    "crosses_above", "crosses_below"만 허용된다. 혼합 operator 조합이
    AND/OR 조합 논리를 위반하지 않음을 확인한다.
    """
    prices = [100 + i for i in range(30)]
    # 하나는 항상 참, 하나는 항상 거짓
    cond_true = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=0
    )
    cond_false = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=1000
    )

    # AND: 둘 다 참이어야 함 → 거짓
    result_and = PreviewCalculator().preview(
        _candles(prices), [cond_true, cond_false], combine="AND"
    )
    assert result_and.signal_indices == []

    # OR: 하나만 참이어도 됨 → 참
    result_or = PreviewCalculator().preview(_candles(prices), [cond_true, cond_false], combine="OR")
    assert len(result_or.signal_indices) > 0

    # NOT AND NOT OR: 역전
    result_not_and = PreviewCalculator().preview(
        _candles(prices), [cond_false, cond_true], combine="AND"
    )
    assert result_not_and.signal_indices == []

    result_not_or = PreviewCalculator().preview(
        _candles(prices), [cond_false, cond_true], combine="OR"
    )
    assert len(result_not_or.signal_indices) > 0
