from __future__ import annotations

import time

import pytest

from scripts.check_import_linter import ROOT as LINTER_ROOT
from scripts.check_import_linter import _eval_forbidden, _imports_of, parse_contracts
from src.core.strategy.condition_evaluator import extract_indicator_keys
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


# -- D2 실패 주입 --------------------------------------------------------------


def test_extract_indicator_keys_propagates_compiler_regression_malformed_key() -> None:
    """`condition_evaluator.extract_indicator_keys` delegates key validation to
    `indicator_key.parse_key` (the single grammar owner) precisely so a
    ConditionCompiler regression that emits a grammar-violating key (here: a
    lower-cased indicator name) surfaces immediately as `IndicatorKeyError`
    instead of being silently accepted and only failing later, opaquely, as a
    market_state lookup miss."""
    malformed_expression = "rsi_timeperiod14 > 30 AND MACD_slowperiod26.signal < 0"
    with pytest.raises(IndicatorKeyError):
        extract_indicator_keys(malformed_expression)


# -- D2 성능 단언 --------------------------------------------------------------


def test_parse_and_format_key_round_trip_throughput_budget() -> None:
    """`parse_key`/`format_key` run once per required indicator key on every
    execution-loop tick (`market_state.build_market_state`); 10,000 round
    trips must stay well under a 200ms budget to rule out a pathological
    (e.g. backtracking-regex) implementation creeping into this hot path."""
    key = "MACD_slowperiod26.signal@1h"
    start = time.perf_counter()
    for _ in range(10_000):
        format_key(parse_key(key))
    elapsed = time.perf_counter() - start
    assert elapsed < 0.2, f"10,000 round trips took {elapsed * 1000:.2f}ms, budget 200ms"


# -- D2 게이트 적색 재현 -------------------------------------------------------


def test_import_linter_core_no_io_catches_indicator_key_foundation_import_regression() -> None:
    """`indicator_key.py`'s `ALLOWED_TIMEFRAMES` docstring explains it is kept
    as a local constant rather than imported from
    `src.foundation.market_data.contracts.v1_enums.Timeframe`, specifically so
    this FROZEN_PAPER_ONLY leaf stays clear of `.importlinter`'s `core-no-io`
    forbidden contract (src.core may not import src.foundation). This proves
    that contract's real evaluator (`scripts/check_import_linter.py`) fires
    red for exactly that regression shape, and stays green for this module's
    real current imports — using a synthetic graph so the test doesn't
    require the regression to exist in the tree first."""
    contracts = parse_contracts(LINTER_ROOT / ".importlinter")
    core_no_io = next(c for c in contracts if c["id"] == "core-no-io")

    regressed_graph = {
        "src.core.strategy.indicator_key": {
            "src.foundation.market_data.contracts.v1_enums.Timeframe"
        }
    }
    hits = _eval_forbidden(regressed_graph, core_no_io)
    assert len(hits) == 1
    assert hits[0][0] == "src.core.strategy.indicator_key"

    real_imports = _imports_of(
        LINTER_ROOT / "src/core/strategy/indicator_key.py",
        "src.core.strategy.indicator_key",
        is_package=False,
    )
    assert _eval_forbidden({"src.core.strategy.indicator_key": real_imports}, core_no_io) == []
