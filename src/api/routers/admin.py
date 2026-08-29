"""18번 — 관리자 도구 API 라우터 (FD-18.1~18.5, FD-10.1 승인 결정).

Spec: 기능설계문서_v1.20.md#FD-18.1~FD-18.5, FD-10.1

FD-18.1(검증 대기열 조회)은 운영자가 아니라 검증담당자(is_verifier)
권한이라 이 라우터 안에서도 get_current_verifier를 쓴다 — 나머지
엔드포인트는 전부 get_current_admin(운영자 전용).

16번(실행 제어판) leaf에서 미룬 FD-10.1 승인 결정(approve/reject) HTTP
엔드포인트를 여기서 채운다 — LIVE 실행 시작에 필요한 승인은 운영자
액션이라 이 위치가 맞다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, status

from src.api.admin_deps import (
    get_dispute_resolution_service,
    get_seller_suspension_service,
    get_user_admin_service,
    get_verification_queue_service,
)
from src.api.deps import get_current_admin, get_current_verifier, get_pool
from src.api.marketplace_deps import get_listing_service
from src.api.schemas.admin import (
    DisputeResolveRequest,
    DisputeSummary,
    SuspendSellerRequest,
    UserStatusChangeRequest,
    to_dispute_summary,
)
from src.api.schemas.marketplace import (
    ListingResponse,
    PlatformListingCreateRequest,
    to_listing_response,
)
from src.api.service_deps import get_wallet_service
from src.core.approval.service import ApprovalError, ApprovalRequest, approve, reject
from src.services.auth_service import User
from src.services.dispute_resolution_service import (
    DisputeDetail,
    DisputeResolutionError,
    DisputeResolutionResult,
    DisputeResolutionService,
)
from src.services.listing_service import ListingError, ListingService
from src.services.seller_suspension_service import (
    SellerSuspensionError,
    SellerSuspensionResult,
    SellerSuspensionService,
)
from src.services.user_admin_service import (
    UserAdminError,
    UserAdminService,
    UserStatusChangeResult,
    UserSummary,
)
from src.services.verification_queue_service import QueuedListing, VerificationQueueService
from src.services.wallet_service import (
    WalletService,
    WalletTopupConfirmResult,
    WalletTopupError,
    WalletTopupPage,
)

router = APIRouter(prefix="/admin")


@router.get("/verification-queue")
async def get_verification_queue(
    verifier: User = Depends(get_current_verifier),
    service: VerificationQueueService = Depends(get_verification_queue_service),
) -> list[QueuedListing]:
    return await service.list_pending(verifier.user_id)


@router.get("/disputes")
async def list_disputes(
    dispute_status: str | None = None,
    admin: User = Depends(get_current_admin),
    service: DisputeResolutionService = Depends(get_dispute_resolution_service),
) -> list[DisputeSummary]:
    rows = await service.list_disputes(dispute_status)
    return [to_dispute_summary(row) for row in rows]


@router.get("/disputes/{dispute_id}")
async def get_dispute(
    dispute_id: int,
    admin: User = Depends(get_current_admin),
    service: DisputeResolutionService = Depends(get_dispute_resolution_service),
) -> DisputeDetail:
    try:
        return await service.get_detail(dispute_id)
    except DisputeResolutionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/disputes/{dispute_id}/resolve")
async def resolve_dispute(
    dispute_id: int,
    body: DisputeResolveRequest,
    admin: User = Depends(get_current_admin),
    service: DisputeResolutionService = Depends(get_dispute_resolution_service),
) -> DisputeResolutionResult:
    try:
        return await service.resolve(dispute_id, admin.user_id, body.decision, body.reason)
    except DisputeResolutionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/users")
async def list_users(
    email_search: str | None = None,
    admin: User = Depends(get_current_admin),
    service: UserAdminService = Depends(get_user_admin_service),
) -> list[UserSummary]:
    return await service.list_users(email_search)


@router.patch("/users/{user_id}/status")
async def change_user_status(
    user_id: UUID,
    body: UserStatusChangeRequest,
    admin: User = Depends(get_current_admin),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserStatusChangeResult:
    try:
        return await service.change_status(user_id, body.status)
    except UserAdminError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/users/{user_id}/suspend-seller")
async def suspend_seller(
    user_id: UUID,
    body: SuspendSellerRequest,
    admin: User = Depends(get_current_admin),
    service: SellerSuspensionService = Depends(get_seller_suspension_service),
) -> SellerSuspensionResult:
    try:
        return await service.suspend(user_id, admin.user_id, body.reason)
    except SellerSuspensionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/wallet/topups/pending")
async def list_pending_topups(
    page: int = 1,
    page_size: int = 20,
    admin: User = Depends(get_current_admin),
    service: WalletService = Depends(get_wallet_service),
) -> WalletTopupPage:
    return await service.list_pending_topups(page=page, page_size=page_size)


@router.post("/wallet/topups/{topup_id}/confirm")
async def confirm_topup(
    topup_id: int,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    admin: User = Depends(get_current_admin),
    service: WalletService = Depends(get_wallet_service),
) -> WalletTopupConfirmResult:
    try:
        return await service.confirm_topup(
            topup_id, admin.user_id, idempotency_key=idempotency_key
        )
    except WalletTopupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/marketplace/platform-listings", status_code=status.HTTP_201_CREATED)
async def create_platform_listing(
    body: PlatformListingCreateRequest,
    admin: User = Depends(get_current_admin),
    service: ListingService = Depends(get_listing_service),
) -> ListingResponse:
    try:
        listing = await service.create_platform_listing(
            body.strategy_id, body.strategy_version, body.price
        )
    except ListingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return to_listing_response(listing)


@router.post("/approval-requests/{request_id}/approve")
async def approve_request(
    request_id: int,
    admin: User = Depends(get_current_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApprovalRequest:
    try:
        return await approve(pool, request_id, admin.user_id)
    except ApprovalError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/approval-requests/{request_id}/reject")
async def reject_request(
    request_id: int,
    admin: User = Depends(get_current_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApprovalRequest:
    try:
        return await reject(pool, request_id, admin.user_id)
    except ApprovalError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
