"""12.1~12.4 — 거래소 자격증명 API 라우터.

Spec: 기능설계문서_v1.20.md#FD-13.3(개발명세서), FD-12.2, 16_backend_signatures.md

편차: 16_backend_signatures.md Draft는 해지를 credential_id 경로
파라미터로 받는 것으로 스케치했지만, 실제 구현된
ExchangeCredentialService.revoke()는 UNIQUE(user_id, exchange) 제약을
그대로 이용해 (user_id, exchange) 조합으로 해지한다(한 사용자당 거래소
하나에 활성 자격증명은 항상 1개뿐이라 credential_id와 동등) — 여기서는
exchange 경로 파라미터로 노출한다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user
from src.api.schemas.exchange import (
    CredentialRequest,
    CredentialResponse,
    request_to_extra,
    to_credential_response,
)
from src.api.service_deps import get_credential_resolver, get_exchange_credential_service
from src.data.models.trading import AccountBalance, Position
from src.exchanges.common.types import ExchangeCapability
from src.services.auth_service import User
from src.services.credential_resolver import CredentialNotFoundError, CredentialResolver
from src.services.exchange_credential_service import (
    ExchangeCredentialError,
    ExchangeCredentialService,
)

router = APIRouter()


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_credential(
    body: CredentialRequest,
    user: User = Depends(get_current_user),
    service: ExchangeCredentialService = Depends(get_exchange_credential_service),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> CredentialResponse:
    try:
        summary = await service.register(
            user.user_id,
            body.exchange,
            body.api_key,
            body.api_secret,
            extra=request_to_extra(body),
        )
    except ExchangeCredentialError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # 레드팀 감사(docs/RED_TEAM_FINDINGS.md #02) 반영 — 캐시가 실제로 살아있는
    # 싱글턴이 된 이상, 재등록 직후 TTL이 끝나지 않은 옛 자격증명으로 만든
    # 어댑터가 계속 쓰이지 않도록 반드시 함께 지워야 한다.
    resolver.invalidate(user.user_id, body.exchange)
    return to_credential_response(summary)


@router.delete("/{exchange}")
async def revoke_credential(
    exchange: str,
    user: User = Depends(get_current_user),
    service: ExchangeCredentialService = Depends(get_exchange_credential_service),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> dict[str, str]:
    try:
        await service.revoke(user.user_id, exchange)
    except ExchangeCredentialError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    resolver.invalidate(user.user_id, exchange)
    return {"exchange": exchange, "status": "revoked"}


@router.get("")
async def list_credentials(
    user: User = Depends(get_current_user),
    service: ExchangeCredentialService = Depends(get_exchange_credential_service),
) -> list[CredentialResponse]:
    summaries = await service.list_for_user(user.user_id)
    return [to_credential_response(s) for s in summaries]


@router.get("/{exchange}/balance")
async def get_balance(
    exchange: str,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> list[AccountBalance]:
    try:
        adapter = await resolver.get_adapter(user.user_id, exchange)
    except CredentialNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return await adapter.get_balance()


@router.get("/{exchange}/positions")
async def get_positions(
    exchange: str,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> list[Position]:
    try:
        adapter = await resolver.get_adapter(user.user_id, exchange)
    except CredentialNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return await adapter.get_positions()


@router.get("/{exchange}/capabilities")
async def get_capabilities(
    exchange: str,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> ExchangeCapability:
    try:
        adapter = await resolver.get_adapter(user.user_id, exchange)
    except CredentialNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return adapter.get_capabilities()
