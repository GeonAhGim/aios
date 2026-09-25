"""AI-18 -- ML Signals contract v1.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-18
(`contracts/v1.py`: `FeatureSpec`, `ModelCard{model_id, version, model_hash,
train_data_lineage, trained_at, metrics, drift_baseline}`), §9 AI-18 DoD
("future data rejection (A-3)").

`TrainDataLineage.end` is the field §4 A-3 tests against
(`train_data_lineage.end < backtest.start`) -- `domain/point_in_time.py`
enforces that invariant using `ModelCard.train_data_lineage`, not this
module (contracts stay pure data, standard 71 §4 layering: contracts
modules must not depend on domain modules of the same or another leaf).

`drift_baseline` holds, per feature, the raw baseline sample values
`domain/drift.py::population_stability_index`/`ks_statistic` compare a
live sample against -- not a precomputed histogram, so a caller can rebin
with a different bucket count without re-touching the stored `ModelCard`.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator, model_validator

__all__ = ["SCHEMA_VERSION", "TrainDataLineage", "FeatureSpec", "ModelCard"]

SCHEMA_VERSION: Literal["v1"] = "v1"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256_hex(value: str) -> str:
    """Shape check only -- never recomputes a digest (same discipline as
    `gateway/contracts/v1.py::_validate_sha256_hex`, duplicated here rather
    than imported because contracts modules must not depend on each
    other)."""
    if not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError("must be a lowercase sha256 hex digest (64 hex chars)")
    return value


class TrainDataLineage(BaseModel):
    """The span of data a model's training run consumed. `end` is the field
    `domain/point_in_time.py::check_point_in_time` compares against a
    backtest's start (spec §4 A-3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: AwareDatetime
    end: AwareDatetime
    source_ref: str

    @field_validator("source_ref")
    @classmethod
    def _check_source_ref(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_ref must not be empty")
        return value

    @model_validator(mode="after")
    def _check_span(self) -> TrainDataLineage:
        if self.end < self.start:
            raise ValueError(
                f"train_data_lineage.end ({self.end!r}) must be >= start ({self.start!r})"
            )
        return self


class FeatureSpec(BaseModel):
    """One feature a model consumes -- identity + storage type only; the
    actual values live in a feature store (AI-19, not yet implemented)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feature_id: str
    dtype: Literal["float", "int", "bool", "category"]
    source_ref: str
    description: str = ""
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @field_validator("feature_id", "source_ref")
    @classmethod
    def _check_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class ModelCard(BaseModel):
    """§2.5 AI-18 row verbatim field list."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    version: str
    model_hash: str
    train_data_lineage: TrainDataLineage
    trained_at: AwareDatetime
    metrics: dict[str, float] = {}
    drift_baseline: dict[str, tuple[float, ...]] = {}
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @field_validator("model_id", "version")
    @classmethod
    def _check_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("model_hash")
    @classmethod
    def _check_model_hash(cls, value: str) -> str:
        return _validate_sha256_hex(value)

    @model_validator(mode="after")
    def _check_trained_at_after_lineage(self) -> ModelCard:
        if self.trained_at < self.train_data_lineage.end:
            raise ValueError(
                "trained_at must be >= train_data_lineage.end "
                f"(trained_at={self.trained_at!r}, lineage.end={self.train_data_lineage.end!r})"
            )
        return self
