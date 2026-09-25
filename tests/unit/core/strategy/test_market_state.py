"""L08 -- src/core/strategy/market_state.py DoD (task-2456, DEEPEN task-3209)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.strategy.market_state import (
    MarketState,
    MarketStateIntegrityError,
    assert_no_future,
)

_SRC = Path("src/core/strategy")


def test_market_state_fields_match_spec() -> None:
    # DoD (a): drop a field and this fails.
    assert set(MarketState.model_fields) == {
        "schema_version",
        "as_of",
        "values",
        "bar_close_time",
    }
    assert MarketState.model_fields["schema_version"].default == "ms-v1"


def test_assert_no_future_rejects_bar_close_time_one_second_ahead() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = MarketState(
        as_of=as_of,
        values={},
        bar_close_time={"1h": as_of + timedelta(seconds=1)},
    )
    with pytest.raises(Exception, match="INTEGRITY_FUTURE_DATA"):
        assert_no_future(state)


def test_assert_no_future_allows_bar_close_time_equal_to_as_of() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = MarketState(as_of=as_of, values={}, bar_close_time={"1h": as_of})
    assert_no_future(state)  # must not raise


@pytest.mark.parametrize("module", ["market_state.py", "state_memory.py"])
def test_no_indicator_key_regex_defined_in_module(module: str) -> None:
    # DoD (d): the key grammar has exactly one owner (indicator_key.py).
    source = (_SRC / module).read_text(encoding="utf-8")
    assert "import re" not in source
    assert "re.compile" not in source
    assert not re.search(r"_KEY_RE\s*=", source)


def test_market_state_rejects_malformed_indicator_key() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        MarketState(as_of=as_of, values={"RSI@": Decimal("1")}, bar_close_time={})


def test_market_state_rejects_naive_as_of() -> None:
    with pytest.raises(ValidationError):
        MarketState(as_of=datetime(2026, 1, 1), values={}, bar_close_time={})


def test_market_state_rejects_naive_bar_close_time() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        MarketState(as_of=as_of, values={}, bar_close_time={"1h": datetime(2026, 1, 1)})


def test_market_state_rejects_float_values() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        MarketState(as_of=as_of, values={"RSI_timeperiod14": 55.0}, bar_close_time={})


def test_from_flat_builds_single_timeframe_state() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = MarketState.from_flat({"RSI_timeperiod14": 55.0}, as_of=as_of, tf="1h")
    assert state.values == {"RSI_timeperiod14": Decimal("55.0")}
    assert state.bar_close_time == {"1h": as_of}


# -- D2 실패 주입 --------------------------------------------------------------


class _FlakyValuesDict(dict[str, Decimal]):
    """상류 지표 배치(예: TA-Lib 어댑터)가 키별로 값을 지연 스트리밍하다가
    중간에 실패하는 상황을 흉내 낸다."""

    def items(self):
        for i, kv in enumerate(dict.items(self)):
            if i == 1:
                raise RuntimeError("indicator batch computation aborted mid-stream")
            yield kv


def test_construction_propagates_upstream_indicator_stream_failure() -> None:
    """`values` 필드 검증기는 입력을 `.items()`로 순회한다 -- 상류 지표
    계산이 두 번째 키를 만들다가 죽으면, 그 예외가 삼켜지고 부분적으로만
    채워진 `values`를 가진 `MarketState`가 만들어지는 게 아니라(look-ahead
    무결성 검증 자체가 훼손된 채로 통과하는 것), 생성자 호출 지점까지
    fail-closed로 전파돼야 한다."""
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    flaky_values = _FlakyValuesDict(
        {
            "RSI_timeperiod14@1h": Decimal("50"),
            "MACD_slowperiod26@1h": Decimal("1"),
            "SMA_timeperiod20@1h": Decimal("2"),
        }
    )

    with pytest.raises(RuntimeError, match="aborted mid-stream"):
        MarketState(as_of=as_of, values=flaky_values, bar_close_time={})


# -- D2 성능 단언 --------------------------------------------------------------


@pytest.mark.perf
def test_market_state_construction_p95_latency_within_budget_for_many_keys() -> None:
    """ADR-2026-09-09-C Decision 1 성능 예산 축 차용: 매 틱 조립되는
    `MarketState` 생성(키 1,000개 각각 `parse_key` 검증 포함)이 가장 빠듯한
    축인 사전거래 게이트(p99 5ms)에 준하는 CI 여유 10배(50ms) 예산 안에
    든다."""
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = {f"RSI_timeperiod{i}@1h": Decimal(i) for i in range(1_000)}

    samples: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        MarketState(as_of=as_of, values=values, bar_close_time={"1h": as_of})
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95_seconds = samples[int(len(samples) * 0.95)]
    assert p95_seconds < 0.05, f"p95={p95_seconds * 1000:.2f}ms exceeds 50ms budget"


# -- D2 게이트 적색 재현 -------------------------------------------------------


def test_gate_red_repro_single_timeframe_check_misses_future_violation() -> None:
    """게이트 적색 재현: `assert_no_future`가 `bar_close_time`의 모든 tf를
    순회하는 대신 고정된 키(`"1h"`) 하나만 검사하도록 축약된 회귀본을
    재현한다. 그 회귀본은 다른 tf 아래 숨어 있는 미래 위반(I1)을 놓치고
    조용히 통과시킨다 -- 실제 구현은 `bar_close_time.items()` 전체를 순회해
    fail-closed로 잡아낸다."""
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    future = as_of + timedelta(days=1)

    def _regressed_assert_no_future_single_tf(state: MarketState) -> None:
        close_time = state.bar_close_time.get("1h")
        if close_time is not None and close_time > state.as_of:
            raise MarketStateIntegrityError("INTEGRITY_FUTURE_DATA")

    state = MarketState(as_of=as_of, values={}, bar_close_time={"4h": future})

    # 적색: "1h"만 검사하는 회귀본은 "4h" 아래 숨은 미래 위반을 놓친다.
    _regressed_assert_no_future_single_tf(state)  # must not raise

    # 녹색: 실제 구현은 모든 tf를 순회해 잡아낸다.
    with pytest.raises(MarketStateIntegrityError, match="INTEGRITY_FUTURE_DATA"):
        assert_no_future(state)
