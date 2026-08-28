"""12.1~12.4 — 거래소 자격증명 API 요청·응답 스키마."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.services.exchange_credential_service import CredentialSummary


class CredentialRequest(BaseModel):
    exchange: str
    api_key: str
    api_secret: str
    api_passphrase: str | None = None  # Bitget 전용
    cano: str | None = None  # KIS 전용
    acnt_prdt_cd: str | None = None  # KIS 전용


class CredentialResponse(BaseModel):
    id: int
    exchange: str
    is_active: bool
    linked_at: datetime
    withdrawal_permission_warning: str | None = None


def to_credential_response(summary: CredentialSummary) -> CredentialResponse:
    return CredentialResponse(
        id=summary.id,
        exchange=summary.exchange,
        is_active=summary.is_active,
        linked_at=summary.linked_at,
        withdrawal_permission_warning=summary.withdrawal_permission_warning,
    )


def request_to_extra(body: CredentialRequest) -> dict[str, str]:
    extra: dict[str, str] = {}
    if body.api_passphrase is not None:
        extra["api_passphrase"] = body.api_passphrase
    if body.cano is not None:
        extra["cano"] = body.cano
    if body.acnt_prdt_cd is not None:
        extra["acnt_prdt_cd"] = body.acnt_prdt_cd
    return extra
