"""L14 -- src/services/execution_loop/market_state.py DoD (task-2520).

다중 타임프레임 조립, `@tf` 없는 키의 1m 승격, U10(진행 중 bar 제외),
부분 실패(필요 tf 통째로 없음) 시 예외 전파를 확인한다.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.check_code_language import count_file
from src.core.indicators.registry import IndicatorError
from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.services.execution_loop.market_state import (
    IndicatorKeyParseError,
    MarketStateAssemblyError,
    build_market_state,
    parse_indicator_key,
    required_timeframes,
)
from tests.support.perf_budget import PerfBudget

_BASE = datetime(2026, 1, 1, tzinfo=UTC)
_MARKET_STATE_PATH = (
    Path(__file__).resolve().parents[3] / "src/services/execution_loop/market_state.py"
)


def _candles(count: int, *, timeframe: str, step: timedelta, start: datetime) -> list[Candle]:
    out = []
    for i in range(count):
        close_time = start + step * (i + 1)
        price = Decimal(10 + i)
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe=timeframe,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("1"),
                open_time=close_time - step,
                close_time=close_time,
            )
        )
    return out


def _fsm(condition: str) -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="s1",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE, to_state=FSMState.BUY_ORDER_PENDING, condition=condition
            )
        ],
        author_agent="tester",
    )


def test_build_market_state_assembles_multiple_timeframes() -> None:
    # `@tf` 없는 키는 1m으로 승격, `@1h` 키는 명시된 tf 그대로.
    fsm = _fsm("SMA_timeperiod3 >= 1 AND SMA_timeperiod3@1h >= 1")
    candles_1m = _candles(5, timeframe="1m", step=timedelta(minutes=1), start=_BASE)
    candles_1h = _candles(5, timeframe="1h", step=timedelta(hours=1), start=_BASE)
    as_of = max(candles_1m[-1].close_time, candles_1h[-1].close_time)

    state = build_market_state(fsm, {"1m": candles_1m, "1h": candles_1h}, as_of=as_of)

    assert "SMA_timeperiod3" in state.values
    assert "SMA_timeperiod3@1h" in state.values
    assert state.bar_close_time["1m"] == candles_1m[-1].close_time
    assert state.bar_close_time["1h"] == candles_1h[-1].close_time
    assert state.as_of == as_of


def test_required_timeframes_defaults_missing_tf_suffix_to_1m() -> None:
    fsm = _fsm("SMA_timeperiod3 >= 1")
    assert required_timeframes(fsm) == {"1m": 3}


def test_build_market_state_excludes_unclosed_trailing_bar() -> None:
    # U10 -- 마지막 bar가 as_of 이후에 닫히면(진행 중) 지표 계산에서 제외한다.
    fsm = _fsm("SMA_timeperiod3@1h >= 1")
    closed = _candles(4, timeframe="1h", step=timedelta(hours=1), start=_BASE)
    as_of = closed[-1].close_time
    in_progress = Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal("99"),
        high=Decimal("99"),
        low=Decimal("99"),
        close=Decimal("99"),
        volume=Decimal("1"),
        open_time=as_of,
        close_time=as_of + timedelta(hours=1),
    )

    state = build_market_state(fsm, {"1h": [*closed, in_progress]}, as_of=as_of)

    assert state.bar_close_time["1h"] == closed[-1].close_time  # not the in-progress bar
    assert "SMA_timeperiod3@1h" in state.values


def test_build_market_state_skips_key_with_insufficient_warmup_without_raising() -> None:
    # tf는 candles_by_tf에 있지만 warm-up bar가 모자라면 조용히 빠진다
    # (StrategyEngine이 IndicatorDataMissingError로 판단을 보류한다).
    fsm = _fsm("SMA_timeperiod3 >= 1")
    candles = _candles(1, timeframe="1m", step=timedelta(minutes=1), start=_BASE)

    state = build_market_state(fsm, {"1m": candles}, as_of=candles[-1].close_time)

    assert state.values == {}


def test_build_market_state_raises_when_required_timeframe_entirely_missing() -> None:
    # 부분 실패(네트워크 분리로 특정 tf 갱신 실패)는 부분 시장상태로
    # 판단하지 않고 예외로 틱 전체를 폐기한다(R-32 §5 market_state_partial).
    fsm = _fsm("SMA_timeperiod3 >= 1 AND SMA_timeperiod3@1h >= 1")
    candles_1m = _candles(5, timeframe="1m", step=timedelta(minutes=1), start=_BASE)

    with pytest.raises(MarketStateAssemblyError, match="1h"):
        build_market_state(fsm, {"1m": candles_1m}, as_of=candles_1m[-1].close_time)


def test_parse_indicator_key_rejects_malformed_key() -> None:
    # A key shape ConditionCompiler never produces must not be silently accepted.
    with pytest.raises(IndicatorKeyParseError):
        parse_indicator_key("not a valid key !!")


def test_required_timeframes_raises_for_unregistered_indicator() -> None:
    # An unregistered indicator name fails closed (IndicatorError) at lookback sizing.
    fsm = _fsm("FAKEIND_timeperiod3 >= 1")

    with pytest.raises(IndicatorError):
        required_timeframes(fsm)


def test_build_market_state_rejects_naive_as_of() -> None:
    # A naive as_of can't be compared against aware candle.close_time -- must not pass silently.
    fsm = _fsm("SMA_timeperiod3 >= 1")
    candles = _candles(5, timeframe="1m", step=timedelta(minutes=1), start=_BASE)
    naive_as_of = candles[-1].close_time.replace(tzinfo=None)

    with pytest.raises(TypeError):
        build_market_state(fsm, {"1m": candles}, as_of=naive_as_of)


# -- D2 failure injection -----------------------------------------------------


def test_build_market_state_propagates_indicator_service_failure_mid_batch() -> None:
    """`build_market_state`'s only collaborator for numeric values is
    `IndicatorService.calculate`; inject a failure (e.g. a TA-Lib native
    crash) on one key of a multi-key, multi-timeframe batch and confirm the
    exception propagates instead of the loop silently moving on and
    returning a `values` dict that omits the failed key -- an omission here
    looks identical to `IndicatorDataMissingError`'s legitimate "not warmed
    up yet" case (see the module docstring), so a real engine failure must
    never be allowed to masquerade as one.
    """
    fsm = _fsm("SMA_timeperiod3 >= 1 AND RSI_timeperiod14@1h >= 1")
    candles_1m = _candles(5, timeframe="1m", step=timedelta(minutes=1), start=_BASE)
    candles_1h = _candles(20, timeframe="1h", step=timedelta(hours=1), start=_BASE)
    as_of = max(candles_1m[-1].close_time, candles_1h[-1].close_time)
    real_service = IndicatorService()

    class _PoisonedIndicatorService:
        def calculate(self, indicator: str, candles: list[Candle], **params: int) -> object:
            if indicator == "RSI":
                raise RuntimeError("simulated indicator engine crash")
            return real_service.calculate(indicator, candles, **params)

    with pytest.raises(RuntimeError, match="simulated indicator engine crash"):
        build_market_state(
            fsm,
            {"1m": candles_1m, "1h": candles_1h},
            as_of=as_of,
            indicator_service=_PoisonedIndicatorService(),
        )


# -- D2 numeric performance assertion -----------------------------------------


@pytest.mark.perf
def test_build_market_state_throughput_budget(perf_budget: object) -> None:
    """`run_execution_tick` (tick.py) calls `build_market_state` once per
    execution per tick; 300 calls over a realistic 2-timeframe/2-key
    strategy must stay well under a 2s budget to rule out a pathological
    per-tick regression (e.g. re-parsing keys or re-instantiating the
    indicator service per call) creeping into the hot path.
    """
    fsm = _fsm("SMA_timeperiod3 >= 1 AND SMA_timeperiod3@1h >= 1")
    candles_1m = _candles(50, timeframe="1m", step=timedelta(minutes=1), start=_BASE)
    candles_1h = _candles(50, timeframe="1h", step=timedelta(hours=1), start=_BASE)
    as_of = max(candles_1m[-1].close_time, candles_1h[-1].close_time)
    service = IndicatorService()

    perf_budget.assert_within(
        lambda: build_market_state(
            fsm, {"1m": candles_1m, "1h": candles_1h}, as_of=as_of, indicator_service=service
        ),
        budget_ms=2000.0,
        batch=300,
    )


# -- 0 수렴 (CPU 측정 단일 호출이 0에 수렴함을 동적으로 검증) -----------------


@pytest.mark.perf
def test_build_market_state_cpu_time_converges_to_zero_for_small_input(
    perf_budget: PerfBudget,
) -> None:
    """0 수렴 검증: 단일 build_market_state 호출(2 타임프레임, 2 키, 50 바)의
    CPU 시간이 1ms 미만이어야 한다.

    Windows의 time.process_time()은 약 15.6ms 양자화 오차가 있으므로,
    batch=32로 여러 호출을 묶어 호출당 오차를 ~0.5ms로 줄인다.
    PerfBudget.sample docstring 참고: batch=N이면 양자화 오차가 15.6ms/N로 줄어든다.
    batch=32이면 오차 ~0.5ms이므로, 실제 CPU 시간이 1.0ms 미만이어야 합격한다.
    """
    fsm = _fsm("SMA_timeperiod3 >= 1 AND SMA_timeperiod3@1h >= 1")
    candles_1m = _candles(50, timeframe="1m", step=timedelta(minutes=1), start=_BASE)
    candles_1h = _candles(50, timeframe="1h", step=timedelta(hours=1), start=_BASE)
    as_of = max(candles_1m[-1].close_time, candles_1h[-1].close_time)
    service = IndicatorService()

    # batch=32: 양자화 오차를 호출당 ~0.5ms로 줄임 (15.6ms / 32 ≈ 0.5ms)
    sample = perf_budget.sample(
        lambda: build_market_state(
            fsm, {"1m": candles_1m, "1h": candles_1h}, as_of=as_of, indicator_service=service
        ),
        batch=32,
    )
    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        "작은 입력은 사실상 0ms에 수렴해야 함 (budget<1ms)"
    )


# --- gate-red reproduction (D2) — 10x 지연 monkeypatch ----------------------


@pytest.mark.perf
def test_build_market_state_perf_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 예산 단언이 실제로
    지연을 포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해
    1회 호출당 예산(2000ms)을 초과하는 CPU 시간을 가장하면
    `assert_within`가 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    budget_ms = 2000.0

    _fake_time = [0.0]

    def _fake_process_time() -> float:
        _fake_time[0] += 10_000.0  # 10 000ms per call >> 2000ms budget
        return _fake_time[0]

    monkeypatch.setattr(time, "process_time", _fake_process_time)

    fsm = _fsm("SMA_timeperiod3 >= 1 AND SMA_timeperiod3@1h >= 1")
    candles_1m = _candles(50, timeframe="1m", step=timedelta(minutes=1), start=_BASE)
    candles_1h = _candles(50, timeframe="1h", step=timedelta(hours=1), start=_BASE)
    as_of = max(candles_1m[-1].close_time, candles_1h[-1].close_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: build_market_state(
                fsm,
                {"1m": candles_1m, "1h": candles_1h},
                as_of=as_of,
                indicator_service=IndicatorService(),
            ),
            budget_ms=budget_ms,
            batch=300,
            label="build_market_state delay-injected",
        )


# -- D2 gate-red reproduction --------------------------------------------------


def test_l14_code_language_gate_flags_korean_comment_regression(tmp_path: Path) -> None:
    """ADR-2026-09-07-A requires English-only comments/docstrings under
    `src/` (enforced by `scripts/check_code_language.py`, wired into CI).
    Prove the gate's own counter fires red for a synthetic Hangul-comment
    regression, and confirm this leaf's actual source is green (0) --
    using a temp file so the test doesn't require the regression to exist
    in the tree.
    """
    poisoned = tmp_path / "poisoned.py"
    poisoned.write_text(
        "def f() -> int:\n"
        "    # simulated regression comment written in Korean: 이것은 한글 주석\n"
        "    return 1\n",
        encoding="utf-8",
    )
    assert count_file(poisoned) == 1
    assert count_file(_MARKET_STATE_PATH) == 0
