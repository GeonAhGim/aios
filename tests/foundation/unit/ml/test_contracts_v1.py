"""Unit tests for `src/foundation/ml/contracts/v1.py` -- task-2653 AI-18.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-18.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from src.foundation.ml.contracts.v1 import FeatureSpec, ModelCard, TrainDataLineage

_NOW = datetime.now(timezone.utc)
_LINEAGE_START = _NOW - timedelta(days=30)
_LINEAGE_END = _NOW - timedelta(days=1)


def _lineage(**overrides: Any) -> TrainDataLineage:
    base: dict[str, Any] = dict(
        start=_LINEAGE_START, end=_LINEAGE_END, source_ref="parquet://features/v1"
    )
    base.update(overrides)
    return TrainDataLineage(**base)


def _model_card(**overrides: Any) -> ModelCard:
    base: dict[str, Any] = dict(
        model_id="momentum-lgbm",
        version="1",
        model_hash="a" * 64,
        train_data_lineage=_lineage(),
        trained_at=_NOW,
        metrics={"auc": 0.71},
        drift_baseline={"rsi_14": (1.0, 2.0, 3.0)},
    )
    base.update(overrides)
    return ModelCard(**base)


# --- happy path ---


def test_train_data_lineage_accepts_valid_span() -> None:
    lineage = _lineage()
    assert lineage.start < lineage.end


def test_model_card_accepts_valid_fields() -> None:
    card = _model_card()
    assert card.model_id == "momentum-lgbm"
    assert card.schema_version == "v1"


def test_feature_spec_accepts_valid_fields() -> None:
    spec = FeatureSpec(feature_id="rsi_14", dtype="float", source_ref="parquet://features/v1")
    assert spec.description == ""


# --- negative (>= 3) ---


def test_train_data_lineage_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        _lineage(start=_NOW, end=_NOW - timedelta(days=1))


def test_train_data_lineage_rejects_blank_source_ref() -> None:
    with pytest.raises(ValidationError):
        _lineage(source_ref="   ")


def test_model_card_rejects_non_hex_model_hash() -> None:
    with pytest.raises(ValidationError):
        _model_card(model_hash="not-a-hash")


def test_model_card_rejects_trained_at_before_lineage_end() -> None:
    with pytest.raises(ValidationError):
        _model_card(trained_at=_LINEAGE_START)


def test_model_card_rejects_unknown_field() -> None:
    payload: dict[str, Any] = dict(
        model_id="x",
        version="1",
        model_hash="a" * 64,
        train_data_lineage=_lineage(),
        trained_at=_NOW,
        extra_field="not allowed",
    )
    with pytest.raises(ValidationError):
        ModelCard.model_validate(payload)


def test_feature_spec_rejects_blank_feature_id() -> None:
    with pytest.raises(ValidationError):
        FeatureSpec(feature_id="  ", dtype="float", source_ref="parquet://features/v1")
