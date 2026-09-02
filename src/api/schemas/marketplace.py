"""13번 — 마켓플레이스 API 요청·응답 스키마."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from src.services.dispute_service import Dispute
from src.services.listing_search_service import ListingSummary
from src.services.listing_service import Listing
from src.services.purchase_service import PurchaseResult
from src.services.review_service import Review


class ListingCreateRequest(BaseModel):
    strategy_id: str
    strategy_version: str
    # 전수감사(docs/FULL_AUDIT_2026-09-02.md §2) 반영 — 음수 가격은 구매 시
    # wallet_service.debit()의 `balance >= amount` 조건을 항상 통과시켜 구매자
    # 지갑을 늘리는 경로가 된다. 스키마·서비스·DB CHECK 세 겹으로 막는다.
    price: Decimal | None = Field(default=None, ge=0)


class ListingResponse(BaseModel):
    id: int
    strategy_id: str
    strategy_version: str
    seller_user_id: str
    seller_type: str
    price: Decimal | None
    status: str
    created_at: datetime


def to_listing_response(listing: Listing) -> ListingResponse:
    return ListingResponse(
        id=listing.id,
        strategy_id=listing.strategy_id,
        strategy_version=listing.strategy_version,
        seller_user_id=str(listing.seller_user_id),
        seller_type=listing.seller_type,
        price=listing.price,
        status=listing.status,
        created_at=listing.created_at,
    )


class PlatformListingCreateRequest(BaseModel):
    strategy_id: str
    strategy_version: str
    price: Decimal | None = Field(default=None, ge=0)


class ListingSearchResponse(BaseModel):
    items: list[ListingSummary]
    total: int
    page: int
    page_size: int


class VerificationDecisionRequest(BaseModel):
    decision: str
    rejection_reason: str | None = None


class PurchaseCreateRequest(BaseModel):
    risk_warning_acknowledged: bool = False


class PurchaseResponse(BaseModel):
    purchase_id: int
    status: str
    risk_warning: bool = False
    risk_warning_reason: str | None = None
    platform_commission_amount: Decimal | None = None
    seller_payout_amount: Decimal | None = None


def to_purchase_response(result: PurchaseResult) -> PurchaseResponse:
    return PurchaseResponse(
        purchase_id=result.purchase_id,
        status=result.status,
        risk_warning=result.risk_warning is not None,
        risk_warning_reason=result.risk_warning,
        platform_commission_amount=result.platform_commission_amount,
        seller_payout_amount=result.seller_payout_amount,
    )


class ReviewCreateRequest(BaseModel):
    rating: int
    comment: str | None = None


class ReviewResponse(BaseModel):
    review_id: int
    listing_id: int
    rating: int
    comment: str | None
    created_at: datetime


def to_review_response(review: Review) -> ReviewResponse:
    return ReviewResponse(
        review_id=review.id,
        listing_id=review.listing_id,
        rating=review.rating,
        comment=review.comment,
        created_at=review.created_at,
    )


class DisputeCreateRequest(BaseModel):
    purchase_id: int
    reason: str


class DisputeResponse(BaseModel):
    dispute_id: int
    status: str
    created_at: datetime


def to_dispute_response(dispute: Dispute) -> DisputeResponse:
    return DisputeResponse(
        dispute_id=dispute.id, status=dispute.status, created_at=dispute.created_at
    )
