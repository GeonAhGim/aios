"""13번 — 마켓플레이스 API 라우터.

Spec: 기능설계문서_v1.20.md#FD-13.1~FD-13.10, 16_backend_signatures.md

구매(purchase)는 15번 §15.1 Idempotency-Key 원칙 적용 대상(금전 관련
POST) — src/core/idempotency.py로 동일 키 재요청 시 중복 구매를
만들지 않는다.

PLT-18 — raw `HTTPException` raise를 전부 도메인 예외로 이관했다(§9
PLT-17~21). 도메인 예외 → ErrorCode 매핑은 src/api/contracts/
exception_mapping.py EXCEPTION_MAP/STATUS_OVERRIDE, 전역 핸들러는
src/api/contracts/handlers.py. `InsufficientWalletBalanceError`(402)는
ErrorCode 자체는 POLICY_DENIED를 쓰되 상태코드만 STATUS_OVERRIDE로
402를 고정한다 — 기존 라우터 테스트(test_marketplace_router.py)가
정확히 402를 기대하는데, error_codes.py taxonomy 접두 화이트리스트에
결제 전용 코드가 없기 때문이다.

이 리프도 PLT-17과 동일하게 성공 응답의 `ApiResponse` 봉투화는 보류한다
— `/marketplace/*`가 아직 legacy 단일 경로라 감싸면 openapi 스냅샷
MAJOR 위반이 난다(PM 선반영 decision, task-1009).
"""
from __future__ import annotations

from decimal import Decimal

import asyncpg
from fastapi import APIRouter, Depends, Header, status

from src.api.deps import get_current_user, get_current_verifier, get_pool
from src.api.marketplace_deps import (
    get_dispute_service,
    get_listing_search_service,
    get_listing_service,
    get_purchase_service,
    get_review_service,
    get_strategy_access_service,
    get_verification_service,
)
from src.api.schemas.marketplace import (
    DisputeCreateRequest,
    DisputeResponse,
    ListingCreateRequest,
    ListingResponse,
    ListingSearchResponse,
    PurchaseCreateRequest,
    PurchaseResponse,
    ReviewCreateRequest,
    ReviewResponse,
    VerificationDecisionRequest,
    to_dispute_response,
    to_listing_response,
    to_purchase_response,
    to_review_response,
)
from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.idempotency import with_idempotency
from src.services.auth_service import User
from src.services.dispute_service import DisputeService
from src.services.listing_search_service import ListingSearchService
from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseService
from src.services.review_service import ReviewService
from src.services.strategy_access_service import StrategyAccessService
from src.services.verification_service import VerificationService

router = APIRouter()


@router.post("/listings", status_code=status.HTTP_201_CREATED)
async def create_listing(
    body: ListingCreateRequest,
    user: User = Depends(get_current_user),
    service: ListingService = Depends(get_listing_service),
) -> ListingResponse:
    listing = await service.create_listing(
        user.user_id, body.strategy_id, body.strategy_version, body.price
    )
    return to_listing_response(listing)


@router.get("/listings")
async def list_listings(
    asset_class: str | None = None,
    exchange: str | None = None,
    max_price: Decimal | None = None,
    sort_by: str = "RECOMMENDED",
    page: int = 1,
    page_size: int = 20,
    service: ListingSearchService = Depends(get_listing_search_service),
) -> ListingSearchResponse:
    result = await service.search(
        asset_class=asset_class,
        exchange=exchange,
        max_price=max_price,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )
    return ListingSearchResponse(
        items=result.items, total=result.total, page=result.page, page_size=result.page_size
    )


@router.post("/listings/{listing_id}/submit-verification")
async def submit_for_verification(
    listing_id: int,
    user: User = Depends(get_current_user),
    service: ListingService = Depends(get_listing_service),
) -> ListingResponse:
    listing = await service.submit_for_verification(listing_id, user.user_id)
    return to_listing_response(listing)


@router.post("/listings/{listing_id}/verify")
async def verify_listing(
    listing_id: int,
    body: VerificationDecisionRequest,
    verifier: User = Depends(get_current_verifier),
    service: VerificationService = Depends(get_verification_service),
) -> dict[str, str | int | None]:
    result = await service.decide(
        listing_id,
        verifier.user_id,
        body.decision,
        rejection_reason=body.rejection_reason,
    )
    return {
        "listing_id": result.listing_id,
        "status": result.status,
        "rejection_reason": result.rejection_reason,
    }


