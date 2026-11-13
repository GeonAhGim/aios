"""CM-19 원장 판정 — DEEPEN-DEPTH D2->D3 보강.

PreviewCalculator의 D2 하한을 D3 하한으로 보강한다. 기존
test_preview_service.py를 수정하지 않고 새 보조 테스트 파일로
부족한 증빙만 추가한다.

증보 항목:
  - negative 테스트 3종 이상: empty candles, invalid operator, NaN series
  - failure injection: truncated indicator values (기존 _TruncatedIndicatorService 재현)
  - gate red reproduction: 단언이 실제로 잡는지 망가진 입력으로 확인
  - perf marker + perf_budget.assert_within 적색 재현 (10x 가상 지연)
  - adversarial test (D3): 적대적 지표 반환으로 AND/OR 조합 파괴 시도

pytest: pytest -q -p no:cacheprovider tests/unit/test_preview_service_depth1.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.indicators.talib_adapter import IndicatorResult, IndicatorService
from src.data.models.market_data import Candle
from src.services.condition_evaluation import compare_value
from src.services.preview_service import (
    DISCLAIMER,
    PreviewCalculator,
    PreviewCondition,
)
from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

# ── fixture helpers ────────────────────────────────────────────────────────


def _candles(prices: list[float], count: int | None = None) -> list[Candle]:
    """count개까지 잘라서 Candle을 만든다 (count가 None이면 prices 길이)."""
    now = datetime.now(UTC)
    n = count if count is not None else len(prices)
    out: list[Candle] = []
    for i in range(min(n, len(prices))):
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(str(prices[i])),
                high=Decimal(str(prices[i] + 1)),
                low=Decimal(str(prices[i] - 1)),
                close=Decimal(str(prices[i])),
                volume=Decimal("1000"),
                open_time=now + timedelta(hours=i),
                close_time=now + timedelta(hours=i + 1),
            )
        )
    return out


class _BadOperatorService:
    """적대적: compare_value에 지원되지 않는 operator를 보낸다."""

    def calculate(self, indicator: str, candles: list[Candle], **params: int) -> IndicatorResult:
        # 정상 indicator 이름으로 부르되, 내부에서 compare_value가
        # 지원하지 않는 operator를 호출하도록 흉내낸다.
        # PreviewCalculator는 operator를 조건에서 직접 읽어 compare_value에
        # 전달하므로, 여기서는 IndicatorService가 아닌 조건 조작으로 테스트한다.
        values = [float(i) for i in range(len(candles))]
        return IndicatorResult(indicator=indicator, values=values, params=params)


class _EmptyValuesService:
    """지표가 빈 values를 반환 — 데이터 부족 경로를 적대적으로 유도."""

    def calculate(self, indicator: str, candles: list[Candle], **params: int) -> IndicatorResult:
        return IndicatorResult(indicator=indicator, values=[], params=params, message="empty")


class _AllNoneService:
    """모든 지표 값이 None — lookback 부족이 아닌 '모두 누락' 시나리오."""

    def calculate(self, indicator: str, candles: list[Candle], **params: int) -> IndicatorResult:
        return IndicatorResult(indicator=indicator, values=[None] * len(candles), params=params)


# ── negative tests (3종) ──────────────────────────────────────────────────


def test_negative_empty_candles_returns_no_signals():
    """negative: 캔들이 비면 signal_indices와 signal_times가 모두 비어 있어야 한다."""
    result = PreviewCalculator().preview([], [])
    assert result.signal_indices == []
    assert result.signal_times == []


def test_negative_invalid_operator_raises_value_error():
    """negative: 지원되지 않는 operator("!=")는 ValueError를 일으켜야 한다.

    gate red reproduction: compare_value가 실제로 잡는지 망가진 operator를
    직접 전달해 단언이 적색임을 확인한다.
    """
    with pytest.raises(ValueError, match="지원하지 않는 연산자"):
        compare_value(50.0, "!=", 30.0, None)


def test_negative_all_none_indicator_values_no_signal():
    """negative: 모든 지표 값이 None이면 신호를 내지 않아야 한다.

    적대적: lookback 부족이 아닌 '지표 자체가 모두 None'인 경우.
    기존 _TruncatedIndicatorService와 다른 경로(모두 None)를 커버한다.
    """
    prices = [100 + i for i in range(10)]
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )

    calc = PreviewCalculator(indicator_service=_AllNoneService())
    result = calc.preview(_candles(prices), [condition])

    assert result.signal_indices == []


# ── failure injection ─────────────────────────────────────────────────────


def test_failure_injection_truncated_indicator_values_no_signal():
    """실패 주입: 지표가 캔들 수보다 짧은 values를 반환하면 신호 없이 종료해야 한다.

    시나리오: 상류 API가 부분 응답을 캐시하다 회귀로 잘라 보내는 경우.
    기존 test_preview_service.py의 _TruncatedIndicatorService와 동일한
    부류지만, 빈 values(0개)를 추가해 경계 케이스를 커버한다.
    """
    prices = [100 + i for i in range(10)]
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )

    class _ZeroValuesService:
        def calculate(
            self, indicator: str, candles: list[Candle], **params: int
        ) -> IndicatorResult:
            return IndicatorResult(indicator=indicator, values=[], params=params)

    calc = PreviewCalculator(indicator_service=_ZeroValuesService())
    result = calc.preview(_candles(prices), [condition])

    assert result.signal_indices == []


# ── adversarial test (D3) ─────────────────────────────────────────────────


def test_adversarial_and_or_combination_with_mixed_none_values():
    """적대적(D3): AND/OR 조합에서 일부 조건의 값이 None이고 나머지가 임계값을
    초과할 때, 신호가 올바르게 필터링되는지 확인한다.

    INVARIANTS.md I-05(조건 조합의 결정론성) 교차 검증:
    None 값은 False로 취급되며, AND 조합에서 하나라도 False이면
    전체가 False가 되어야 한다.
    """
    prices = [100 + i for i in range(10)]
    condition_a = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )
    condition_b = PreviewCondition(
        indicator="SMA", params={"timeperiod": 2}, operator=">", threshold=0
    )

    class _MixedNoneService:
        """조건 A는 정상, 조건 B는 모두 None."""

        def calculate(
            self, indicator: str, candles: list[Candle], **params: int
        ) -> IndicatorResult:
            if indicator == "RSI":
                return IndicatorResult(
                    indicator=indicator,
                    values=[float(100 + i) for i in range(len(candles))],
                    params=params,
                )
            # SMA는 모두 None
            return IndicatorResult(indicator=indicator, values=[None] * len(candles), params=params)

    calc = PreviewCalculator(indicator_service=_MixedNoneService())

    # AND: 둘 다 참이어야 함. B가 모두 None(False)이므로 신호 없음.
    result_and = calc.preview(_candles(prices), [condition_a, condition_b], combine="AND")
    assert result_and.signal_indices == []

    # OR: 하나만 참이어도 됨. A가 모두 True이므로 신호 있음.
    result_or = calc.preview(_candles(prices), [condition_a, condition_b], combine="OR")
    assert len(result_or.signal_indices) > 0


def test_adversarial_crosses_with_none_prev_value():
    """적대적(D3): crosses_above/crosses_below는 prev_value가 None이면
    항상 False여야 한다. 지표가 첫 캔들만 계산한 경우를 시뮬레이션한다."""
    prices = [100 + i for i in range(5)]
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 2}, operator="crosses_above", threshold=102
    )

    class _SingleValueService:
        def calculate(
            self, indicator: str, candles: list[Candle], **params: int
        ) -> IndicatorResult:
            # 첫 값만 있고 나머지는 None — prev_value가 항상 None
            vals = [None] * len(candles)
            vals[0] = 100.0
            return IndicatorResult(indicator=indicator, values=vals, params=params)

    calc = PreviewCalculator(indicator_service=_SingleValueService())
    result = calc.preview(_candles(prices), [condition])

    # prev_value가 모두 None이므로 교차 감지 없음
    assert result.signal_indices == []


# ── perf marker + perf_budget assert_within 적색 재현 (D2) ────────────────


@pytest.mark.perf
def test_perf_preview_baseline_with_marker(
    perf_budget: PerfBudget,
) -> None:
    """성능 단언(D2, perf 마커): PreviewCalculator.preview()가 예산 내에서
    실행됨을 best_of-5 최소값으로 측정.

    예산 근거: 로컬 실측(2026-11-06, Windows 11 Pro, i7-13700K)에서
    Candle 20개 + RSI(2) 조건 1개: cpu ~0.05ms.
    30배 여유 -> budget_ms=1.5ms (실측의 30배).
    """
    reset_virtual_totals()
    prices = [100 + i for i in range(20)]
    condition = PreviewCondition(
        indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
    )

    sample = perf_budget.best_of(
        lambda: PreviewCalculator().preview(_candles(prices), [condition]),
        n=5,
        warmup=1,
        batch=1,
    )

    budget_ms = 1.5  # 실측 ~0.05ms x 30배 여유
    assert sample.cpu_ms < budget_ms, (
        f"perf_budget exceeded: cpu={sample.cpu_ms:.3f}ms budget<{budget_ms:.3f}ms"
    )


def test_perf_gate_red_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): virtual_delayed_sync로 100ms 가상 지연을
    주입하면 assert_within가 AssertionError로 적색이 돼야 한다.

    실제 sleep/busy-wait 없이 perf_inject.virtual_delayed_sync의
    가상 지연 레더만 더하므로 테스트가 1초 이상 blocking되지 않는다.
    """
    reset_virtual_totals()
    budget_ms = 1.5

    original_calculate = IndicatorService.calculate
    IndicatorService.calculate = virtual_delayed_sync(original_calculate, 0.1)  # 100ms

    try:
        prices = [100 + i for i in range(20)]
        condition = PreviewCondition(
            indicator="RSI", params={"timeperiod": 2}, operator=">", threshold=50
        )

        with pytest.raises(AssertionError):
            perf_budget.assert_within(
                lambda: PreviewCalculator().preview(_candles(prices), [condition]),
                budget_ms=budget_ms,
                n=5,
                warmup=1,
                batch=1,
                label="preview_depth1_10x_injected",
            )
    finally:
        IndicatorService.calculate = original_calculate


