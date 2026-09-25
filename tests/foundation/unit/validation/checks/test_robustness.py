"""L39 -- unit tests for `checks/robustness.py` (task-3360 D2 evidence:
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
from src.foundation.validation.checks import robustness as check_mod
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

_BUDGET_MS = 800.0
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


def _artifact(**overrides: object):
    artifact = build_artifact(
        strategy_id="test-strategy",
        version="v1",
        fsm_definition=_fsm_definition(),
        compiler_version="test-compiler-v1",
    )
    return artifact.model_copy(update=overrides) if overrides else artifact


def _lenient_policy(**overrides: object) -> ValidationPolicy:
    """`max_pbo`/`min_dsr`/`max_param_isolation` wide open so the PASS-path
    test isolates the wiring (grid/seed/registry checks + sweep/pbo/dsr
    plumbing), not this leaf's synthetic fixture's actual DSR/PBO values."""
    kwargs: dict[str, object] = dict(
        min_grid_points=4,
        max_pbo=Decimal("1"),
        min_dsr=Decimal("0"),
        max_param_isolation=Decimal("1"),
    )
    kwargs.update(overrides)
    return ValidationPolicy(**kwargs)


def _ctx(
    bars: list[Candle],
    *,
    artifact=None,
    policy: ValidationPolicy | None = None,
    seed: int = 0,
) -> CheckContext:
    return CheckContext(
        artifact=artifact or _artifact(),
        policy=policy or _lenient_policy(),
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
    original = check_mod.sweep

    def _with_fake_indicator(*a: object, **k: object) -> object:
        return original(*a, **{**k, "indicator_service": _FakePriceIndicatorService()})

    monkeypatch.setattr(check_mod, "sweep", _with_fake_indicator)
    return check_mod.run(ctx)


def test_pass_when_grid_reproducible_and_thresholds_lenient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(_bars(60))
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.check_type == "robustness"
    assert result.outcome == Outcome.PASS
    assert result.hard_fail_reasons == []
    assert result.metrics["fail_reasons"] == []
    assert result.metrics["grid_size"] == 4
    assert result.overfitting_version == "ofit-v1"


def test_soft_fail_when_dsr_under_strict_min(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(_bars(60), policy=_lenient_policy(min_dsr=Decimal("1")))
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.hard_fail_reasons == []
    assert "dsr_under_min" in result.metrics["fail_reasons"]
    assert result.outcome == Outcome.FAIL


def test_hard_fail_when_grid_smaller_than_policy_min(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(_bars(60), policy=_lenient_policy(min_grid_points=8))
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_NONREPRODUCIBLE_CONFIG"]


def test_hard_fail_when_seed_not_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(_bars(60), seed=1)
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_NONREPRODUCIBLE_CONFIG"]


def test_hard_fail_when_registry_version_drifted(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(_bars(60), artifact=_artifact(registry_version="wrong-hash"))
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_NONREPRODUCIBLE_CONFIG"]


def test_dsr_uncomputable_degrades_to_warning_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEEPEN failure injection: `deflated_sharpe` raising (e.g. a math
    domain error from a degenerate skew/kurtosis input) must not crash the
    check -- spec #3.5 "uncomputable collapses to None + warnings, never a
    0 substitute" requires a graceful `dsr=None` + warning, not a crash."""

    def _raise(*args: object, **kwargs: object) -> None:
        raise ValueError("math domain error")

    monkeypatch.setattr(check_mod, "deflated_sharpe", _raise)
    ctx = _ctx(_bars(60))
    result = _run_with_fake_indicator(ctx, monkeypatch)
    assert result.metrics["dsr"] is None
    assert any("dsr_uncomputable" in w for w in result.warnings)
    assert "dsr_under_min" not in result.metrics["fail_reasons"]


def test_result_hash_is_deterministic_for_same_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _bars(60)
    first = _run_with_fake_indicator(_ctx(bars), monkeypatch)
    second = _run_with_fake_indicator(_ctx(bars), monkeypatch)
    assert first.result_hash == second.result_hash


def _p95_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)] * 1000


@pytest.mark.perf
def test_run_p95_latency_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(_bars(60))
    samples: list[float] = []
    for _ in range(_ITERATIONS):
        started = time.perf_counter()
        _run_with_fake_indicator(ctx, monkeypatch)
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    print(f"[L39 robustness] p95={p95_ms:.4f}ms budget<{_BUDGET_MS:.0f}ms")
    assert p95_ms < _BUDGET_MS


@pytest.mark.perf
def test_budget_gate_actually_fails_when_sweep_stalls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate-red reproduction: injecting a delay into `sweep` that actually
    exceeds the budget proves the p95 assertion above would fire
    AssertionError, not just pass trivially."""
    ctx = _ctx(_bars(60))
    original = check_mod.sweep

    def _stalled(*args: object, **kwargs: object) -> object:
        time.sleep(_BUDGET_MS / 1000 + 0.01)
        return original(*args, **{**kwargs, "indicator_service": _FakePriceIndicatorService()})

    monkeypatch.setattr(check_mod, "sweep", _stalled)

    started = time.perf_counter()
    check_mod.run(ctx)
    elapsed_ms = (time.perf_counter() - started) * 1000
    with pytest.raises(AssertionError):
        assert elapsed_ms < _BUDGET_MS
