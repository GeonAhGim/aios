"""Connected Asset API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

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
from src.foundation.connections.application.begin_connection import (
    ConsentRequiredError,
    ForbiddenCapabilityScopeError,
    MfaRequiredError,
    begin_connection,
)
from src.foundation.connections.application.confirm_connection import (
    ScopeVerificationFailedError,
    confirm_connection,
)
from src.foundation.connections.application.errors import (
    ConnectionNotFoundError,
    CrossTenantConnectionAccessError,
)
from src.foundation.connections.application.revoke_connection import (
    ConnectionNotRevocableError,
    revoke_connection,
)
from src.foundation.connections.application.sync_snapshot import (
    ConnectionNotSyncableError,
    ConnectionRevokedDuringSyncError,
    ProviderUnavailableError,
    sync_snapshot,
)
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
) -> ConnectionListResponse:
    view = await build_connection_list_view(repo, user.user_id)
    return ConnectionListResponse(connections=view.connections, as_of=view.as_of)


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_begin_connection(
    body: BeginConnectionRequest,
    user: User = Depends(get_current_user),
    repo: ConnectionRepository = Depends(get_connection_repository),
    trust_repo: TrustRepository = Depends(get_trust_repository),
) -> AccountConnectionView:
    try:
        return await begin_connection(
            repo,
            trust_repo,
            tenant_id=user.user_id,
            subject_id=user.user_id,
            mfa_verified=user.mfa_enabled,
            provider_code=body.provider_code,
            opaque_account_ref=body.opaque_account_ref,
            requested_capability_profile=[s.value for s in body.requested_capability_profile],
        )
    except MfaRequiredError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ForbiddenCapabilityScopeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ConsentRequiredError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post("/{connection_id}:confirm")
async def post_confirm_connection(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    repo: ConnectionRepository = Depends(get_connection_repository),
    provider: ReadonlyAccountProvider = Depends(get_readonly_account_provider),
    encryption_key: str = Depends(get_credential_encryption_key),
) -> AccountConnectionView:
    try:
        return await confirm_connection(
            repo,
            provider,
            tenant_id=user.user_id,
            connection_id=connection_id,
            encryption_key=encryption_key,
        )
    except (ConnectionNotFoundError, CrossTenantConnectionAccessError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 연결입니다.") from exc
    except ForbiddenCapabilityScopeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ScopeVerificationFailedError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "scope 확인에 실패했습니다.") from exc


@router.post("/{connection_id}:sync")
async def post_sync_connection(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    repo: ConnectionRepository = Depends(get_connection_repository),
    provider: ReadonlyAccountProvider = Depends(get_readonly_account_provider),
) -> AccountSnapshotView:
    try:
        return await sync_snapshot(
            repo, provider, tenant_id=user.user_id, connection_id=connection_id
        )
    except (ConnectionNotFoundError, CrossTenantConnectionAccessError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 연결입니다.") from exc
    except ConnectionNotSyncableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProviderUnavailableError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "provider를 사용할 수 없습니다.") from exc
    except ConnectionRevokedDuringSyncError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "연결이 이미 해지되어 동기화 결과를 저장하지 않았습니다."
        ) from exc


@router.post("/{connection_id}:revoke")
async def post_revoke_connection(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    repo: ConnectionRepository = Depends(get_connection_repository),
) -> AccountConnectionView:
    try:
        return await revoke_connection(repo, tenant_id=user.user_id, connection_id=connection_id)
    except (ConnectionNotFoundError, CrossTenantConnectionAccessError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 연결입니다.") from exc
    except ConnectionNotRevocableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
