"""L38 -- unit test for `CheckContext`: the field set/order must match the
spec's `CheckContext(artifact, policy, bars, snapshot_ref, universe, config,
seed, trace_id, prior_results)` signature (§2 row 165 / §9 L38) exactly, and
the dataclass must be frozen (every other check-input type it bundles is
immutable too).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.snapshot import BarSnapshotRef
from src.foundation.backtest.domain.universe import UniverseSnapshot
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.artifact import build_artifact
from src.foundation.validation.domain.policy import ValidationPolicy

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ctx() -> CheckContext:
    return CheckContext(
        artifact=build_artifact(
            strategy_id="strat-1",
            version="v1",
            fsm_definition={"states": ["IDLE"], "transitions": []},
            compiler_version="cc-test-1",
        ),
        policy=ValidationPolicy(),
        bars=ListBars([]),
        snapshot_ref=BarSnapshotRef(
            snapshot_hash="a" * 64,
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            from_time=_T0,
            to_time=_T0,
            bar_count=0,
            source="bitget-rest",
            as_of=_T0,
        ),
        universe=UniverseSnapshot(as_of=_T0, members=[], snapshot_hash="b" * 64),
        config=BacktestConfig(
            strategy_id="strat-1",
            strategy_version="v1",
            initial_equity=Decimal("1000"),
            cost_model=CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5")),
            warmup_bars=0,
            periods_per_year=252,
        ),
        seed=0,
        trace_id="trace-1",
        prior_results={},
    )


def test_field_order_matches_spec_signature() -> None:
    assert [f.name for f in fields(CheckContext)] == [
        "artifact",
        "policy",
        "bars",
        "snapshot_ref",
        "universe",
        "config",
        "seed",
        "trace_id",
        "prior_results",
    ]


def test_context_is_frozen_rejects_mutation() -> None:
    ctx = _ctx()
    attr_name = (
        "seed"  # non-literal so this exercises runtime __setattr__, not a lint-visible field write
    )
    with pytest.raises(FrozenInstanceError):
        setattr(ctx, attr_name, 1)


def test_context_is_frozen_rejects_mutation_of_mutable_field() -> None:
    # prior_results is itself a mutable dict, but __setattr__ on the frozen
    # dataclass must still refuse to rebind the attribute to a new dict.
    ctx = _ctx()
    with pytest.raises(FrozenInstanceError):
        setattr(ctx, "prior_results", {})  # noqa: B010 -- exercises __setattr__, not a field write


def test_context_is_frozen_rejects_delattr() -> None:
    ctx = _ctx()
    with pytest.raises(FrozenInstanceError):
        delattr(ctx, "trace_id")


def test_context_rejects_missing_required_field() -> None:
    kwargs = _ctx().__dict__.copy()
    del kwargs["prior_results"]
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_unknown_field() -> None:
    kwargs = _ctx().__dict__.copy()
    kwargs["unknown_field"] = "nope"
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_construction_propagates_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Failure injection: if a dependency the context bundles together
    # (here UniverseSnapshot) blows up while assembling the bundle, the
    # exception must propagate -- CheckContext must not swallow it or
    # construct a partially-valid context (fail-closed per standard-105).
    def _boom(*_args: object, **_kwargs: object) -> UniverseSnapshot:
        raise RuntimeError("universe snapshot source unavailable")

    monkeypatch.setattr(UniverseSnapshot, "__init__", _boom)

    with pytest.raises(RuntimeError, match="universe snapshot source unavailable"):
        _ctx()
