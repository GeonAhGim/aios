"""Connected Asset API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다.

도메인 예외는 여기서 잡지 않는다 — `src/api/contracts/exception_mapping.py`의
`EXCEPTION_MAP`이 전역 핸들러에서 봉투로 번역한다(§9 PLT-21 decision, task-1108).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_user
from src.api.foundation_deps import (
    get_connection_repository,
    get_credential_encryption_key,
    get_readonly_account_provider,
    get_trust_repository,
)
from src.api.schemas.foundation.connections import (
    AccountConnectionView,
    AccountSnapshotView,
    BeginConnectionRequest,
    ConnectionListResponse,
)
from src.foundation.connections.application.begin_connection import begin_connection
from src.foundation.connections.application.confirm_connection import confirm_connection
from src.foundation.connections.application.revoke_connection import revoke_connection
from src.foundation.connections.application.sync_snapshot import sync_snapshot
from src.foundation.connections.ports.provider import ReadonlyAccountProvider
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.connections.projections import build_connection_list_view
from src.foundation.trust.ports.repository import TrustRepository
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/connections", tags=["foundation:connections"])


@router.get("")
async def list_connections(
    user: User = Depends(get_current_user),
    repo: ConnectionRepository = Depends(get_connection_repository),
) -> ApiResponse[ConnectionListResponse]:
    view = await build_connection_list_view(repo, user.user_id)
    return ok(ConnectionListResponse(connections=view.connections, as_of=view.as_of))


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_begin_connection(
    body: BeginConnectionRequest,
    user: User = Depends(get_current_user),
    repo: ConnectionRepository = Depends(get_connection_repository),
    trust_repo: TrustRepository = Depends(get_trust_repository),
) -> ApiResponse[AccountConnectionView]:
    result = await begin_connection(
        repo,
        trust_repo,
        tenant_id=user.user_id,
        subject_id=user.user_id,
        mfa_verified=user.mfa_enabled,
        provider_code=body.provider_code,
        opaque_account_ref=body.opaque_account_ref,
        requested_capability_profile=[s.value for s in body.requested_capability_profile],
    )
    return ok(result)


@router.post("/{connection_id}:confirm")
async def post_confirm_connection(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    repo: ConnectionRepository = Depends(get_connection_repository),
    provider: ReadonlyAccountProvider = Depends(get_readonly_account_provider),
    encryption_key: str = Depends(get_credential_encryption_key),
) -> ApiResponse[AccountConnectionView]:
    result = await confirm_connection(
        repo,
        provider,
        tenant_id=user.user_id,
        connection_id=connection_id,
        encryption_key=encryption_key,
    )
    return ok(result)


@router.post("/{connection_id}:sync")
async def post_sync_connection(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    repo: ConnectionRepository = Depends(get_connection_repository),
    provider: ReadonlyAccountProvider = Depends(get_readonly_account_provider),
) -> ApiResponse[AccountSnapshotView]:
    result = await sync_snapshot(
        repo, provider, tenant_id=user.user_id, connection_id=connection_id
    )
    return ok(result)


@router.post("/{connection_id}:revoke")
async def post_revoke_connection(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    repo: ConnectionRepository = Depends(get_connection_repository),
) -> ApiResponse[AccountConnectionView]:
    result = await revoke_connection(repo, tenant_id=user.user_id, connection_id=connection_id)
    return ok(result)
