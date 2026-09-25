"""AI-21 -- serve_signal_batch / check_signal_equivalence: the batch/backtest
counterpart of `serve_signal.py`'s live per-bar path, plus the "incremental=
batch equivalence" proof the leaf's DoD asks for.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5/§9 AI-21. Split
from `serve_signal.py` to stay under the architecture guard's 300-line cap
(`P6.line_cap`) -- both files are one leaf/DoD, not two independent ones.

`serve_signal_batch` scores every `as_of` in one call for a single
`entity_id`, caching each `(feature_id, day)` Parquet partition across
repeated days instead of re-reading it once per `as_of` -- the batch half of
IND-1's incremental/vectorized split. Unlike a TA-Lib indicator there is no
rolling window state, so `check_signal_equivalence` is what proves the
cache never returns a different value than `serve_signal`'s uncached
per-call path would.

Drift evaluation is scoped to `serve_signal` only, not this module: spec §6
ties `degraded` to blocking a *new* PAPER promotion, a live-path decision --
a backtest replay has no promotion to block, so `serve_signal_batch` does
not carry a `live_features` argument.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone

from src.foundation.ml.application.serve_signal import (
    FeatureNotFoundError,
    SignalResult,
    cast_feature_value,
    load_card,
    require_aware,
    select_most_recent,
    serve_signal,
)
from src.foundation.ml.contracts.v1 import FeatureSpec
from src.foundation.ml.domain.point_in_time import check_point_in_time
from src.foundation.ml.ports.feature_store import FeatureStorePort, FeatureValue
from src.foundation.ml.ports.model_registry import ModelRegistryPort
from src.foundation.ml.ports.predictor import ModelPredictorPort

__all__ = [
    "EQUIVALENCE_TOLERANCE",
    "SignalEngineMismatchError",
    "serve_signal_batch",
    "check_signal_equivalence",
]

EQUIVALENCE_TOLERANCE = 1e-9


class SignalEngineMismatchError(ValueError):
    """`serve_signal` (per-call) and `serve_signal_batch` (cached) disagree
    on at least one `as_of` beyond `tolerance` -- IND-1's
    `INDICATOR_ENGINE_MISMATCH` counterpart for the ML signal path (AI-21
    DoD "incremental=batch equivalence")."""

    def __init__(self, model_id: str, worst: float) -> None:
        self.model_id = model_id
        self.worst = worst
        super().__init__(
            f"model {model_id!r}: serve_signal/serve_signal_batch disagree "
            f"(worst scaled diff={worst!r})"
        )


async def serve_signal_batch(
    *,
    model_id: str,
    version: str | None,
    entity_id: str,
    as_of_dates: Sequence[datetime],
    feature_specs: Sequence[FeatureSpec],
    registry: ModelRegistryPort,
    feature_store: FeatureStorePort,
    predictor: ModelPredictorPort,
    backtest_start: datetime | None = None,
) -> list[SignalResult]:
    """Score every `as_of` in `as_of_dates` for one `entity_id` against a
    single model lookup, caching each `(feature_id, day)` partition read
    across repeated days instead of re-reading it once per `as_of` (the
    backtest/batch path -- see module docstring)."""
    card = await load_card(registry, model_id, version)
    if backtest_start is not None:
        check_point_in_time(card, backtest_start=backtest_start)

    cache: dict[tuple[str, date], list[FeatureValue]] = {}
    results: list[SignalResult] = []
    for as_of in as_of_dates:
        require_aware(as_of)
        day = as_of.astimezone(timezone.utc).date()
        features: dict[str, float] = {}
        for spec in feature_specs:
            key = (spec.feature_id, day)
            if key not in cache:
                try:
                    cache[key] = list(feature_store.read_batch(spec, day))
                except FileNotFoundError:
                    raise FeatureNotFoundError(spec.feature_id, entity_id, as_of) from None
            value = select_most_recent(cache[key], entity_id, as_of)
            if value is None:
                raise FeatureNotFoundError(spec.feature_id, entity_id, as_of)
            features[spec.feature_id] = cast_feature_value(spec, value)
        results.append(
            SignalResult(
                model_id=card.model_id,
                version=card.version,
                entity_id=entity_id,
                as_of=as_of,
                value=predictor.predict(card, features),
                degraded=False,
                drift=(),
            )
        )
    return results


async def check_signal_equivalence(
    *,
    model_id: str,
    version: str | None,
    entity_id: str,
    as_of_dates: Sequence[datetime],
    feature_specs: Sequence[FeatureSpec],
    registry: ModelRegistryPort,
    feature_store: FeatureStorePort,
    predictor: ModelPredictorPort,
    backtest_start: datetime | None = None,
    tolerance: float = EQUIVALENCE_TOLERANCE,
) -> float:
    """AI-21 DoD "incremental=batch equivalence": call `serve_signal` once
    per `as_of` (uncached, the live/replay path) and `serve_signal_batch`
    once for the whole span (cached, the backtest path) and assert every
    value matches within `tolerance`. Unlike IND-1's TA-Lib engines there is
    no rolling numerical state here -- a mismatch means the cache in
    `serve_signal_batch` picked a different feature row than the uncached
    path would, not floating-point drift. Returns the worst scaled
    difference found (`0.0` on an exact match)."""
    batch = await serve_signal_batch(
        model_id=model_id,
        version=version,
        entity_id=entity_id,
        as_of_dates=as_of_dates,
        feature_specs=feature_specs,
        registry=registry,
        feature_store=feature_store,
        predictor=predictor,
        backtest_start=backtest_start,
    )
    worst = 0.0
    for as_of, batch_result in zip(as_of_dates, batch, strict=True):
        streamed = await serve_signal(
            model_id=model_id,
            version=version,
            entity_id=entity_id,
            as_of=as_of,
            feature_specs=feature_specs,
            registry=registry,
            feature_store=feature_store,
            predictor=predictor,
            backtest_start=backtest_start,
        )
        scale = max(1.0, abs(streamed.value), abs(batch_result.value))
        worst = max(worst, abs(streamed.value - batch_result.value) / scale)
    if worst > tolerance:
        raise SignalEngineMismatchError(model_id, worst)
    return worst
