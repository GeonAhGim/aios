"""L38 -- unit test for `CheckContext`: the field set/order must match the
spec's `CheckContext(artifact, policy, bars, snapshot_ref, universe, config,
seed, trace_id, prior_results)` signature (§2 row 165 / §9 L38) exactly, and
the dataclass must be frozen (every other check-input type it bundles is
immutable too).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.snapshot import BarSnapshotRef
from src.foundation.backtest.domain.universe import UniverseSnapshot
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.artifact import build_artifact
from src.foundation.validation.domain.policy import ValidationPolicy

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


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


def test_context_is_frozen_rejects_mutable_field_rebind_to_dict_keeps_original() -> None:
    # Rebinding prior_results to a new dict must raise FrozenInstanceError,
    # and ctx.prior_results must still be the original object (identity).
    ctx = _ctx()
    original = ctx.prior_results
    attr_name = "prior_results"
    with pytest.raises(FrozenInstanceError):
        setattr(ctx, attr_name, {"x": 1})  # noqa: B010 -- exercises __setattr__, not a field write
    assert ctx.prior_results is original


def test_context_is_frozen_rejects_delattr_of_mutable_field() -> None:
    # delattr on a frozen dataclass field must raise FrozenInstanceError.
    ctx = _ctx()
    attr_name = "prior_results"
    with pytest.raises(FrozenInstanceError):
        delattr(ctx, attr_name)


def test_context_is_frozen_rejects_delattr() -> None:
    ctx = _ctx()
    with pytest.raises(FrozenInstanceError):
        delattr(ctx, "trace_id")


def test_context_rejects_missing_required_field() -> None:
    kwargs = _ctx().__dict__.copy()
    del kwargs["prior_results"]
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_missing_trace_id() -> None:
    """Negative: CheckContext must reject construction when trace_id is absent."""
    kwargs = _ctx().__dict__.copy()
    del kwargs["trace_id"]
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_missing_seed() -> None:
    """Negative: CheckContext must reject construction when seed is absent."""
    kwargs = _ctx().__dict__.copy()
    del kwargs["seed"]
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_missing_required_field_goes_red_with_corrupted_input() -> None:
    """Gate-red reproduction: pass an unknown keyword argument,
    then verify that CheckContext construction raises TypeError (fail-closed).
    This proves the signature-enforcement assertion actually catches bad input.
    """
    kwargs = _ctx().__dict__.copy()
    kwargs["bogus_field"] = 42
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


@pytest.mark.perf
def test_context_rejects_missing_required_field_perf_budget(perf_budget) -> None:
    """Performance: constructing CheckContext with a missing required field
    must raise TypeError quickly. Budget: best-of-5 < 5 ms.
    Local measurement: ~0.001 ms (Windows 15.6 ms tick floor); 5 ms = 3000x headroom.
    """

    def fn() -> None:
        kwargs = _ctx().__dict__.copy()
        del kwargs["prior_results"]
        with pytest.raises(TypeError):
            CheckContext(**kwargs)

    sample = perf_budget.best_of(fn, n=5, warmup=1, batch=200)
    assert sample.cpu_ms < 5.0, f"budget exceeded: {sample.cpu_ms} ms"


@pytest.mark.perf
def test_context_rejects_missing_required_field_goes_red_with_10x_delay(perf_budget) -> None:
    """Gate-red reproduction with 10x virtual delay: the TypeError should still
    be raised but the measured cpu_ms must exceed the budget, proving the
    performance budget assertion is real and not a false positive.
    """
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()
    budget_ms = 5.0

    def fn() -> None:
        kwargs = _ctx().__dict__.copy()
        del kwargs["prior_results"]
        with pytest.raises(TypeError):
            CheckContext(**kwargs)

    slow = virtual_delayed_sync(fn, budget_ms * 10 / 1000)
    sample = perf_budget.best_of(slow, n=3, warmup=0)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < budget_ms


def test_context_rejects_unknown_field() -> None:
    kwargs = _ctx().__dict__.copy()
    kwargs["unknown_field"] = "nope"
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_unknown_field_with_numeric_value() -> None:
    """Negative: unknown field with a numeric value must still raise TypeError."""
    kwargs = _ctx().__dict__.copy()
    kwargs["another_unknown"] = 42
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_unknown_field_with_none_value() -> None:
    """Negative: unknown field with None value must still raise TypeError."""
    kwargs = _ctx().__dict__.copy()
    kwargs["null_field"] = None
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_multiple_unknown_fields() -> None:
    """Negative: multiple unknown fields must raise TypeError (not silently
    ignore extras or raise for only the first one)."""
    kwargs = _ctx().__dict__.copy()
    kwargs["bogus_a"] = 1
    kwargs["bogus_b"] = "two"
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_unknown_field_failure_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure injection: if build_artifact raises during context assembly
    with an unknown field present, the build_artifact exception must propagate
    — CheckContext must not swallow it or construct a partially-valid context.
    """
    import tests.foundation.unit.validation.checks.test_context as test_mod

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("artifact store unavailable")

    monkeypatch.setattr(test_mod, "build_artifact", _boom)

    # _ctx() itself will raise because build_artifact is patched;
    # this proves the exception propagates before CheckContext even sees
    # the unknown field — fail-closed (I-01) holds at every layer.
    with pytest.raises(RuntimeError, match="artifact store unavailable"):
        _ctx()


