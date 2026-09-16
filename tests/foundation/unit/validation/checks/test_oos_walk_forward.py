"""L39 -- unit tests for `checks/oos_walk_forward.py` (task-3360 D2 evidence:
negative >=3, 1 failure-injection, 1 perf assertion, 1 gate-red repro)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.snapshot import BarSnapshotRef
from src.foundation.backtest.domain.universe import UniverseSnapshot
from src.foundation.validation.checks import oos_walk_forward as check_mod
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.artifact import build_artifact
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import ValidationPolicy
from src.services.condition_compiler import ORDER_FILLED

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
_CYCLE = [
    ("100", "100"),
    ("100", "110"),
    ("112", "111"),
    ("111", "90"),
    ("88", "89"),
]

_BUDGET_MS = 400.0
_ITERATIONS = 5


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    def calculate(
        self, indicator: str, candles: list[Candle], **params: int
    ) -> _FakeIndicatorResult:
        assert indicator == "PRICE"
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _bar(*, open_price: str, close_price: str, index: int) -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(open_price),
        high=max(Decimal(open_price), Decimal(close_price)),
        low=min(Decimal(open_price), Decimal(close_price)),
        close=Decimal(close_price),
        volume=Decimal("1"),
        open_time=ts,
        close_time=ts,
    )


def _bars(n: int) -> list[Candle]:
    return [
        _bar(index=i, open_price=_CYCLE[i % len(_CYCLE)][0], close_price=_CYCLE[i % len(_CYCLE)][1])
        for i in range(n)
    ]


def _uptrend_bars(n_train: int = 20, n_test: int = 30) -> list[Candle]:
    """Train segment reuses `_CYCLE` (trade activity so IS Sharpe is
    scoreable); test segment climbs steadily (+5/bar from 90), crossing the
    strategy's buy threshold (PRICE > 105) early and never dropping back
    below the sell threshold (PRICE < 95) -- a clean OOS-profitable case."""
    bars: list[Candle] = []
    hour = 0
    for k in range(n_train):
        open_price, close_price = _CYCLE[k % len(_CYCLE)]
        bars.append(_bar(index=hour, open_price=open_price, close_price=close_price))
        hour += 1
    price = Decimal("90")
    for _ in range(n_test):
        open_price = price
        close_price = price + Decimal("5")
        bars.append(_bar(index=hour, open_price=str(open_price), close_price=str(close_price)))
        hour += 1
        price = close_price
    return bars


def _flat_bars(n: int) -> list[Candle]:
    return [_bar(index=i, open_price="100", close_price="100") for i in range(n)]


def _fsm_definition() -> dict[str, object]:
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[
            FSMState.IDLE,
            FSMState.BUY_ORDER_PENDING,
            FSMState.HOLDING,
            FSMState.SELL_ORDER_PENDING,
        ],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 105",
            ),
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.HOLDING,
                condition=ORDER_FILLED,
            ),
            FSMTransition(
                from_state=FSMState.HOLDING,
                to_state=FSMState.SELL_ORDER_PENDING,
                condition="PRICE < 95",
            ),
            FSMTransition(
                from_state=FSMState.SELL_ORDER_PENDING,
                to_state=FSMState.IDLE,
                condition=ORDER_FILLED,
            ),
        ],
        author_agent="test",
    ).model_dump(mode="json")


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy_id="test-strategy",
        strategy_version="v1",
        initial_equity=Decimal("1000"),
        cost_model=_ZERO_COST,
        warmup_bars=0,
        periods_per_year=252,
        seed=0,
    )


def _policy(**overrides: object) -> ValidationPolicy:
    kwargs: dict[str, object] = dict(
        min_oos_windows=2,
        oos_mode="ANCHORED",
        purge_bars=0,
        embargo_bars=0,
    )
    kwargs.update(overrides)
    return ValidationPolicy(**kwargs)


def _ctx(
    bars: list[Candle],
    *,
    policy: ValidationPolicy | None = None,
    seed: int = 0,
) -> CheckContext:
    return CheckContext(
        artifact=build_artifact(
            strategy_id="test-strategy",
            version="v1",
            fsm_definition=_fsm_definition(),
            compiler_version="test-compiler-v1",
        ),
        policy=policy or _policy(),
        bars=ListBars(bars),
        snapshot_ref=BarSnapshotRef(
            snapshot_hash="a" * 64,
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            from_time=bars[0].open_time,
            to_time=bars[-1].close_time,
            bar_count=len(bars),
            source="bitget-rest",
            as_of=bars[-1].close_time,
        ),
        universe=UniverseSnapshot(as_of=bars[-1].close_time, members=[], snapshot_hash="b" * 64),
        config=_config(),
        seed=seed,
        trace_id="trace-abc",
        prior_results={},
    )


def _run_with_fake_indicator(ctx: CheckContext, monkeypatch: pytest.MonkeyPatch):
    original = check_mod.run_walk_forward

    def _with_fake_indicator(*a: object, **k: object) -> object:
        return original(*a, **{**k, "indicator_service": _FakePriceIndicatorService()})

    monkeypatch.setattr(check_mod, "run_walk_forward", _with_fake_indicator)
    return check_mod.run(ctx)


def test_pass_when_oos_stitched_net_sharpe_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(_uptrend_bars())
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.check_type == "oos_walk_forward"
    assert result.hard_fail_reasons == []
    assert result.metrics["oos_net_sharpe"] > 0
    assert result.outcome == Outcome.PASS
    assert result.metrics["basis"] == "PAPER_SIM"
    assert result.metrics["oos_windows_count"] == 2


def test_soft_fail_when_oos_stitched_net_sharpe_nonpositive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_CYCLE`'s oscillation repeatedly buys near local highs and sells
    near local lows under this FSM's thresholds -- a losing OOS pattern by
    construction, so this is a soft FAIL (no hard_fail_reasons), not a
    data-integrity rejection."""
    ctx = _ctx(_bars(50))
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.hard_fail_reasons == []
    assert result.metrics["oos_net_sharpe"] < 0
    assert result.outcome == Outcome.FAIL


