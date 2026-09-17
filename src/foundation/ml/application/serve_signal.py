"""AI-21 -- serve_signal: compose ModelRegistry + FeatureStore + a
`ModelPredictorPort` into one signal-serving call.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-21
(`application/serve_signal.py`), §9 AI-21 DoD ("IND spec compliance,
incremental=batch equivalence"), §4 A-3 (point-in-time guard), §6 ("model
drift -> the signal result carries a `degraded` flag, new PAPER promotion
is blocked").

This module is the caller `domain/point_in_time.py::check_point_in_time`'s
own docstring already names ("AI-21's `serve_signal`"). It runs three
existing AI-18/19 primitives in sequence and adds no math of its own
(same "delegation only" discipline as `ai/factory/application/
research_tools.py`, AI-14): `check_point_in_time` (A-3), `FeatureStorePort.
read_batch` (AI-19) picked down to the single most-recent value at/before
`as_of` for `entity_id` (fail-closed on a future-dated or entity-missing
row -- this module's own look-ahead guard, one layer above A-3's
model-lineage guard), and `evaluate_drift` (AI-18) against
`ModelCard.drift_baseline`.

Two call paths, one per §9 DoD half:
- `serve_signal` -- one `(entity_id, as_of)` at a time, no caching. The
  live/replay path: a strategy calls this once per bar, same as
  `IncrementalIndicator.update()`.
- `serve_signal_batch` -- one `entity_id`, many `as_of` values, with a
  per-`(feature_id, day)` partition cache so a backtest over N bars reads
  each Parquet partition once instead of N times. This is the batch half
  of IND-1's incremental/vectorized split; unlike a TA-Lib
  indicator there is no rolling window state, so `check_signal_equivalence`
  below is what proves the cache never returns a different value than the
  uncached per-call path would.

Drift evaluation is scoped to `serve_signal` only (not `serve_signal_batch`):
§6 ties `degraded` to blocking a *new* PAPER promotion, which is a live-path
decision -- a backtest replay has no promotion to block, so a batch call
does not carry a `live_features` argument.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from src.foundation.ml.contracts.v1 import FeatureSpec, ModelCard
from src.foundation.ml.domain.drift import (
    DEFAULT_THRESHOLDS,
    DriftResult,
    DriftThresholds,
    evaluate_drift,
)
from src.foundation.ml.domain.feature_values import validate_feature_value
from src.foundation.ml.domain.point_in_time import check_point_in_time
from src.foundation.ml.ports.feature_store import FeatureStorePort, FeatureValue
from src.foundation.ml.ports.model_registry import ModelRegistryPort
from src.foundation.ml.ports.predictor import ModelPredictorPort

__all__ = [
    "SignalResult",
    "ModelNotFoundError",
    "FeatureNotFoundError",
    "SignalEngineMismatchError",
    "EQUIVALENCE_TOLERANCE",
    "serve_signal",
    "serve_signal_batch",
    "check_signal_equivalence",
]

EQUIVALENCE_TOLERANCE = 1e-9


class ModelNotFoundError(LookupError):
    """`model_id`/`version` has no registration in `ModelRegistryPort`
    (`version=None` means "no registration at all exists for `model_id`",
    since that case resolves through `.latest()`)."""

    def __init__(self, model_id: str, version: str | None) -> None:
        self.model_id = model_id
        self.version = version
        super().__init__(f"model {model_id!r} version={version!r} is not registered")


class FeatureNotFoundError(LookupError):
    """`feature_id` has no published value for `entity_id` at or before
    `as_of` -- either the partition was never published (`FileNotFoundError`
    from `FeatureStorePort.read_batch`) or it was published but every row
    for `entity_id` is dated after `as_of` (a would-be look-ahead read,
    rejected the same fail-closed way A-3 rejects a future-trained model)."""

    def __init__(self, feature_id: str, entity_id: str, as_of: datetime) -> None:
        self.feature_id = feature_id
        self.entity_id = entity_id
        self.as_of = as_of
        super().__init__(
            f"feature {feature_id!r} has no value for entity_id={entity_id!r} "
            f"at/before as_of={as_of.isoformat()}"
        )


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


@dataclass(frozen=True)
class SignalResult:
    model_id: str
    version: str
    entity_id: str
    as_of: datetime
    value: float
    degraded: bool
    drift: tuple[DriftResult, ...] = ()


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware (CLAUDE.md §3)")


def _cast(spec: FeatureSpec, value: str) -> float:
    validate_feature_value(spec, value)
    if spec.dtype == "bool":
        return 1.0 if value == "true" else 0.0
    return float(value)


def _select(rows: Sequence[FeatureValue], entity_id: str, as_of: datetime) -> str | None:
    """Most-recent row for `entity_id` at/before `as_of` -- `None` if no
    such row exists (a strictly-future-dated row for the same entity does
    not count, same "future data is invisible" discipline as A-3)."""
    best_value: str | None = None
    best_as_of: datetime | None = None
    for row in rows:
        if row.entity_id != entity_id or row.as_of > as_of:
            continue
        if best_as_of is None or row.as_of > best_as_of:
            best_value, best_as_of = row.value, row.as_of
    return best_value


def _read_one(
    feature_store: FeatureStorePort, spec: FeatureSpec, entity_id: str, as_of: datetime
) -> float:
    day = as_of.astimezone(timezone.utc).date()
    try:
        rows = list(feature_store.read_batch(spec, day))
    except FileNotFoundError:
        raise FeatureNotFoundError(spec.feature_id, entity_id, as_of) from None
    value = _select(rows, entity_id, as_of)
    if value is None:
        raise FeatureNotFoundError(spec.feature_id, entity_id, as_of)
    return _cast(spec, value)


async def _load_card(registry: ModelRegistryPort, model_id: str, version: str | None) -> ModelCard:
    card = (
        await registry.get(model_id, version)
        if version is not None
        else await registry.latest(model_id)
    )
    if card is None:
        raise ModelNotFoundError(model_id, version)
    return card


async def serve_signal(
    *,
    model_id: str,
    version: str | None,
    entity_id: str,
    as_of: datetime,
    feature_specs: Sequence[FeatureSpec],
    registry: ModelRegistryPort,
    feature_store: FeatureStorePort,
    predictor: ModelPredictorPort,
    backtest_start: datetime | None = None,
    live_features: Mapping[str, Sequence[float]] | None = None,
    drift_thresholds: DriftThresholds = DEFAULT_THRESHOLDS,
) -> SignalResult:
    """Score one `(entity_id, as_of)` against `model_id`/`version`
    (`version=None` -> latest registration). Raises `ModelNotFoundError`,
    `FutureDataLeakageError` (A-3, only when `backtest_start` is given),
    or `FeatureNotFoundError` before ever calling `predictor.predict` --
    a missing input never silently becomes a default/zero feature value."""
    _require_aware(as_of)
    card = await _load_card(registry, model_id, version)
    if backtest_start is not None:
        check_point_in_time(card, backtest_start=backtest_start)
    features = {
        spec.feature_id: _read_one(feature_store, spec, entity_id, as_of) for spec in feature_specs
    }
    drift_results = tuple(
        evaluate_drift(
            feature_id, card.drift_baseline[feature_id], sample, thresholds=drift_thresholds
        )
        for feature_id, sample in (live_features or {}).items()
        if feature_id in card.drift_baseline
    )
    value = predictor.predict(card, features)
    return SignalResult(
        model_id=card.model_id,
        version=card.version,
        entity_id=entity_id,
        as_of=as_of,
        value=value,
        degraded=any(result.degraded for result in drift_results),
        drift=drift_results,
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
    card = await _load_card(registry, model_id, version)
    if backtest_start is not None:
        check_point_in_time(card, backtest_start=backtest_start)

    cache: dict[tuple[str, date], list[FeatureValue]] = {}
    results: list[SignalResult] = []
    for as_of in as_of_dates:
        _require_aware(as_of)
        day = as_of.astimezone(timezone.utc).date()
        features: dict[str, float] = {}
        for spec in feature_specs:
            key = (spec.feature_id, day)
            if key not in cache:
                try:
                    cache[key] = list(feature_store.read_batch(spec, day))
                except FileNotFoundError:
                    raise FeatureNotFoundError(spec.feature_id, entity_id, as_of) from None
            value = _select(cache[key], entity_id, as_of)
            if value is None:
                raise FeatureNotFoundError(spec.feature_id, entity_id, as_of)
            features[spec.feature_id] = _cast(spec, value)
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
