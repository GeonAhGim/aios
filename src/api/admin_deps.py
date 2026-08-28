"""18번대 — 관리자 도구 서비스 팩토리 의존성."""
from __future__ import annotations

import asyncpg
from fastapi import Depends

from src.services.dispute_resolution_service import DisputeResolutionService
from src.services.payment_confirmation_service import PaymentConfirmationService
from src.services.seller_suspension_service import SellerSuspensionService
from src.services.user_admin_service import UserAdminService
from src.services.verification_queue_service import VerificationQueueService

from .deps import get_pool


def get_verification_queue_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> VerificationQueueService:
    return VerificationQueueService(pool)


def get_dispute_resolution_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> DisputeResolutionService:
    return DisputeResolutionService(pool)


def get_user_admin_service(pool: asyncpg.Pool = Depends(get_pool)) -> UserAdminService:
    return UserAdminService(pool)


def get_seller_suspension_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> SellerSuspensionService:
    return SellerSuspensionService(pool)


def get_payment_confirmation_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> PaymentConfirmationService:
    return PaymentConfirmationService(pool)
