"""11.4/11.5/11.6 — 승인설정/화이트리스트/탈퇴 API 요청·응답 스키마."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.services.approval_settings_service import ApprovalSettings
from src.services.withdrawal_whitelist_service import WithdrawalWhitelistEntry


class ApprovalSettingsRequest(BaseModel):
    mode: str
    second_approver_contact: str | None = None
    risk_warning_acknowledged: bool = False


class ApprovalSettingsResponse(BaseModel):
    mode: str
    second_approver_contact: str | None
    mandatory_wait_seconds: int
    risk_warning: str | None = None


def to_approval_settings_response(settings: ApprovalSettings) -> ApprovalSettingsResponse:
    return ApprovalSettingsResponse(
        mode=settings.mode,
        second_approver_contact=settings.second_approver_contact,
        mandatory_wait_seconds=settings.mandatory_wait_seconds,
        risk_warning=settings.risk_warning,
    )


class WhitelistEntryRequest(BaseModel):
    exchange: str
    destination_address: str
    label: str | None = None
    password: str
    totp_code: str | None = None


class WhitelistEntryResponse(BaseModel):
    id: int
    exchange: str
    destination_address: str
    label: str | None


def to_whitelist_response(entry: WithdrawalWhitelistEntry) -> WhitelistEntryResponse:
    return WhitelistEntryResponse(
        id=entry.id,
        exchange=entry.exchange,
        destination_address=entry.destination_address,
        label=entry.label,
    )


class DeletionRequest(BaseModel):
    password: str


class DeletionResponse(BaseModel):
    status: str
    deletion_effective_at: datetime
