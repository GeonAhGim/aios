"""18번 — 관리자 도구 API 요청/응답 스키마 커버리지."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.admin import (
    DisputeResolveRequest,
    DisputeSummary,
    SuspendSellerRequest,
    UserStatusChangeRequest,
    to_dispute_summary,
)


def test_user_status_change_request_valid() -> None:
    req = UserStatusChangeRequest(status="active")
    assert req.status == "active"


def test_user_status_change_request_missing_status_raises() -> None:
    with pytest.raises(ValidationError):
        UserStatusChangeRequest.model_validate({})


def test_user_status_change_request_invalid_type_raises() -> None:
    with pytest.raises(ValidationError):
        UserStatusChangeRequest(status=123)


def test_suspend_seller_request_missing_reason_raises() -> None:
    with pytest.raises(ValidationError):
        SuspendSellerRequest.model_validate({})


def test_suspend_seller_request_invalid_type_raises() -> None:
    with pytest.raises(ValidationError):
        SuspendSellerRequest(reason=["not-a-string"])


def test_dispute_resolve_request_missing_fields_raises() -> None:
    with pytest.raises(ValidationError):
        DisputeResolveRequest.model_validate({"decision": "approved"})


def test_dispute_resolve_request_valid() -> None:
    req = DisputeResolveRequest(decision="approved", reason="evidence sufficient")
    assert req.decision == "approved"
    assert req.reason == "evidence sufficient"


def test_to_dispute_summary_maps_fields() -> None:
    submitted_by = uuid4()
    resolved_by = uuid4()
    now = datetime.now(timezone.utc)
    row = {
        "id": 1,
        "purchase_id": 42,
        "submitted_by": submitted_by,
        "reason": "item not received",
        "status": "resolved",
        "resolution_decision": "refund",
        "resolution_reason": "confirmed",
        "resolved_by": resolved_by,
        "created_at": now,
        "resolved_at": now,
    }
    summary = to_dispute_summary(row)
    assert isinstance(summary, DisputeSummary)
    assert summary.id == 1
    assert summary.purchase_id == 42
    assert summary.submitted_by == submitted_by
    assert summary.resolution_decision == "refund"
    assert summary.resolved_by == resolved_by


def test_to_dispute_summary_optional_fields_none() -> None:
    row = {
        "id": 2,
        "purchase_id": 43,
        "submitted_by": uuid4(),
        "reason": "not shipped",
        "status": "open",
        "resolution_decision": None,
        "resolution_reason": None,
        "resolved_by": None,
        "created_at": datetime.now(timezone.utc),
        "resolved_at": None,
    }
    summary = to_dispute_summary(row)
    assert summary.resolution_decision is None
    assert summary.resolved_by is None
    assert summary.resolved_at is None


def test_to_dispute_summary_missing_required_field_raises() -> None:
    row = {
        "id": 3,
        "purchase_id": 44,
        "submitted_by": uuid4(),
        # "reason" intentionally omitted to inject a missing-field failure
        "status": "open",
        "resolution_decision": None,
        "resolution_reason": None,
        "resolved_by": None,
        "created_at": datetime.now(timezone.utc),
        "resolved_at": None,
    }
    with pytest.raises(ValidationError):
        to_dispute_summary(row)


def test_dispute_summary_invalid_uuid_raises() -> None:
    with pytest.raises(ValidationError):
        DisputeSummary(
            id=4,
            purchase_id=45,
            submitted_by="not-a-uuid",
            reason="x",
            status="open",
            resolution_decision=None,
            resolution_reason=None,
            resolved_by=None,
            created_at=datetime.now(timezone.utc),
            resolved_at=None,
        )
