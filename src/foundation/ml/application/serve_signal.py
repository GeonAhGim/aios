"""AI-21 -- serve_signal: compose ModelRegistry + FeatureStore + a
`ModelPredictorPort` into one signal-serving call (the live/replay path).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-21
(`application/serve_signal.py`), §9 AI-21 DoD ("IND spec compliance,
incremental=batch equivalence"), §4 A-3 (point-in-time guard), §6 ("model
drift -> the signal result carries a `degraded` flag, new PAPER promotion
is blocked").

This module is the caller `domain/point_in_time.py::check_point_in_time`'s
own docstring already names ("AI-21's `serve_signal`"). `serve_signal`
scores one `(entity_id, as_of)` at a time -- a strategy calls this once per
bar, same as `IncrementalIndicator.update()`. `serve_signal_batch.py` (same
leaf, split into its own file to stay under the architecture guard's
300-line `P6.line_cap`) holds the batch/backtest counterpart plus the
"incremental=batch equivalence" proof the AI-21 DoD asks for, reusing the
helpers this module exports below.

`serve_signal` runs three existing AI-18/19 primitives in sequence and adds
no math of its own (same "delegation only" discipline as `ai/factory/
application/research_tools.py`, AI-14): `check_point_in_time` (A-3),
`FeatureStorePort.read_batch` (AI-19) picked down to the single most-recent
value at/before `as_of` for `entity_id` (fail-closed on a future-dated or
entity-missing row -- this module's own look-ahead guard, one layer above
A-3's model-lineage guard), and `evaluate_drift` (AI-18) against
`ModelCard.drift_baseline`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

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
    "require_aware",
    "cast_feature_value",
    "select_most_recent",
    "load_card",
    "serve_signal",
]


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


@dataclass(frozen=True)
class SignalResult:
    model_id: str
    version: str
    entity_id: str
    as_of: datetime
    value: float
    degraded: bool
    drift: tuple[DriftResult, ...] = ()


def require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware (CLAUDE.md §3)")


def cast_feature_value(spec: FeatureSpec, value: str) -> float:
    validate_feature_value(spec, value)
    if spec.dtype == "bool":
        return 1.0 if value == "true" else 0.0
    return float(value)


def select_most_recent(rows: Sequence[FeatureValue], entity_id: str, as_of: datetime) -> str | None:
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
    value = select_most_recent(rows, entity_id, as_of)
    if value is None:
        raise FeatureNotFoundError(spec.feature_id, entity_id, as_of)
    return cast_feature_value(spec, value)


async def load_card(registry: ModelRegistryPort, model_id: str, version: str | None) -> ModelCard:
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
    require_aware(as_of)
    card = await load_card(registry, model_id, version)
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
