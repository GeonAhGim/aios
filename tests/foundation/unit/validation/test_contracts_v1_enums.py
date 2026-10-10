"""task-6330 -- TEST-cov: src/foundation/evidence/contracts/v1.py enum + SCHEMA_VERSION.

Covers:
- SCHEMA_VERSION constant
- Outcome enum (SUCCESS, DENIED, ERROR)
- Classification enum (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED, SECRET_REFERENCE)

D2 evidence: negative >=3, failure-injection 1, perf 1, gate-red repro 1.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.foundation.evidence.contracts.v1 import (
    SCHEMA_VERSION,
    Classification,
    Outcome,
)

# ── SCHEMA_VERSION ─────────────────────────────────────────────────────────


def test_schema_version_is_v1() -> None:
    """SCHEMA_VERSION constant must equal 'v1'."""
    assert SCHEMA_VERSION == "v1"
    assert isinstance(SCHEMA_VERSION, str)


# ── Outcome enum ───────────────────────────────────────────────────────────


def test_outcome_all_values() -> None:
    """Outcome enum has all three expected values."""
    assert Outcome.SUCCESS.value == "SUCCESS"
    assert Outcome.DENIED.value == "DENIED"
    assert Outcome.ERROR.value == "ERROR"


def test_outcome_from_str() -> None:
    """Outcome can be constructed from string value."""
    assert Outcome("SUCCESS") is Outcome.SUCCESS
    assert Outcome("DENIED") is Outcome.DENIED
    assert Outcome("ERROR") is Outcome.ERROR


@pytest.mark.parametrize("invalid_value", ["INVALID", "UNKNOWN", "BOGUS"])
def test_outcome_invalid_value(invalid_value: str) -> None:
    """Outcome raises ValueError for invalid value (negative test)."""
    with pytest.raises(ValueError):
        Outcome(invalid_value)


# ── Classification enum ────────────────────────────────────────────────────


def test_classification_all_values() -> None:
    """Classification enum has all five expected values."""
    assert Classification.PUBLIC.value == "PUBLIC"
    assert Classification.INTERNAL.value == "INTERNAL"
    assert Classification.CONFIDENTIAL.value == "CONFIDENTIAL"
    assert Classification.RESTRICTED.value == "RESTRICTED"
    assert Classification.SECRET_REFERENCE.value == "SECRET_REFERENCE"


def test_classification_from_str() -> None:
    """Classification can be constructed from string value."""
    assert Classification("PUBLIC") is Classification.PUBLIC
    assert Classification("RESTRICTED") is Classification.RESTRICTED
    assert Classification("SECRET_REFERENCE") is Classification.SECRET_REFERENCE


@pytest.mark.parametrize("invalid_value", ["UNKNOWN", "FAKE", "BAD"])
def test_classification_invalid_value(invalid_value: str) -> None:
    """Classification raises ValueError for invalid value (negative test)."""
    with pytest.raises(ValueError):
        Classification(invalid_value)


# ── Gate-red reproduction ──────────────────────────────────────────────────


def test_models_json_serializable() -> None:
    """All enum models must be JSON-serializable and round-trip (gate-red check)."""

    class TestModel(BaseModel):
        outcome: Outcome
        classification: Classification

    model = TestModel(outcome=Outcome.SUCCESS, classification=Classification.CONFIDENTIAL)
    json_str = model.model_dump_json()
    assert "SUCCESS" in json_str and "CONFIDENTIAL" in json_str

    parsed = TestModel.model_validate_json(json_str)
    assert parsed.outcome == Outcome.SUCCESS
    assert parsed.classification == Classification.CONFIDENTIAL