@router.post("/listings/{listing_id}/purchase", status_code=status.HTTP_201_CREATED)
async def purchase_listing(
    listing_id: int,
    body: PurchaseCreateRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    service: PurchaseService = Depends(get_purchase_service),
) -> PurchaseResponse:
    async def compute() -> tuple[int, dict[str, object]]:
        # PurchaseError/InsufficientWalletBalanceError는 여기서 잡지 않고
        # 그대로 던진다 — with_idempotency는 compute()가 예외를 던지면
        # 선점 행을 지우고(캐시하지 않음) 그대로 재전파하므로(core/idempotency.py
        # docstring), 전역 핸들러가 도메인 예외를 봉투로 변환하는 경로와
        # 자연히 합쳐진다.
        result = await service.purchase(
            user.user_id,
            listing_id,
            risk_warning_acknowledged=body.risk_warning_acknowledged,
        )
        return status.HTTP_201_CREATED, to_purchase_response(result).model_dump(mode="json")

    # 전수감사(docs/FULL_AUDIT_2026-09-02.md §2) 반영 — 키에 사용자 ID를 넣어
    # 스코프를 사용자 단위로 고정한다. 헤더값만으로 키를 만들면 다른 사용자가
    # 같은 값을 보냈을 때 남의 구매 응답(purchase_id·정산액)을 그대로 돌려받는다.
    status_code, response_body = await with_idempotency(
        pool, f"purchase:{user.user_id}:{idempotency_key}", compute
    )
    if status_code != status.HTTP_201_CREATED:
        # compute()는 이제 성공(201) 외에는 예외로만 실패하므로, 여기 남는
        # 유일한 경로는 with_idempotency 자체가 만드는 409(동시 처리 중) —
        # compute()를 아예 실행하지 않고 반환한 값이라 예외로 표현되지
        # 않는다. STATE_CONCURRENCY_CONFLICT(409)는 이미 존재하는 코드라
        # 재사용한다.
        raise ConcurrencyConflictError(
            str(response_body.get("detail", "동시 처리 충돌이 발생했습니다."))
        )
    return PurchaseResponse(**response_body)


@router.get("/strategies/{strategy_id}/{strategy_version}")
async def get_strategy_definition(
    strategy_id: str,
    strategy_version: str,
    user: User = Depends(get_current_user),
    service: StrategyAccessService = Depends(get_strategy_access_service),
) -> dict[str, object]:
    """10.3-B 블랙박스 원칙 — 소유자이거나 결제확인된 구매자만 접근 가능."""
    definition = await service.get_strategy_for_execution(
        user.user_id, strategy_id, strategy_version
    )
    return definition.model_dump(mode="json")


@router.post("/listings/{listing_id}/reviews", status_code=status.HTTP_201_CREATED)
async def create_review(
    listing_id: int,
    body: ReviewCreateRequest,
    user: User = Depends(get_current_user),
    service: ReviewService = Depends(get_review_service),
) -> ReviewResponse:
    review = await service.create_review(
        user.user_id, listing_id, body.rating, comment=body.comment
    )
    return to_review_response(review)


@router.get("/listings/{listing_id}/reviews")
async def list_reviews(
    listing_id: int, service: ReviewService = Depends(get_review_service)
) -> dict[str, object]:
    reviews = await service.list_reviews(listing_id)
    summary = await service.get_rating_summary(listing_id)
    return {
        "reviews": [to_review_response(r).model_dump(mode="json") for r in reviews],
        "review_count": summary.review_count,
        "average_rating": summary.average_rating,
    }


@router.post("/disputes", status_code=status.HTTP_201_CREATED)
async def submit_dispute(
    body: DisputeCreateRequest,
    user: User = Depends(get_current_user),
    service: DisputeService = Depends(get_dispute_service),
) -> DisputeResponse:
    dispute = await service.submit(user.user_id, body.purchase_id, body.reason)
    return to_dispute_response(dispute)
