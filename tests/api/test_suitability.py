"""TEST-cov task-4664 — src/api/schemas/suitability.py 커버리지 보강.

순수 변환 함수(to_risk_profile_response/to_history_entry)라 DB 없이 단위테스트로
충분하다 — I/O 없는 pydantic 모델 매핑.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from src.api.schemas.suitability import (
    RiskProfileHistoryEntry,
    RiskProfileResponse,
    to_history_entry,
    to_risk_profile_response,
)
from src.services.risk_profile_service import RiskProfileRecord

_ASSESSED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NEXT_DUE = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _record(**overrides: Any) -> RiskProfileRecord:
    defaults: dict[str, Any] = {
        "risk_profile": "중립형",
        "assessed_at": _ASSESSED_AT,
        "next_reassessment_due": _NEXT_DUE,
        "is_higher_risk_than_previous": False,
    }
    defaults.update(overrides)
    return RiskProfileRecord.model_validate(defaults)


def test_to_risk_profile_response_maps_all_fields() -> None:
    record = _record(risk_profile="공격형", is_higher_risk_than_previous=True)

    response = to_risk_profile_response(record)

    assert isinstance(response, RiskProfileResponse)
    assert response.risk_profile == "공격형"
    assert response.assessed_at == _ASSESSED_AT
    assert response.next_reassessment_due == _NEXT_DUE
    assert response.is_higher_risk_than_previous is True


def test_to_risk_profile_response_default_is_higher_risk_false() -> None:
    record = _record()

    response = to_risk_profile_response(record)

    assert response.is_higher_risk_than_previous is False


def test_to_history_entry_parses_json_string_answers() -> None:
    row = {
        "risk_profile": "안정형",
        "assessed_at": _ASSESSED_AT,
        "assessment_answers": json.dumps({"years_of_experience": 0}),
    }

    entry = to_history_entry(row)

    assert isinstance(entry, RiskProfileHistoryEntry)
    assert entry.risk_profile == "안정형"
    assert entry.assessed_at == _ASSESSED_AT
    assert entry.answers == {"years_of_experience": 0}


def test_to_history_entry_accepts_dict_answers_without_reparsing() -> None:
    row = {
        "risk_profile": "중립형",
        "assessed_at": _ASSESSED_AT,
        "assessment_answers": {"years_of_experience": 5},
    }

    entry = to_history_entry(row)

    assert entry.answers == {"years_of_experience": 5}


# --- negative tests -------------------------------------------------------


def test_to_history_entry_missing_key_raises_key_error() -> None:
    row = {"risk_profile": "안정형", "assessed_at": _ASSESSED_AT}

    with pytest.raises(KeyError):
        to_history_entry(row)


def test_to_history_entry_invalid_json_string_raises() -> None:
    row = {
        "risk_profile": "안정형",
        "assessed_at": _ASSESSED_AT,
        "assessment_answers": "{not valid json",
    }

    with pytest.raises(json.JSONDecodeError):
        to_history_entry(row)


def test_risk_profile_response_rejects_missing_required_field() -> None:
    payload: dict[str, Any] = {
        "assessed_at": _ASSESSED_AT,
        "next_reassessment_due": _NEXT_DUE,
    }

    with pytest.raises(ValidationError):
        RiskProfileResponse.model_validate(payload)


def test_risk_profile_response_rejects_non_datetime_assessed_at() -> None:
    payload: dict[str, Any] = {
        "risk_profile": "중립형",
        "assessed_at": "not-a-datetime",
        "next_reassessment_due": _NEXT_DUE,
    }

    with pytest.raises(ValidationError):
        RiskProfileResponse.model_validate(payload)


def test_to_history_entry_answers_wrong_type_raises_validation_error() -> None:
    row = {
        "risk_profile": "안정형",
        "assessed_at": _ASSESSED_AT,
        "assessment_answers": json.dumps([1, 2, 3]),
    }

    with pytest.raises(ValidationError):
        to_history_entry(row)


# --- failure injection ------------------------------------------------------


def test_to_history_entry_answers_field_raises_when_json_loads_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_value: str) -> None:
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(json, "loads", _boom)

    row = {
        "risk_profile": "안정형",
        "assessed_at": _ASSESSED_AT,
        "assessment_answers": json.dumps({"years_of_experience": 0}),
    }

    with pytest.raises(RuntimeError, match="dependency exploded"):
        to_history_entry(row)
