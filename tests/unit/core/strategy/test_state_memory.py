"""L08 -- src/core/strategy/state_memory.py DoD (task-2456, DEEPEN task-3209)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.strategy.indicator_key import IndicatorKeyError
from src.core.strategy.market_state import MarketState
from src.core.strategy.state_memory import StrategyStateMemory, advance

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(hours=1)
_T2 = _T1 + timedelta(hours=1)


def test_strategy_state_memory_fields_match_spec() -> None:
    # DoD (a): drop a field and this fails.
    assert set(StrategyStateMemory.model_fields) == {
        "schema_version",
        "execution_id",
        "state_version",
        "last_bar_time",
        "prev_values",
    }
    assert StrategyStateMemory.model_fields["schema_version"].default == "ssm-v1"


def test_advance_does_not_mutate_input_and_bumps_state_version() -> None:
    original = StrategyStateMemory(
        execution_id=1,
        state_version=0,
        last_bar_time={"1h": _T0},
        prev_values={"RSI_timeperiod14@1h": Decimal("50")},
    )
    market_state = MarketState(
        as_of=_T1,
        values={"RSI_timeperiod14@1h": Decimal("55")},
        bar_close_time={"1h": _T1},
    )

    result = advance(original, market_state, bar_times={"1h": _T0})

    # Original is untouched -- all three fields checked (DoD b).
    assert original.prev_values == {"RSI_timeperiod14@1h": Decimal("50")}
    assert original.last_bar_time == {"1h": _T0}
    assert original.state_version == 0

    assert result.state_version == original.state_version + 1


def test_advance_carries_prev_values_when_bar_times_matches_last_bar_time() -> None:
    original = StrategyStateMemory(
        execution_id=1,
        state_version=0,
        last_bar_time={"1h": _T0},
        prev_values={"RSI_timeperiod14@1h": Decimal("50")},
    )
    market_state = MarketState(
        as_of=_T1,
        values={"RSI_timeperiod14@1h": Decimal("55")},
        bar_close_time={"1h": _T1},
    )

    result = advance(original, market_state, bar_times={"1h": _T0})

    assert result.prev_values == {"RSI_timeperiod14@1h": Decimal("55")}
    assert result.last_bar_time == {"1h": _T1}


def test_advance_drops_prev_values_for_a_skipped_timeframe() -> None:
    # memory's recorded last bar for "1h" is _T0, but the caller asserts the
    # immediately preceding bar was _T1 (one bar later than what's on record) --
    # a bar was skipped between the recorded state and this tick.
    original = StrategyStateMemory(
        execution_id=1,
        state_version=0,
        last_bar_time={"1h": _T0},
        prev_values={"RSI_timeperiod14@1h": Decimal("50")},
    )
    market_state = MarketState(
        as_of=_T2,
        values={"RSI_timeperiod14@1h": Decimal("60")},
        bar_close_time={"1h": _T2},
    )

    result = advance(original, market_state, bar_times={"1h": _T1})

    assert "RSI_timeperiod14@1h" not in result.prev_values
    # last_bar_time still records the newest observed bar for next tick's check.
    assert result.last_bar_time == {"1h": _T2}


def test_state_memory_rejects_naive_last_bar_time() -> None:
    with pytest.raises(ValidationError):
        StrategyStateMemory(
            execution_id=1,
            state_version=0,
            last_bar_time={"1h": datetime(2026, 1, 1)},
            prev_values={},
        )


def test_state_memory_rejects_float_prev_values() -> None:
    with pytest.raises(ValidationError):
        StrategyStateMemory(
            execution_id=1,
            state_version=0,
            last_bar_time={},
            prev_values={"RSI_timeperiod14@1h": 50.0},
        )


def test_state_memory_rejects_invalid_schema_version() -> None:
    # DoD (a) 대칭: schema_version은 "ssm-v1" 고정 리터럴이다 -- 다른 값이 붙은
    # 채로 L13이 복원하면 마이그레이션 없는 스키마 드리프트를 조용히 통과시킨다.
    with pytest.raises(ValidationError):
        StrategyStateMemory(
            schema_version="ssm-v2",
            execution_id=1,
            state_version=0,
            last_bar_time={},
            prev_values={},
        )


def test_market_state_rejects_unsupported_timeframe_in_key() -> None:
    """`indicator_key.parse_key`에는 두 개의 개별 거부 분기가 있다 -- 문법이
    깨진 키(정규식 불일치)와, 문법은 맞지만 `ALLOWED_TIMEFRAMES`에 없는
    timeframe을 가리키는 키. 아래 실패 주입 테스트는 앞의 분기만 건드리므로,
    `MarketState`의 정상 생성 경로(우회 없이)가 뒤의 분기도 fail-closed로
    막는다는 것을 별도로 확인한다 -- "2h"는 문법은 유효하지만 L08이 아는
    timeframe 집합 밖이라 조용히 통과하면 안 된다."""
    with pytest.raises(ValidationError):
        MarketState(
            as_of=_T1,
            values={"RSI_timeperiod14@2h": Decimal("1")},
            bar_close_time={},
        )


# -- D2 실패 주입 --------------------------------------------------------------


def test_advance_propagates_unsupported_timeframe_from_bypassed_validation() -> None:
    """`test_advance_propagates_malformed_key_from_bypassed_validation`과 같은
    신뢰된 역직렬화 경로(`model_construct`)를 우회하지만, 이번엔 문법은 유효한
    키가 `ALLOWED_TIMEFRAMES` 밖의 timeframe을 가리키는 손상 케이스다 --
    `parse_key`의 서로 다른 거부 분기를 `advance()`가 둘 다 fail-closed로
    전파하는지 확인한다."""
    memory = StrategyStateMemory(execution_id=1, state_version=0, last_bar_time={}, prev_values={})
    corrupted_market_state = MarketState.model_construct(
        schema_version="ms-v1",
        as_of=_T1,
        values={"RSI_timeperiod14@2h": Decimal("1")},
        bar_close_time={},
    )

    with pytest.raises(IndicatorKeyError):
        advance(memory, corrupted_market_state, bar_times={})


def test_advance_propagates_malformed_key_from_bypassed_validation() -> None:
    """`model_construct`는 신뢰된 역직렬화 경로(예: L13이
    `strategy_execution_state`에서 복원할 때 필드 검증을 다시 돌리지 않는 빠른
    경로)다. 데이터 손상으로 그 경로에 문법이 깨진 indicator 키가 섞여
    들어오면(생성자 검증이 정상적으로 막는 케이스), `advance()`가 그 키를
    조용히 건너뛰거나 엉뚱한 타임프레임 버킷에 넣지 않고 `_timeframe_of`가
    유일한 진실 소스(`parse_key`)를 호출해 `IndicatorKeyError`를 fail-closed로
    전파해야 한다."""
    memory = StrategyStateMemory(execution_id=1, state_version=0, last_bar_time={}, prev_values={})
    corrupted_market_state = MarketState.model_construct(
        schema_version="ms-v1",
        as_of=_T1,
        values={"not a valid key@@": Decimal("1")},
        bar_close_time={},
    )

    with pytest.raises(IndicatorKeyError):
        advance(memory, corrupted_market_state, bar_times={})


# -- D2 성능 단언 --------------------------------------------------------------


@pytest.mark.perf
def test_advance_p95_latency_within_budget_for_many_keys() -> None:
    """ADR-2026-09-09-C Decision 1 성능 예산 축 차용: `advance()`는 매 틱마다
    실행되는 순수 상태 전이이므로 가장 빠듯한 축인 사전거래 게이트(p99 5ms)에
    준하는 부하(5개 tf x 100개 지표 키 = 500 키)에서도 CI 여유 10배(50ms)
    안에 든다."""
    timeframes = ["1m", "5m", "15m", "30m", "1h"]
    values: dict[str, Decimal] = {}
    bar_close_time: dict[str, datetime] = {}
    last_bar_time: dict[str, datetime] = {}
    prev_values: dict[str, Decimal] = {}
    for tf in timeframes:
        bar_close_time[tf] = _T1
        last_bar_time[tf] = _T0
        for i in range(100):
            key = f"RSI_timeperiod{i}@{tf}"
            values[key] = Decimal(i)
            prev_values[key] = Decimal(i)

    memory = StrategyStateMemory(
        execution_id=1,
        state_version=0,
        last_bar_time=last_bar_time,
        prev_values=prev_values,
    )
    market_state = MarketState(as_of=_T1, values=values, bar_close_time=bar_close_time)
    bar_times = dict(last_bar_time)

    samples: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        advance(memory, market_state, bar_times=bar_times)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95_seconds = samples[int(len(samples) * 0.95)]
    assert p95_seconds < 0.05, f"p95={p95_seconds * 1000:.2f}ms exceeds 50ms budget"


# -- D2 게이트 적색 재현 -------------------------------------------------------


def test_gate_red_repro_missing_continuity_check_leaks_stale_crossover_values() -> None:
    """게이트 적색 재현: `continuous = memory.last_bar_time.get(tf) ==
    bar_times.get(tf)` 가드를 지운 회귀본을 재현한다. 가드가 없으면 bar가
    하나 건너뛰어도 `prev_values`가 무조건 이월돼, 다음 tick의 crossover
    판정이 존재하지 않는 중간 bar를 사이에 두고 조용히 비교된다(I3 위반,
    look-ahead 은폐와 동형). 실제 구현은 continuity가 깨지면 fail-closed로
    해당 tf의 `prev_values`를 드롭한다."""
    original = StrategyStateMemory(
        execution_id=1,
        state_version=0,
        last_bar_time={"1h": _T0},
        prev_values={"RSI_timeperiod14@1h": Decimal("50")},
    )
    market_state = MarketState(
        as_of=_T2,
        values={"RSI_timeperiod14@1h": Decimal("999")},
        bar_close_time={"1h": _T2},
    )
    # 직전 bar가 _T1이라고 주장하지만 memory의 기록은 _T0 -- 그 사이 bar 하나가 건너뛰어졌다.
    bar_times = {"1h": _T1}

    def _regressed_prev_values_without_continuity_guard() -> dict[str, Decimal]:
        new_prev_values = dict(original.prev_values)
        for key in market_state.values:
            # 원본의 `continuous` 비교 없이 무조건 이월 -- 회귀.
            new_prev_values[key] = market_state.values[key]
        return new_prev_values

    # 적색: 가드가 없으면 건너뛴 bar를 사이에 두고도 prev_values가 이월된다.
    regressed = _regressed_prev_values_without_continuity_guard()
    assert regressed["RSI_timeperiod14@1h"] == Decimal("999")

    # 녹색: 실제 구현은 continuity가 깨졌으므로 해당 키를 드롭한다.
    real_result = advance(original, market_state, bar_times=bar_times)
    assert "RSI_timeperiod14@1h" not in real_result.prev_values