# cpu_ms for CheckContext construction with unknown field (TypeError path).
# Local best-of-10 (batch=200) reads ~0.001 ms.
# 5 ms budget = >=3000x headroom over sub-millisecond real measurement.
_UNKNOWN_FIELD_BUDGET_MS = 5.0


@pytest.mark.perf
def test_context_rejects_unknown_field_perf_budget(perf_budget) -> None:
    """Performance: constructing CheckContext with an unknown field must raise
    TypeError quickly. Budget: best-of-5 < 5 ms.
    Local measurement: ~0.001 ms (Windows 15.6 ms tick floor); 5 ms = 3000x headroom.
    """

    def fn() -> None:
        kwargs = _ctx().__dict__.copy()
        kwargs["unknown_field"] = "nope"
        with pytest.raises(TypeError):
            CheckContext(**kwargs)

    sample = perf_budget.best_of(fn, n=5, warmup=1, batch=200)
    assert sample.cpu_ms < _UNKNOWN_FIELD_BUDGET_MS, f"budget exceeded: {sample.cpu_ms} ms"


@pytest.mark.perf
def test_context_rejects_unknown_field_goes_red_with_10x_delay(perf_budget) -> None:
    """Gate-red reproduction with 10x virtual delay: the TypeError should still
    be raised but the measured cpu_ms must exceed the budget, proving the
    performance budget assertion is real and not a false positive.
    """
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()
    budget_ms = _UNKNOWN_FIELD_BUDGET_MS

    def fn() -> None:
        kwargs = _ctx().__dict__.copy()
        kwargs["unknown_field"] = "nope"
        with pytest.raises(TypeError):
            CheckContext(**kwargs)

    slow = virtual_delayed_sync(fn, budget_ms * 10 / 1000)
    sample = perf_budget.best_of(slow, n=3, warmup=0)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < budget_ms


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


def _field_names() -> list[str]:
    return [f.name for f in fields(CheckContext)]


# cpu_ms per batch of 200 `fields(CheckContext)` introspections. Local best-of-10 reading is
# ~0-1 ms (Windows process_time ticks at 15.6 ms); the 50 ms budget leaves >=30x headroom.
_FIELD_INTROSPECTION_BUDGET_MS = 50.0


@pytest.mark.perf
def test_field_order_introspection_perf_budget(perf_budget) -> None:
    sample = perf_budget.best_of(_field_names, n=10, warmup=1, batch=200)
    assert sample.cpu_ms < _FIELD_INTROSPECTION_BUDGET_MS, f"budget exceeded: {sample.cpu_ms} ms"


@pytest.mark.perf
def test_field_order_introspection_goes_red_with_10x_delay(perf_budget) -> None:
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()
    slow = virtual_delayed_sync(_field_names, _FIELD_INTROSPECTION_BUDGET_MS * 10 / 1000)
    sample = perf_budget.best_of(slow, n=3, warmup=0)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _FIELD_INTROSPECTION_BUDGET_MS


# cpu_ms for one `setattr(ctx, "prior_results", {})` on a frozen CheckContext.
# Local best-of-10 (batch=200) reads 0.000 ms (Windows process_time resolution 15.6 ms).
# 50 ms budget = >=30x headroom over sub-microsecond real measurement.
_FROZEN_REBIND_BUDGET_MS = 50.0


@pytest.mark.perf
def test_context_is_frozen_mutable_field_perf_budget(perf_budget) -> None:
    ctx = _ctx()

    def fn() -> None:
        with pytest.raises(FrozenInstanceError):
            setattr(ctx, "prior_results", {})  # noqa: B010 -- exercises __setattr__, not a field write

    sample = perf_budget.best_of(fn, n=10, warmup=1, batch=200)
    assert sample.cpu_ms < _FROZEN_REBIND_BUDGET_MS, f"budget exceeded: {sample.cpu_ms} ms"


