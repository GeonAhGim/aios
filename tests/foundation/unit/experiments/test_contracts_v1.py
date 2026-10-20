"""Unit tests for `src/foundation/experiments/contracts/v1.py` -- task-2645
AI-10 DoD. Pure contract, no I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind

_NOW = datetime.now(timezone.utc)
_HEX64 = "a" * 64


def _experiment(**overrides: Any) -> Experiment:
    base: dict[str, Any] = dict(
        experiment_id=uuid4(),
        tenant_id=uuid4(),
        reproducibility_key=_HEX64,
        kind=ExperimentKind.BACKTEST,
        inputs_hash=_HEX64,
        metrics={"sharpe": 1.2},
        artifacts=("s3://bucket/report.json",),
        parent_id=None,
        created_by=uuid4(),
        created_at=_NOW,
    )
    base.update(overrides)
    return Experiment(**base)


def test_experiment_accepts_valid_payload() -> None:
    exp = _experiment()
    assert exp.kind == ExperimentKind.BACKTEST
    assert exp.schema_version == "v1"


def test_experiment_kind_accepts_all_four_spec_values() -> None:
    for kind in ExperimentKind:
        assert _experiment(kind=kind).kind == kind


# --- negative ---


def test_experiment_rejects_non_hex_reproducibility_key() -> None:
    with pytest.raises(ValidationError):
        _experiment(reproducibility_key="not-a-hash")


def test_experiment_rejects_short_inputs_hash() -> None:
    with pytest.raises(ValidationError):
        _experiment(inputs_hash="a" * 63)


def test_experiment_rejects_uppercase_hash() -> None:
    """Shape check is lowercase-only -- callers must normalize before
    constructing (same discipline as `factory/contracts/v1.py`)."""
    with pytest.raises(ValidationError):
        _experiment(reproducibility_key="A" * 64)


def test_experiment_rejects_self_parent() -> None:
    exp_id = uuid4()
    with pytest.raises(ValidationError):
        _experiment(experiment_id=exp_id, parent_id=exp_id)


def test_experiment_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError):
        _experiment(created_at=datetime(2026, 1, 1))


def test_experiment_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        _experiment(kind="live")


def test_experiment_is_frozen() -> None:
    exp = _experiment()
    with pytest.raises(ValidationError):
        exp.inputs_hash = "b" * 64
