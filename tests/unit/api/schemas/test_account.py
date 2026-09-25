"""FD-11.4/11.5/11.6 account 스키마 — 요청/응답 모델 및 변환 함수 커버리지."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.account import (
    ApprovalSettingsRequest,
    ApprovalSettingsResponse,
    DeletionRequest,
    DeletionResponse,
    WhitelistEntryRequest,
    WhitelistEntryResponse,
    to_approval_settings_response,
    to_whitelist_response,
)
from src.services.approval_settings_service import ApprovalSettings
from src.services.withdrawal_whitelist_service import WithdrawalWhitelistEntry


def test_approval_settings_request_defaults() -> None:
    req = ApprovalSettingsRequest(mode="SOLO")
    assert req.second_approver_contact is None
    assert req.risk_warning_acknowledged is False


def test_approval_settings_request_invalid_mode_type_raises() -> None:
    with pytest.raises(ValidationError):
        ApprovalSettingsRequest(mode=123)


def test_approval_settings_request_invalid_ack_type_raises() -> None:
    with pytest.raises(ValidationError):
        ApprovalSettingsRequest(mode="DUAL", risk_warning_acknowledged="not-a-bool")


def test_approval_settings_request_missing_mode_raises() -> None:
    with pytest.raises(ValidationError):
        ApprovalSettingsRequest.model_validate({})


def test_to_approval_settings_response_maps_fields() -> None:
    settings = ApprovalSettings(
        user_id=uuid4(),
        mode="DUAL",
        second_approver_contact="approver@example.com",
        mandatory_wait_seconds=120,
        risk_warning="경고",
    )
    resp = to_approval_settings_response(settings)
    assert isinstance(resp, ApprovalSettingsResponse)
    assert resp.mode == "DUAL"
    assert resp.second_approver_contact == "approver@example.com"
    assert resp.mandatory_wait_seconds == 120
    assert resp.risk_warning == "경고"


def test_to_approval_settings_response_missing_attr_raises() -> None:
    class _Broken:
        mode = "SOLO"
        second_approver_contact = None
        # mandatory_wait_seconds intentionally absent to inject a dependency failure
        risk_warning = None

    with pytest.raises(AttributeError):
        to_approval_settings_response(cast(ApprovalSettings, _Broken()))


def test_whitelist_entry_request_missing_password_raises() -> None:
    with pytest.raises(ValidationError):
        WhitelistEntryRequest.model_validate(
            {
                "exchange": "bitget",
                "destination_address": "0xabc",
            }
        )


def test_whitelist_entry_request_invalid_exchange_type_raises() -> None:
    with pytest.raises(ValidationError):
        WhitelistEntryRequest(
            exchange=["bitget"],
            destination_address="0xabc",
            password="pw",
        )


def test_whitelist_entry_request_optional_defaults() -> None:
    req = WhitelistEntryRequest(
        exchange="bitget",
        destination_address="0xabc",
        password="pw",
    )
    assert req.label is None
    assert req.totp_code is None


def test_to_whitelist_response_maps_fields() -> None:
    entry = WithdrawalWhitelistEntry(
        id=7,
        exchange="bitget",
        destination_address="0xabc",
        label="main",
    )
    resp = to_whitelist_response(entry)
    assert isinstance(resp, WhitelistEntryResponse)
    assert resp.id == 7
    assert resp.exchange == "bitget"
    assert resp.destination_address == "0xabc"
    assert resp.label == "main"


def test_whitelist_entry_response_invalid_id_type_raises() -> None:
    with pytest.raises(ValidationError):
        WhitelistEntryResponse(
            id="not-an-int",
            exchange="bitget",
            destination_address="0xabc",
            label=None,
        )


def test_deletion_request_missing_password_raises() -> None:
    with pytest.raises(ValidationError):
        DeletionRequest.model_validate({})


def test_deletion_response_invalid_datetime_raises() -> None:
    with pytest.raises(ValidationError):
        DeletionResponse(status="scheduled", deletion_effective_at="not-a-datetime")


def test_deletion_response_valid_construction() -> None:
    now = datetime.now(timezone.utc)
    resp = DeletionResponse(status="scheduled", deletion_effective_at=now)
    assert resp.status == "scheduled"
    assert resp.deletion_effective_at == now
