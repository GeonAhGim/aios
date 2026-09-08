from __future__ import annotations

import pytest

from src.core.strategy.indicator_key import (
    ALLOWED_TIMEFRAMES,
    IndicatorKey,
    IndicatorKeyError,
    format_key,
    parse_key,
)
from src.foundation.market_data.contracts.v1_enums import Timeframe
from src.services.execution_loop.market_state import (
    IndicatorKeyParseError,
    parse_indicator_key,
)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (
            "RSI_timeperiod14@1h",
            IndicatorKey("RSI", {"timeperiod": 14}, output=None, timeframe="1h"),
        ),
        (
            "MACD_slowperiod26.signal",
            IndicatorKey("MACD", {"slowperiod": 26}, output="signal", timeframe=None),
        ),
        ("SMA_timeperiod20", IndicatorKey("SMA", {"timeperiod": 20}, output=None, timeframe=None)),
    ],
)
def test_parse_key_examples(key: str, expected: IndicatorKey) -> None:
    assert parse_key(key) == expected


@pytest.mark.parametrize(
    "key",
    ["RSI_timeperiod14@1h", "MACD_slowperiod26.signal", "SMA_timeperiod20", "RSI"],
)
def test_format_key_round_trips(key: str) -> None:
    assert format_key(parse_key(key)) == key


@pytest.mark.parametrize("key", ["RSI_timeperiod14@1y", "", "RSI@", "RSI_timeperiod14@"])
def test_parse_key_rejects_invalid_input(key: str) -> None:
    with pytest.raises(IndicatorKeyError):
        parse_key(key)


def test_allowed_timeframes_match_market_data_timeframe_enum() -> None:
    # I-05 계열 단일 출처 원칙: 새 tf 값을 여기서 따로 발명하지 않고, 실제
    # Timeframe 열거형과 값 집합이 정확히 같아야 한다(모듈 임포트는 하지
    # 않는다 — FROZEN_PAPER_ONLY 리프가 SCAFFOLD 존 의존을 지지 않도록).
    assert ALLOWED_TIMEFRAMES == {tf.value for tf in Timeframe}


def test_market_state_delegates_to_indicator_key() -> None:
    assert parse_indicator_key("RSI_timeperiod14") == ("RSI", {"timeperiod": 14})
    # 구 `_KEY_RE`(`^[A-Z]+(?:_[a-z]+\d+)*$`)는 `.output` 접미사를 받지 못해
    # 이 입력에서 IndicatorKeyParseError를 던졌다 — 이 assert가 통과한다는
    # 것 자체가 market_state.parse_indicator_key가 실제로 새 문법(단일 출처
    # indicator_key.parse_key)에 위임하고 있다는 배선 증거다. 이 교체를
    # 되돌리면 이 테스트가 즉시 실패한다.
    assert parse_indicator_key("MACD_slowperiod26.signal") == ("MACD", {"slowperiod": 26})
    with pytest.raises(IndicatorKeyParseError):
        parse_indicator_key("rsi_timeperiod14")