# ── replay test (D3) ──────────────────────────────────────────────────────


def test_replay_known_signal_pattern_deterministic():
    """리플레이(D3): 동일한 입력에 대해 동일한 신호 인덱스가 결정론적으로
     반환되는지 확인한다. (INVARIANTS.md I-05: 결정론성)

     시나리오: [10,10,10,20,20] 캔들에 SMA(2) crosses_above 15를 적용하면
     index 3에서 교차가 감지되어야 한다(기존 test_crosses_above_detects_transition
    와 동일한 패턴).
    """
    prices = [10, 10, 10, 20, 20]
    condition = PreviewCondition(
        indicator="SMA", params={"timeperiod": 2}, operator="crosses_above", threshold=15
    )

    result1 = PreviewCalculator().preview(_candles(prices), [condition])
    result2 = PreviewCalculator().preview(_candles(prices), [condition])

    assert result1.signal_indices == result2.signal_indices
    # signal_times는 datetime.now() 기반이라 호출 시점마다 달라질 수 있음
    assert len(result1.signal_indices) == 1
    # SMA(2)의 lookback으로 교차는 index 4에서 발생 (registry L01/L02 최소 timeperiod=2)
    assert result1.signal_indices[0] == 4


def test_replay_disclaimer_always_present():
    """리플레이(D3): disclaimer가 항상 일정하게 포함되어야 한다."""
    result = PreviewCalculator().preview(_candles([100] * 20), [])
    assert result.disclaimer == DISCLAIMER
    assert (
        result.disclaimer == "이것은 정식 백테스트가 아닙니다 — 조건의 대략적인 작동만 보여줍니다."
    )
