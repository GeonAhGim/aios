"""FD-11.1/11.3/11.5/11.4 — 현재 사용자 조회/승인설정/화이트리스트/탈퇴 API 라우터.

Spec: 16_backend_signatures.md, 기능설계문서_v1.20.md#FD-11.3/FD-11.5/FD-11.4

화이트리스트 등록/탈퇴는 재인증이 필요하다(비밀번호+MFA) — 이미 로그인된
세션이라도 자금 이동 관련 민감 액션이라 별도로 다시 증명해야 한다.
AuthService.authenticate()를 그대로 재사용한다(로그인 가능 = 재인증
성공, 새 검증 로직을 만들지 않는다).
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.exception_mapping import ApprovalOwnershipError
from src.api.deps import get_auth_service, get_current_user, get_pool, reauthenticate
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
from src.api.schemas.auth import UserResponse, to_user_response
from src.api.service_deps import (
    get_account_deletion_service,
    get_approval_settings_service,
    get_withdrawal_whitelist_service,
)
from src.core.approval.service import ApprovalRequest, approve, list_pending, reject
from src.services.account_deletion_service import AccountDeletionService
from src.services.approval_settings_service import ApprovalSettingsService
from src.services.auth_service import AuthService, User
from src.services.withdrawal_whitelist_service import WithdrawalWhitelistService

router = APIRouter()


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)) -> ApiResponse[UserResponse]:
    return ok(to_user_response(user))


@router.get("/me/approval-settings")
async def get_approval_settings(
    user: User = Depends(get_current_user),
    service: ApprovalSettingsService = Depends(get_approval_settings_service),
) -> ApiResponse[ApprovalSettingsResponse]:
    settings = await service.get(user.user_id)
    return ok(to_approval_settings_response(settings))


@router.put("/me/approval-settings")
async def update_approval_settings(
    body: ApprovalSettingsRequest,
    user: User = Depends(get_current_user),
    service: ApprovalSettingsService = Depends(get_approval_settings_service),
) -> ApiResponse[ApprovalSettingsResponse]:
    settings = await service.update(
        user.user_id,
        mode=body.mode,
        second_approver_contact=body.second_approver_contact,
        risk_warning_acknowledged=body.risk_warning_acknowledged,
    )
    return ok(to_approval_settings_response(settings))


@router.get("/me/withdrawal-whitelist")
async def list_whitelist_entries(
    user: User = Depends(get_current_user),
    service: WithdrawalWhitelistService = Depends(get_withdrawal_whitelist_service),
) -> ApiResponse[list[WhitelistEntryResponse]]:
    entries = await service.list_for_user(user.user_id)
    return ok([to_whitelist_response(entry) for entry in entries])


@router.post("/me/withdrawal-whitelist", status_code=status.HTTP_201_CREATED)
async def register_whitelist_entry(
    body: WhitelistEntryRequest,
    user: User = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
    service: WithdrawalWhitelistService = Depends(get_withdrawal_whitelist_service),
) -> ApiResponse[WhitelistEntryResponse]:
    await reauthenticate(auth, user, body.password, body.totp_code)
    entry = await service.register(
        user.user_id,
        exchange=body.exchange,
        destination_address=body.destination_address,
        label=body.label,
    )
    return ok(to_whitelist_response(entry))


@router.post("/me/delete")
async def request_account_deletion(
    body: DeletionRequest,
    user: User = Depends(get_current_user),
    service: AccountDeletionService = Depends(get_account_deletion_service),
) -> ApiResponse[DeletionResponse]:
    result = await service.request_deletion(user.user_id, body.password)
    return ok(
        DeletionResponse(status=result.status, deletion_effective_at=result.deletion_effective_at)
    )


@router.get("/me/approval-requests")
async def list_my_approval_requests(
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[list[ApprovalRequest]]:
    return ok(await list_pending(pool, user_id=user.user_id))


async def _require_own_request(pool: asyncpg.Pool, request_id: int, user: User) -> None:
    """자기 소유 PENDING 요청인지 확인 — approval/service.py 모듈 docstring
    참조(DUAL 두 번째 서명자는 신원 해석이 없어 self-service 대상이 아님,
    이 검사가 그 경로를 자연히 차단한다: request.user_id는 항상 요청자
    본인이라 다른 계정으로는 이 목록에 잡히지 않는다)."""
    own_pending = await list_pending(pool, user_id=user.user_id)
    if not any(r.id == request_id for r in own_pending):
        raise ApprovalOwnershipError("본인의 승인 요청만 처리할 수 있습니다.")


@router.post("/me/approval-requests/{request_id}/approve")
async def approve_my_request(
    request_id: int,
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[ApprovalRequest]:
    await _require_own_request(pool, request_id, user)
    return ok(await approve(pool, request_id, user.user_id))


@router.post("/me/approval-requests/{request_id}/reject")
async def reject_my_request(
    request_id: int,
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[ApprovalRequest]:
    await _require_own_request(pool, request_id, user)
    return ok(await reject(pool, request_id, user.user_id))
