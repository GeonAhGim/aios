"""12.1~12.4 — 거래소 자격증명 API 라우터.

Spec: 기능설계문서_v1.20.md#FD-13.3(개발명세서), FD-12.2, 16_backend_signatures.md

편차: 16_backend_signatures.md Draft는 해지를 credential_id 경로
파라미터로 받는 것으로 스케치했지만, 실제 구현된
ExchangeCredentialService.revoke()는 UNIQUE(user_id, exchange) 제약을
그대로 이용해 (user_id, exchange) 조합으로 해지한다(한 사용자당 거래소
하나에 활성 자격증명은 항상 1개뿐이라 credential_id와 동등) — 여기서는
exchange 경로 파라미터로 노출한다.

PLT-17 decision(needs_decision): 이 라우터의 성공 응답은 아직 `ApiResponse`
봉투로 감싸지 않는다 — L4 spec §2.3(line 307)은 이 변경을 MAJOR로 분류하고
"/api/v1 경로에만 적용, 레거시 alias는 구형 그대로 반환"하라고 명시하지만
`mount_v1`(src/api/versioning.py, PLT-16)이 아직 `src/main.py`에 배선되지
않아 이 라우터는 지금 legacy 단일 경로(`/exchange-credentials`)로만
노출된다. 여기서 감싸면 `contracts/openapi/v1.json` 베이스라인 대비
MAJOR 위반(18건, 응답 property 제거)이 발생해 `check_openapi_compat.py`가
FAIL한다 — PM 결정(mount_v1 배선 우선순위 vs MAJOR 승인) 대기 중이며
task-1002 note에 기록했다. raw HTTPException 제거(이 리프의 핵심 DoD)는
이 보류와 무관하게 완료했다 — 도메인 예외가 동일한 상태코드/error_code로
매핑되므로 에러 응답 모양은 이번 변경 전후로 동일하다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

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
from src.services.credential_resolver import CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService

router = APIRouter()


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_credential(
    body: CredentialRequest,
    user: User = Depends(get_current_user),
    service: ExchangeCredentialService = Depends(get_exchange_credential_service),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> CredentialResponse:
    summary = await service.register(
        user.user_id,
        body.exchange,
        body.api_key,
        body.api_secret,
        extra=request_to_extra(body),
    )
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
    await service.revoke(user.user_id, exchange)
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
    adapter = await resolver.get_adapter(user.user_id, exchange)
    return await adapter.get_balance()


@router.get("/{exchange}/positions")
async def get_positions(
    exchange: str,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> list[Position]:
    adapter = await resolver.get_adapter(user.user_id, exchange)
    return await adapter.get_positions()


@router.get("/{exchange}/capabilities")
async def get_capabilities(
    exchange: str,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> ExchangeCapability:
    adapter = await resolver.get_adapter(user.user_id, exchange)
    return adapter.get_capabilities()
