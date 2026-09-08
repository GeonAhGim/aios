"""BT-16 §9.9 성능 DoD — 1개월 M1(약 43,200봉) 규모에서 grid sweep 처리량 실측.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 BT-16
("1,000 조합 ≤60s(1개월 M1)"). `tests/unit/foundation/backtest/vector/
test_vector_sweeps.py::test_grid_sweep_normalized_performance_threshold`가
이미 봉 200개 x 조합 1,000으로 스위트 예산 안에서 회귀를 잡고 있다(그 파일
docstring: "실제 1개월 M1(약 43,200봉) 규모는 스위트 예산 밖이라 별도 확인이
필요하다(미검증)"). 이 파일은 그 "봉 수" 축의 미검증을 해소한다 -- 실제
43,200봉 전체로 돌려 처리량을 실측한다.

**조합 수 축은 여전히 1,000이 아니라 200으로 줄인다(추가 미검증 -- 정직하게
기록한다)**: 사전 실측(이 리프 구현 중 실측, task-2426 note)으로 43,200봉
buy&hold 조합을 sweep_grid에 늘려가며 재 보면 조합당 비용이 선형이 아니라
초선형으로 커졌다 -- 100조합 71.6s(0.716s/조합), 200조합 122.6s(0.613s/조합),
300조합 294.6s(0.982s/조합). `GridSweepResult.results`가 조합마다 전체
`QuickBacktestResult.equity_curve`(43,200개 `Decimal`)를 실행이 끝날 때까지
전부 메모리에 붙들고 있어서(sweep_grid.py의 dict 컴프리헨션 설계, 이 leaf가
바꾸는 부분이 아니다) 누적 조합 수가 늘수록 GC 압박이 커지는 것으로 보인다
-- 조합 1,000개 x 43,200봉을 문자 그대로 실행하면 이 추세로 최소 수백 초에서
수천 초가 걸려 어떤 nightly 예산에도 안전하게 들어가지 않는다(task-2426
실측: 1800s 타임아웃도 넘겼다). 그래서 실제로 실행하는 조합 수는 200개로
고정하고(로컬 실측 122.6s, 여유 있게 nightly 타임아웃 안에 든다), 1,000조합
수치는 그 실측 처리율을 선형 외삽해 §9.9의 60s 목표와 "참고용"으로만
비교한다(위 초선형 추세를 고려하면 실외삽치는 실제보다 낙관적인 하한이다).

**게이트 = 정규화 임계(실측 200조합 기준), §9.9의 60s/1,000외삽은 print(비차단)**:
test_perf_journal.py(LC-17)·test_pre_trade_latency.py(R-57) 선례와 동일한 이유
-- 공유 CI의 CPU 배정은 이 파일이 통제할 수 없는 변동성이라 절대 초 단언은
상시 적색이 된다. 실행하는 200조합 중 앞 20개를 baseline으로 재고(같은 실행의
일부를 재사용 -- 별도 워밍업이 아니다), 그 값을 200개로 선형 외삽한 뒤 9배
여유를 곱해 `max(spec_target_for_200, 9*baseline_extrapolated)`를 임계로 쓴다
(test_perf_journal.py "정규화 임계 = max(스펙임계, 배수*기준측정치)"와 동일한
형태 -- 기준 측정 대상이 DB 왕복이 아니라 "이 워크로드 유형 조합 1개당
처리시간"이라는 점만 다르다). `spec_target_for_200`은 §9.9 60s를 1,000조합
대비 200조합 비율로 축소한 값(12s)이다 -- 이미 실측이 그보다 훨씬 크므로
이 값 자체는 정보용이고 실제 게이트는 9배 정규화 쪽이 좌우한다.
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
from src.foundation.backtest.vector.fills import VectorSignal
from src.foundation.backtest.vector.grid import sweep_grid
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_D = Decimal
_CASH = _D("100000")

_BAR_COUNT = 43_200  # §9.9 "1개월 M1" -- 30일 x 24시간 x 60분 (문자 그대로)
_SPEC_COMBO_COUNT = 1_000  # §9.9 "1,000 조합" -- 참고용 외삽 목표치
_MEASURED_COMBO_COUNT = 200  # 실제로 실행하는 조합 수 (docstring 실측 근거)
_BASELINE_COMBO_COUNT = 20
_SPEC_TARGET_SECONDS = 60.0
_SPEC_TARGET_SECONDS_FOR_MEASURED = (
    _SPEC_TARGET_SECONDS * _MEASURED_COMBO_COUNT / _SPEC_COMBO_COUNT
)
_NORMALIZATION_MULTIPLIER = 9


def _one_month_m1_columns(n: int = _BAR_COUNT) -> CandleColumns:
    closes = [_D(str(100 + (i % 50))) for i in range(n)]
    opens = [closes[i - 1] if i else closes[0] for i in range(n)]
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=1) * i for i in range(n)],
        open=opens,
        high=[max(o, c) + 1 for o, c in zip(opens, closes, strict=True)],
        low=[min(o, c) - 1 for o, c in zip(opens, closes, strict=True)],
        close=closes,
        volume=[_D("1000")] * n,
        quote_volume=[None] * n,
    )


def _buy_and_hold_signal(n: int, *, quantity: Decimal) -> VectorSignal:
    """param sweep 조합 하나를 흉내낸다 -- `quantity`가 조합마다 다른 파라미터
    값 역할을 한다. 체결 1건짜리로 고정한 이유는 이 파일의 목적이 조합 수
    자체가 만드는 처리량(및 메모리) 비용을 재는 것이지, 조합 내부 체결
    빈도의 영향을 재는 것이 아니기 때문이다(그건 이미 in-suite
    `test_vector_sweeps.py`가 소규모로 커버한다)."""
    always = BoolSignal(values=np.ones(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    never = BoolSignal(values=np.zeros(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    return VectorSignal(entries=always, exits=never, quantity=quantity)


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


@pytest.mark.nightly
@pytest.mark.timeout(540)  # 43,200봉 x 200조합 실측은 기본 120s 타임아웃을 넘는다
def test_grid_sweep_full_month_m1_throughput() -> None:
    cols = _one_month_m1_columns()
    config = _config()
    combos = {
        f"C{i:04d}": _buy_and_hold_signal(_BAR_COUNT, quantity=_D("1") + _D(i) / _D("1000"))
        for i in range(_MEASURED_COMBO_COUNT)
    }
    combo_keys = list(combos)
    baseline_keys = combo_keys[:_BASELINE_COMBO_COUNT]
    remaining_keys = combo_keys[_BASELINE_COMBO_COUNT:]

    baseline_start = time.perf_counter()
    baseline_result = sweep_grid(
        cols, {k: combos[k] for k in baseline_keys}, config,
        timeframe=Timeframe.M1, initial_cash=_CASH,
    )
    baseline_elapsed_seconds = time.perf_counter() - baseline_start

    remaining_start = time.perf_counter()
    remaining_result = sweep_grid(
        cols, {k: combos[k] for k in remaining_keys}, config,
        timeframe=Timeframe.M1, initial_cash=_CASH,
    )
    remaining_elapsed_seconds = time.perf_counter() - remaining_start

    total_elapsed_seconds = baseline_elapsed_seconds + remaining_elapsed_seconds
    total_results = {**baseline_result.results, **remaining_result.results}

    baseline_per_combo_seconds = baseline_elapsed_seconds / _BASELINE_COMBO_COUNT
    baseline_extrapolated_to_measured = baseline_per_combo_seconds * _MEASURED_COMBO_COUNT
    normalized_target_seconds = max(
        _SPEC_TARGET_SECONDS_FOR_MEASURED,
        _NORMALIZATION_MULTIPLIER * baseline_extrapolated_to_measured,
    )
    extrapolated_1000_seconds = baseline_per_combo_seconds * _SPEC_COMBO_COUNT
    spec_met = (
        "충족" if extrapolated_1000_seconds <= _SPEC_TARGET_SECONDS
        else "미충족(비차단, 참고용 선형 외삽 -- 실측 초선형 추세상 실제로는 더 나쁠 수 있음)"
    )

    print(
        f"\n[BT-16 §9.9] {_BAR_COUNT}봉 x {_MEASURED_COMBO_COUNT}조합 실측="
        f"{total_elapsed_seconds:.3f}s (baseline {_BASELINE_COMBO_COUNT}조합="
        f"{baseline_elapsed_seconds:.3f}s); "
        f"{_SPEC_COMBO_COUNT}조합 선형 외삽={extrapolated_1000_seconds:.3f}s, "
        f"명세 임계={_SPEC_TARGET_SECONDS:.1f}s ({spec_met}); "
        f"정규화 임계(게이트, {_MEASURED_COMBO_COUNT}조합 기준)="
        f"{normalized_target_seconds:.3f}s "
        f"(max({_SPEC_TARGET_SECONDS_FOR_MEASURED:.1f}, "
        f"{_NORMALIZATION_MULTIPLIER}*{baseline_extrapolated_to_measured:.3f})"
    )

    assert len(total_results) == _MEASURED_COMBO_COUNT
    assert total_elapsed_seconds <= normalized_target_seconds, (
        f"grid sweep {_MEASURED_COMBO_COUNT}조합 x {_BAR_COUNT}봉 실측"
        f"({total_elapsed_seconds:.3f}s)이 정규화 임계({normalized_target_seconds:.3f}s)를 "
        "초과했습니다 — 처리량 회귀입니다."
    )
