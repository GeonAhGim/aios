from __future__ import annotations

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
from tests.conftest import PerfBudget


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


@pytest.mark.perf
def test_parse_key_single_call_cpu_converges_to_zero(
    perf_budget: PerfBudget,
) -> None:
    """Single `parse_key` call must consume ~0 CPU ms — a pathological
    backtracking-regex would register measurable CPU even for one call.
    100 samples, best-of-N minimum must stay below 1 ms (a wide floor for
    single-call overhead)."""
    key = "RSI_timeperiod14@1h"

    def one_call() -> None:
        parse_key(key)

    sample = perf_budget.best_of(one_call, n=100, batch=1)
    assert sample.cpu_ms < 1.0, (
        f"single-call CPU={sample.cpu_ms:.3f}ms exceeds 1ms floor — "
        f"wall={sample.wall_ms:.3f}ms load={perf_budget.load_percent()}"
    )


@pytest.mark.perf
def test_parse_and_format_key_round_trip_throughput_budget(
    perf_budget: PerfBudget,
) -> None:
    """`parse_key`/`format_key` run once per required indicator key on every
    execution-loop tick (`market_state.build_market_state`); 10,000 round
    trips must stay well under a 200ms budget to rule out a pathological
    (e.g. backtracking-regex) implementation creeping into this hot path."""
    key = "MACD_slowperiod26.signal@1h"

    def run_10k() -> None:
        for _ in range(10_000):
            format_key(parse_key(key))

    # batch=100으로 묶어 호출당 100회 → process_time 양자화 오차 최소화.
    # 예산 200ms, 단위 일치(ms). n=5 best-of-N으로 host 부하 변동 상쇄.
    perf_budget.assert_within(run_10k, budget_ms=200, batch=100, n=5)


# -- D2 게이트 적색 재현 -------------------------------------------------------


@pytest.mark.perf
def test_parse_key_budget_assertion_detects_delay_injection(
    perf_budget: PerfBudget,
) -> None:
    """Prove the budget assertion actually fires red: wrap `parse_key` in a
    function that burns CPU (busy-loop), then assert the measurement exceeds
    the budget. This is the gate-red reproduction — if the assertion logic
    is broken (e.g. always passes), this test will incorrectly green.
    Uses CPU-bound work because `time.process_time()` ignores sleep."""
    key = "RSI_timeperiod14@1h"
    injected_calls: list[int] = [0]

    def cpu_heavy_call() -> None:
        injected_calls[0] += 1
        # Busy-loop to burn ~5ms of real CPU time.
        # A tight loop of arithmetic is enough to register on process_time().
        total = 0
        for _ in range(5_000_000):
            total += 1
        _ = total  # prevent optimisation away
        parse_key(key)

    # Budget 1 ms — a 5ms busy-loop must exceed it.
    sample = perf_budget.best_of(cpu_heavy_call, n=3, batch=1)
    assert sample.cpu_ms >= 1.0, (
        f"delay-injection gate-red failed: cpu={sample.cpu_ms:.3f}ms "
        f"should be >= 1ms — assertion did not catch the injected CPU load"
    )


def test_parse_key_budget_assertion_catches_10x_violation() -> None:
    """Verify that `PerfBudget.assert_within` raises on budget violation.
    Monkey-patch `time.process_time` to return incrementing values that
    simulate a 10x budget overrun, proving the assertion fires correctly."""
    import time as _time

    original_process_time = _time.process_time
    call_counter: list[int] = [0]

    def fake_process_time() -> float:
        call_counter[0] += 1
        # Simulate each call consuming 5ms worth of CPU time
        return call_counter[0] * 0.005

    _time.process_time = fake_process_time
    try:
        key = "SMA_timeperiod20"

        def one_call() -> None:
            parse_key(key)

        # Budget 0.001 ms — fake clock makes each call ~5ms, must exceed.
        with pytest.raises(AssertionError):
            _pb = PerfBudget()
            _pb.assert_within(one_call, budget_ms=0.001, n=1, batch=1)
    finally:
        _time.process_time = original_process_time


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
