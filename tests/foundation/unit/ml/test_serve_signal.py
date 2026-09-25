"""Unit tests for `src/foundation/ml/application/serve_signal.py` +
`serve_signal_batch.py` -- AI-21 task-2656. D2 depth (ADR-2026-09-09-C):
negative >= 3, failure injection 1, numeric performance assertion 1,
gate-red reproduction 1, plus the AI-21 DoD "incremental=batch equivalence"
property test.

All ports are in-memory fakes (`_FakeModelRegistry`/`_FakeFeatureStore`) --
`FeatureStorePort`/`ModelRegistryPort` themselves are already covered by
`test_parquet_feature_store.py`/`test_postgres_model_registry.py`; this file
is scoped to `serve_signal`'s own composition/guard logic.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.foundation.ml.application import serve_signal as ss
from src.foundation.ml.application import serve_signal_batch as ssb
from src.foundation.ml.contracts.v1 import FeatureSpec, ModelCard, TrainDataLineage
from src.foundation.ml.domain.point_in_time import FutureDataLeakageError
from src.foundation.ml.ports.feature_store import FeatureValue

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _card(**overrides: Any) -> ModelCard:
    base: dict[str, Any] = dict(
        model_id="momentum-lgbm",
        version="v1",
        model_hash="a" * 64,
        train_data_lineage=TrainDataLineage(
            start=_NOW - timedelta(days=60),
            end=_NOW - timedelta(days=1),
            source_ref="parquet://f/v1",
        ),
        trained_at=_NOW,
        drift_baseline={},
    )
    base.update(overrides)
    return ModelCard(**base)


def _spec(feature_id: str = "rsi_14") -> FeatureSpec:
    return FeatureSpec(feature_id=feature_id, dtype="float", source_ref="parquet://features/rsi_14")


class _FakeModelRegistry:
    def __init__(self, cards: dict[tuple[str, str], ModelCard] | None = None) -> None:
        self._by_id_version = dict(cards or {})

    async def register(self, card: ModelCard) -> ModelCard:
        self._by_id_version[(card.model_id, card.version)] = card
        return card

    async def get(self, model_id: str, version: str) -> ModelCard | None:
        return self._by_id_version.get((model_id, version))

    async def latest(self, model_id: str) -> ModelCard | None:
        matches = [c for (mid, _v), c in self._by_id_version.items() if mid == model_id]
        return max(matches, key=lambda c: c.trained_at) if matches else None


class _FakeFeatureStore:
    def __init__(self) -> None:
        self._data: dict[tuple[str, date], list[FeatureValue]] = {}

    def write_batch(self, spec: FeatureSpec, day: date, rows: Iterable[FeatureValue]) -> Path:
        self._data.setdefault((spec.feature_id, day), []).extend(rows)
        return Path(f"fake://{spec.feature_id}/{day}")

    def read_batch(self, spec: FeatureSpec, day: date) -> Iterator[FeatureValue]:
        key = (spec.feature_id, day)
        if key not in self._data:
            raise FileNotFoundError(key)
        yield from self._data[key]


class _LinearPredictor:
    """Deterministic pure-function stand-in for a real (AI-20) predictor --
    weighted sum of features, no hidden state."""

    def __init__(self, weights: dict[str, float], bias: float = 0.0) -> None:
        self._weights = weights
        self._bias = bias

    def predict(self, card: ModelCard, features: Mapping[str, float]) -> float:
        return self._bias + sum(self._weights.get(k, 0.0) * v for k, v in features.items())


class _ExplodingPredictor:
    def predict(self, card: ModelCard, features: Mapping[str, float]) -> float:
        raise RuntimeError("predictor blew up")


def _store_with_row(
    feature_id: str, entity_id: str, as_of: datetime, value: str
) -> _FakeFeatureStore:
    store = _FakeFeatureStore()
    store.write_batch(
        _spec(feature_id),
        as_of.date(),
        [FeatureValue(entity_id=entity_id, as_of=as_of, value=value)],
    )
    return store


# --- happy path ---


async def test_serve_signal_scores_the_most_recent_feature_row_at_or_before_as_of() -> None:
    registry = _FakeModelRegistry({("momentum-lgbm", "v1"): _card()})
    as_of = _NOW
    store = _store_with_row("rsi_14", "BTC-USD", as_of - timedelta(hours=1), "70.0")
    predictor = _LinearPredictor({"rsi_14": 2.0}, bias=1.0)

    result = await ss.serve_signal(
        model_id="momentum-lgbm",
        version=None,
        entity_id="BTC-USD",
        as_of=as_of,
        feature_specs=[_spec("rsi_14")],
        registry=registry,
        feature_store=store,
        predictor=predictor,
    )

    assert result.value == pytest.approx(1.0 + 2.0 * 70.0)
    assert result.degraded is False
    assert result.model_id == "momentum-lgbm"
    assert result.version == "v1"


# --- negative (>= 3) ---


async def test_serve_signal_rejects_unregistered_model() -> None:
    registry = _FakeModelRegistry()
    with pytest.raises(ss.ModelNotFoundError):
        await ss.serve_signal(
            model_id="does-not-exist",
            version=None,
            entity_id="BTC-USD",
            as_of=_NOW,
            feature_specs=[],
            registry=registry,
            feature_store=_FakeFeatureStore(),
            predictor=_LinearPredictor({}),
        )


async def test_serve_signal_rejects_a_model_trained_on_or_after_backtest_start() -> None:
    """A-3 wired through `serve_signal` (the docstring in `domain/
    point_in_time.py` names this exact call site)."""
    backtest_start = _NOW - timedelta(days=1)
    card = _card(
        train_data_lineage=TrainDataLineage(
            start=_NOW - timedelta(days=10), end=backtest_start, source_ref="parquet://f/v1"
        )
    )
    registry = _FakeModelRegistry({("momentum-lgbm", "v1"): card})

    with pytest.raises(FutureDataLeakageError):
        await ss.serve_signal(
            model_id="momentum-lgbm",
            version="v1",
            entity_id="BTC-USD",
            as_of=_NOW,
            feature_specs=[],
            registry=registry,
            feature_store=_FakeFeatureStore(),
            predictor=_LinearPredictor({}),
            backtest_start=backtest_start,
        )


async def test_serve_signal_rejects_an_unpublished_feature_partition() -> None:
    registry = _FakeModelRegistry({("momentum-lgbm", "v1"): _card()})
    with pytest.raises(ss.FeatureNotFoundError):
        await ss.serve_signal(
            model_id="momentum-lgbm",
            version="v1",
            entity_id="BTC-USD",
            as_of=_NOW,
            feature_specs=[_spec("rsi_14")],
            registry=registry,
            feature_store=_FakeFeatureStore(),
            predictor=_LinearPredictor({}),
        )


async def test_serve_signal_rejects_a_feature_row_published_only_after_as_of() -> None:
    """Adversarial-shaped: the partition exists, but every row for this
    entity is dated *after* the requested `as_of` -- a naive "just take the
    last row" read would leak a future feature value into the signal. This
    is the module's own look-ahead guard, distinct from A-3's model-lineage
    guard."""
    registry = _FakeModelRegistry({("momentum-lgbm", "v1"): _card()})
    store = _store_with_row("rsi_14", "BTC-USD", _NOW + timedelta(hours=1), "99.0")

    with pytest.raises(ss.FeatureNotFoundError):
        await ss.serve_signal(
            model_id="momentum-lgbm",
            version="v1",
            entity_id="BTC-USD",
            as_of=_NOW,
            feature_specs=[_spec("rsi_14")],
            registry=registry,
            feature_store=store,
            predictor=_LinearPredictor({}),
        )


# --- failure injection ---


async def test_serve_signal_propagates_a_predictor_failure_instead_of_a_default_value() -> None:
    registry = _FakeModelRegistry({("momentum-lgbm", "v1"): _card()})
    store = _store_with_row("rsi_14", "BTC-USD", _NOW, "50.0")

    with pytest.raises(RuntimeError, match="predictor blew up"):
        await ss.serve_signal(
            model_id="momentum-lgbm",
            version="v1",
            entity_id="BTC-USD",
            as_of=_NOW,
            feature_specs=[_spec("rsi_14")],
            registry=registry,
            feature_store=store,
            predictor=_ExplodingPredictor(),
        )


# --- gate-red reproduction: look-ahead guard ---


async def test_gate_red_naive_selection_would_leak_a_future_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red repro: swap `select_most_recent` for a naive "most recent row
    regardless of as_of" implementation and show it *would* return a
    future-dated value (the bug `select_most_recent`'s `row.as_of > as_of`
    guard exists to prevent) -- then restore the real implementation and
    confirm the same fixture raises `FeatureNotFoundError` instead."""
    registry = _FakeModelRegistry({("momentum-lgbm", "v1"): _card()})
    store = _store_with_row("rsi_14", "BTC-USD", _NOW + timedelta(hours=1), "99.0")
    predictor = _LinearPredictor({"rsi_14": 1.0})

    def _naive_select(
        rows: Iterable[FeatureValue], entity_id: str, as_of: datetime
    ) -> str | None:  # no as_of cutoff at all
        matching = [r for r in rows if r.entity_id == entity_id]
        return matching[-1].value if matching else None

    monkeypatch.setattr(ss, "select_most_recent", _naive_select)
    leaked = await ss.serve_signal(
        model_id="momentum-lgbm",
        version="v1",
        entity_id="BTC-USD",
        as_of=_NOW,
        feature_specs=[_spec("rsi_14")],
        registry=registry,
        feature_store=store,
        predictor=predictor,
    )
    assert leaked.value == pytest.approx(99.0)  # red: future value leaked

    monkeypatch.undo()
    with pytest.raises(ss.FeatureNotFoundError):
        await ss.serve_signal(
            model_id="momentum-lgbm",
            version="v1",
            entity_id="BTC-USD",
            as_of=_NOW,
            feature_specs=[_spec("rsi_14")],
            registry=registry,
            feature_store=store,
            predictor=predictor,
        )