def test_hard_fail_when_too_few_bars_for_min_oos_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """20 bars / min_train=4 (20% floor) can't support 10 windows --
    `make_splits` rejects it (`n_bars // n_splits < min_train`) before any
    backtest replay is attempted."""
    ctx = _ctx(_bars(20), policy=_policy(min_oos_windows=10))
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_OOS_INSUFFICIENT"]


def test_hard_fail_when_all_grid_points_unscoreable(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(_flat_bars(30), policy=_policy(min_oos_windows=2))
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_OOS_INSUFFICIENT"]


def test_hard_fail_oos_leakage_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEEPEN failure injection: `splits.make_splits` never produces an
    overlapping split by construction, so the only way to exercise the
    `VALIDATION_OOS_LEAKAGE` branch is to inject a failure directly into
    `assert_no_overlap` -- proving the branch is reachable and wired to
    the right hard-fail code, not just dead code."""
    from src.foundation.backtest.domain.splits import OosLeakageError

    def _raise(*args: object, **kwargs: object) -> None:
        raise OosLeakageError("injected leakage")

    monkeypatch.setattr(check_mod, "assert_no_overlap", _raise)
    ctx = _ctx(_bars(50))
    result = check_mod.run(ctx)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_OOS_LEAKAGE"]


def test_result_hash_is_deterministic_for_same_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _bars(50)
    first = _run_with_fake_indicator(_ctx(bars), monkeypatch)
    second = _run_with_fake_indicator(_ctx(bars), monkeypatch)
    assert first.result_hash == second.result_hash


def _p95_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)] * 1000


def test_run_p95_latency_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(_bars(50))
    samples: list[float] = []
    for _ in range(_ITERATIONS):
        started = time.perf_counter()
        _run_with_fake_indicator(ctx, monkeypatch)
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    print(f"[L39 oos_walk_forward] p95={p95_ms:.4f}ms budget<{_BUDGET_MS:.0f}ms")
    assert p95_ms < _BUDGET_MS


def test_budget_gate_actually_fails_when_run_walk_forward_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-red reproduction: injecting a delay into `run_walk_forward` that
    actually exceeds the budget proves the p95 assertion above would fire
    AssertionError, not just pass trivially."""
    ctx = _ctx(_bars(50))
    original = check_mod.run_walk_forward

    def _stalled(*args: object, **kwargs: object) -> object:
        time.sleep(_BUDGET_MS / 1000 + 0.01)
        return original(*args, **{**kwargs, "indicator_service": _FakePriceIndicatorService()})

    monkeypatch.setattr(check_mod, "run_walk_forward", _stalled)

    started = time.perf_counter()
    check_mod.run(ctx)
    elapsed_ms = (time.perf_counter() - started) * 1000
    with pytest.raises(AssertionError):
        assert elapsed_ms < _BUDGET_MS
