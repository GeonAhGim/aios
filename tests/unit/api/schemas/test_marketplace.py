"""TEST-cov(task-4616): src/api/schemas/marketplace.py coverage.

Covers request/response schema validation (incl. negative/boundary cases per
docs/FULL_AUDIT_2026-09-02.md §2 negative-price rule) and the to_*_response
converters against their upstream service models.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.marketplace import (
    DisputeCreateRequest,
    ListingCreateRequest,
    ListingResponse,
    ListingSearchResponse,
    PlatformListingCreateRequest,
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
from src.services.dispute_service import Dispute
from src.services.listing_service import Listing
from src.services.purchase_service import PurchaseResult
from src.services.review_service import Review

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _call_untyped(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke `fn` outside mypy's static arg checking.

    These negative tests deliberately pass missing/wrong-typed arguments to
    assert pydantic's runtime validation rejects them — routing the call
    through `Any` avoids a `# type: ignore` per call site (PLT-40 budget).
    """
    return fn(*args, **kwargs)


def test_listing_create_request_accepts_zero_price_boundary() -> None:
    req = ListingCreateRequest(strategy_id="s1", strategy_version="v1", price=Decimal("0"))
    assert req.price == Decimal("0")


def test_listing_create_request_defaults_price_to_none() -> None:
    req = ListingCreateRequest(strategy_id="s1", strategy_version="v1")
    assert req.price is None


def test_listing_create_request_rejects_negative_price() -> None:
    """docs/FULL_AUDIT_2026-09-02.md §2 — negative price must never pass schema
    validation, since it would otherwise satisfy wallet_service.debit()'s
    `balance >= amount` and inflate the buyer's wallet."""
    with pytest.raises(ValidationError):
        ListingCreateRequest(strategy_id="s1", strategy_version="v1", price=Decimal("-0.01"))


def test_platform_listing_create_request_rejects_negative_price() -> None:
    with pytest.raises(ValidationError):
        PlatformListingCreateRequest(strategy_id="s1", strategy_version="v1", price=Decimal("-1"))


def test_listing_create_request_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(ListingCreateRequest, strategy_version="v1")


def test_to_listing_response_maps_all_fields() -> None:
    seller_id = uuid4()
    listing = Listing(
        id=1,
        strategy_id="s1",
        strategy_version="v1",
        seller_user_id=seller_id,
        price=Decimal("10.5"),
        status="LISTED",
        created_at=NOW,
        seller_type="USER",
    )
    resp = to_listing_response(listing)
    assert resp == ListingResponse(
        id=1,
        strategy_id="s1",
        strategy_version="v1",
        seller_user_id=str(seller_id),
        seller_type="USER",
        price=Decimal("10.5"),
        status="LISTED",
        created_at=NOW,
    )


def test_to_listing_response_preserves_none_price() -> None:
    listing = Listing(
        id=2,
        strategy_id="s2",
        strategy_version="v1",
        seller_user_id=uuid4(),
        price=None,
        status="DRAFT",
        created_at=NOW,
    )
    resp = to_listing_response(listing)
    assert resp.price is None


def test_listing_search_response_requires_items_list() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(
            ListingSearchResponse, items="not-a-list", total=0, page=1, page_size=10
        )


def test_verification_decision_request_rejects_non_string_decision() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(VerificationDecisionRequest, decision=123)


def test_purchase_create_request_defaults_risk_ack_false() -> None:
    req = PurchaseCreateRequest()
    assert req.risk_warning_acknowledged is False


def test_to_purchase_response_maps_risk_warning_flag_when_present() -> None:
    result = PurchaseResult(
        purchase_id=7,
        status="COMPLETED",
        risk_warning="seller unverified",
        platform_commission_amount=Decimal("1.00"),
        seller_payout_amount=Decimal("9.00"),
    )
    resp = to_purchase_response(result)
    assert resp == PurchaseResponse(
        purchase_id=7,
        status="COMPLETED",
        risk_warning=True,
        risk_warning_reason="seller unverified",
        platform_commission_amount=Decimal("1.00"),
        seller_payout_amount=Decimal("9.00"),
    )


def test_to_purchase_response_risk_warning_false_when_none() -> None:
    result = PurchaseResult(purchase_id=8, status="COMPLETED")
    resp = to_purchase_response(result)
    assert resp.risk_warning is False
    assert resp.risk_warning_reason is None


def test_purchase_response_rejects_non_decimal_commission() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(
            PurchaseResponse, purchase_id=1, status="x", platform_commission_amount="abc"
        )


def test_review_create_request_rejects_missing_rating() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(ReviewCreateRequest, comment="great")


def test_review_create_request_allows_null_comment() -> None:
    req = ReviewCreateRequest(rating=5, comment=None)
    assert req.comment is None


def test_to_review_response_maps_all_fields() -> None:
    review = Review(
        id=3,
        listing_id=1,
        reviewer_user_id=uuid4(),
        rating=4,
        comment="good",
        created_at=NOW,
    )
    resp = to_review_response(review)
    assert resp == ReviewResponse(
        review_id=3,
        listing_id=1,
        rating=4,
        comment="good",
        created_at=NOW,
    )


def test_dispute_create_request_rejects_missing_reason() -> None:
    with pytest.raises(ValidationError):
        _call_untyped(DisputeCreateRequest, purchase_id=1)


def test_to_dispute_response_maps_all_fields() -> None:
    dispute = Dispute(
        id=9,
        purchase_id=1,
        submitted_by=uuid4(),
        reason="not delivered",
        status="OPEN",
        created_at=NOW,
    )
    resp = to_dispute_response(dispute)
    assert resp.dispute_id == 9
    assert resp.status == "OPEN"
    assert resp.created_at == NOW


def test_to_listing_response_raises_on_upstream_attribute_error() -> None:
    """Failure injection — a Listing-shaped object missing an attribute the
    converter reads must surface as AttributeError, not be silently swallowed
    into a partially-built response."""

    class BrokenListing:
        id = 1
        strategy_id = "s1"
        strategy_version = "v1"
        seller_user_id = uuid4()
        price = None
        status = "DRAFT"
        # created_at intentionally missing

    with pytest.raises(AttributeError):
        _call_untyped(to_listing_response, BrokenListing())