# --- incremental = batch equivalence (AI-21 DoD) ---


async def test_check_signal_equivalence_agrees_across_many_days() -> None:
    registry = _FakeModelRegistry({("momentum-lgbm", "v1"): _card()})
    store = _FakeFeatureStore()
    n_days = 120
    as_of_dates = [_NOW + timedelta(days=i) for i in range(n_days)]
    for i, as_of in enumerate(as_of_dates):
        store.write_batch(
            _spec("rsi_14"),
            as_of.date(),
            [FeatureValue(entity_id="BTC-USD", as_of=as_of, value=str(30.0 + i))],
        )
    predictor = _LinearPredictor({"rsi_14": 0.5}, bias=-3.0)

    worst = await ssb.check_signal_equivalence(
        model_id="momentum-lgbm",
        version="v1",
        entity_id="BTC-USD",
        as_of_dates=as_of_dates,
        feature_specs=[_spec("rsi_14")],
        registry=registry,
        feature_store=store,
        predictor=predictor,
    )
    assert worst == 0.0


async def test_check_signal_equivalence_raises_when_the_cache_returns_a_stale_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-mode control for the equivalence property: if
    `serve_signal_batch`'s per-day cache ever mixed up two different days
    (e.g. keyed by feature_id alone), it would silently score a bar against
    the wrong day's feature value while `serve_signal` (uncached) kept
    scoring correctly -- `check_signal_equivalence` must catch that as
    `SignalEngineMismatchError`, not let it pass."""
    registry = _FakeModelRegistry({("momentum-lgbm", "v1"): _card()})
    store = _FakeFeatureStore()
    as_of_dates = [_NOW + timedelta(days=i) for i in range(5)]
    for i, as_of in enumerate(as_of_dates):
        store.write_batch(
            _spec("rsi_14"),
            as_of.date(),
            [FeatureValue(entity_id="BTC-USD", as_of=as_of, value=str(10.0 + i))],
        )
    predictor = _LinearPredictor({"rsi_14": 1.0})

    real_serve_signal_batch = ssb.serve_signal_batch

    async def _stale_cache_batch(**kwargs: Any) -> list[ss.SignalResult]:
        results = await real_serve_signal_batch(**kwargs)
        # Corrupt exactly one entry to simulate a day-cache mix-up.
        stale = results[2]
        results[2] = ss.SignalResult(
            model_id=stale.model_id,
            version=stale.version,
            entity_id=stale.entity_id,
            as_of=stale.as_of,
            value=stale.value + 1.0,
            degraded=stale.degraded,
            drift=stale.drift,
        )
        return results

    monkeypatch.setattr(ssb, "serve_signal_batch", _stale_cache_batch)
    with pytest.raises(ssb.SignalEngineMismatchError):
        await ssb.check_signal_equivalence(
            model_id="momentum-lgbm",
            version="v1",
            entity_id="BTC-USD",
            as_of_dates=as_of_dates,
            feature_specs=[_spec("rsi_14")],
            registry=registry,
            feature_store=store,
            predictor=predictor,
        )


# --- numeric performance assertion ---


@pytest.mark.perf
async def test_serve_signal_latency_p99_within_budget() -> None:
    """ADR-2026-09-09-C Decision 1's per-axis performance budget table has
    no ML-signal-serving row -- mirrors `tests/unit/core/indicators/
    test_engine_equivalence.py::test_incremental_update_latency_p99_within_
    streaming_budget`'s approach of setting a local budget from a real
    measurement with generous CI headroom. `serve_signal` here does one
    dict lookup (fake registry), one list scan of a single-row partition
    (fake store), and one multiply-add (linear predictor) -- no real disk
    or network I/O -- so 1ms is ~50x the observed local per-call latency
    (~20us), leaving headroom for slower CI hardware and asyncio scheduling
    jitter."""
    registry = _FakeModelRegistry({("momentum-lgbm", "v1"): _card()})
    store = _FakeFeatureStore()
    n = 2000
    as_of_dates = [_NOW + timedelta(hours=i) for i in range(n)]
    for as_of in as_of_dates:
        store.write_batch(
            _spec("rsi_14"),
            as_of.date(),
            [FeatureValue(entity_id="BTC-USD", as_of=as_of, value="42.0")],
        )
    predictor = _LinearPredictor({"rsi_14": 1.0})

    samples = [0.0] * n
    for i, as_of in enumerate(as_of_dates):
        start = time.perf_counter()
        await ss.serve_signal(
            model_id="momentum-lgbm",
            version="v1",
            entity_id="BTC-USD",
            as_of=as_of,
            feature_specs=[_spec("rsi_14")],
            registry=registry,
            feature_store=store,
            predictor=predictor,
        )
        samples[i] = time.perf_counter() - start
    samples.sort()
    p99 = samples[int(n * 0.99)]
    budget_sec = 1e-3
    print(f"[AI-21 serve_signal] n={n} p99={p99 * 1e6:.2f}us budget<{budget_sec * 1e6:.0f}us")
    assert p99 < budget_sec
