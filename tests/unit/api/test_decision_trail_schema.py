"""Legacy decision-trail response mapper keeps the RiskTrailEntry contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.decision_trail import to_decision_trail_response
from src.services.decision_trail import RiskTrailEntry


def _entry() -> RiskTrailEntry:
    now = datetime(2026, 11, 10, tzinfo=UTC)
    return RiskTrailEntry(
        decision_id=uuid4(), kind="PRE_TRADE", outcome="ALLOW", reason_codes=("A", "B"),
        rule_version="v1", evaluated_at=now, expires_at=now, trace_id=uuid4(), summary="ok",
    )


def test_maps_risk_entry_fields() -> None:
    response = to_decision_trail_response([_entry()])
    assert response.entries[0].reason_codes == ["A", "B"]
    assert response.entries[0].kind == "PRE_TRADE"


def test_empty_trail() -> None:
    assert to_decision_trail_response([]).entries == []


def test_rejects_chronological_trail_entry() -> None:
    from src.services.decision_trail import TrailEntry, TrailKind

    chrono = TrailEntry(
        kind=TrailKind.RISK_DECISION, occurred_at=datetime(2026, 11, 10, tzinfo=UTC),
        actor=None, summary={}, evidence=None,
    )
    with pytest.raises((AttributeError, ValidationError)):
        to_decision_trail_response(cast("list[RiskTrailEntry]", [chrono]))