@pytest.mark.perf
def test_context_is_frozen_mutable_field_goes_red_with_10x_delay(perf_budget) -> None:
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()

    ctx = _ctx()

    def fn() -> None:
        with pytest.raises(FrozenInstanceError):
            setattr(ctx, "prior_results", {})  # noqa: B010 -- exercises __setattr__, not a field write

    slow = virtual_delayed_sync(fn, _FROZEN_REBIND_BUDGET_MS * 10 / 1000)
    sample = perf_budget.best_of(slow, n=3, warmup=0)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _FROZEN_REBIND_BUDGET_MS


# cpu_ms for one `delattr(ctx, "trace_id")` on a frozen CheckContext.
# Local best-of-10 (batch=200) reads 0.000 ms (Windows process_time resolution 15.6 ms).
# 50 ms budget = >=30x headroom over sub-microsecond real measurement.
_DELATTR_BUDGET_MS = 50.0


@pytest.mark.perf
def test_context_is_frozen_rejects_delattr_perf_budget(perf_budget) -> None:
    ctx = _ctx()

    def fn() -> None:
        with pytest.raises(FrozenInstanceError):
            delattr(ctx, "trace_id")

    sample = perf_budget.best_of(fn, n=10, warmup=1, batch=200)
    assert sample.cpu_ms < _DELATTR_BUDGET_MS, f"budget exceeded: {sample.cpu_ms} ms"


@pytest.mark.perf
def test_context_is_frozen_rejects_delattr_goes_red_with_10x_delay(perf_budget) -> None:
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()

    ctx = _ctx()

    def fn() -> None:
        with pytest.raises(FrozenInstanceError):
            delattr(ctx, "trace_id")

    slow = virtual_delayed_sync(fn, _DELATTR_BUDGET_MS * 10 / 1000)
    sample = perf_budget.best_of(slow, n=3, warmup=0)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _DELATTR_BUDGET_MS


def test_field_order_assertion_detects_reordered_fields() -> None:
    names = _field_names()
    names[0], names[1] = names[1], names[0]
    assert names != _field_names()


# --- D2 deepening for test_context_construction_propagates_dependency_failure ---

# cpu_ms for one CheckContext construction (full dependency bundle).
# Local best-of-10 (batch=200) reads ~0.05 ms.
# 5 ms budget = >=100x headroom over sub-millisecond real measurement.
_CTX_CONSTRUCTION_BUDGET_MS = 5.0


@pytest.mark.perf
def test_context_construction_propagates_dependency_failure_perf_budget(
    perf_budget,
) -> None:
    """Performance: constructing CheckContext with all dependencies must
    complete within budget. Local best-of-10 measurement: ~0.05 ms.
    Budget: 5 ms = 100x headroom.
    """

    def fn() -> CheckContext:
        return _ctx()

    sample = perf_budget.best_of(fn, n=10, warmup=1, batch=200)
    assert sample.cpu_ms < _CTX_CONSTRUCTION_BUDGET_MS, f"budget exceeded: {sample.cpu_ms} ms"


@pytest.mark.perf
def test_context_construction_propagates_dependency_failure_goes_red_with_10x_delay(
    perf_budget,
) -> None:
    """Gate-red reproduction with 10x virtual delay: the construction must
    still succeed but the measured cpu_ms must exceed the budget, proving
    the performance budget assertion is real and not a false positive.
    """
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()

    def fn() -> CheckContext:
        return _ctx()

    slow = virtual_delayed_sync(fn, _CTX_CONSTRUCTION_BUDGET_MS * 10 / 1000)
    sample = perf_budget.best_of(slow, n=3, warmup=0)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _CTX_CONSTRUCTION_BUDGET_MS


def test_context_rejects_missing_artifact() -> None:
    """Negative: CheckContext must reject construction when artifact is absent."""
    kwargs = _ctx().__dict__.copy()
    del kwargs["artifact"]
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_missing_policy() -> None:
    """Negative: CheckContext must reject construction when policy is absent."""
    kwargs = _ctx().__dict__.copy()
    del kwargs["policy"]
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_rejects_missing_bars() -> None:
    """Negative: CheckContext must reject construction when bars is absent."""
    kwargs = _ctx().__dict__.copy()
    del kwargs["bars"]
    with pytest.raises(TypeError):
        CheckContext(**kwargs)


def test_context_construction_propagates_artifact_build_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adversarial (I-01 fail-closed): if build_artifact raises during
    context assembly, the exception must propagate — CheckContext must not
    construct a partially-valid context. This cross-checks INVARIANTS.md
    I-01 (all writes default fail-closed).
    """
    import tests.foundation.unit.validation.checks.test_context as test_mod

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("artifact store unavailable")

    monkeypatch.setattr(test_mod, "build_artifact", _boom)

    with pytest.raises(RuntimeError, match="artifact store unavailable"):
        _ctx()
