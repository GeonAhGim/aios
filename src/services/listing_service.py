"""13.2 — Strategy Listing API (create + verification submission).

Spec: functional_design_v1.20.md#FD-13.1/FD-13.1b, #13 §13.5, #15 §15.5

Listing creation (DRAFT) and verification submission (PENDING_VERIFICATION) are
separate actions (revision round correction — do not auto-enqueue newly created
listings into the verification queue; allow the seller to review price etc.
before explicitly submitting).

Three-month Paper Trading history check (Principle 9.5-A) cannot be implemented
here because FD-16 (Strategy Execution) does not yet exist — we accept it as a
verify_paper_trading_eligibility DI callback (same pattern applied throughout
this session, matching WatchdogService.compute_equity, SurgeDetector.verify_provenance, etc.).

create_listing() also checks users.seller_suspended — when FD-18.4 (Seller
Suspension) toggles this flag, new listing creation is immediately rejected.

Deviation (ADR-2026-08-29 §2): seller_type='PLATFORM' (platform direct-sale)
listings use a separate path via create_platform_listing() — we do not reuse
the third-party seller DRAFT→PENDING_VERIFICATION→LISTED verification pipeline;
instead, admin registration publishes directly to LISTED (see method docstring).
commission.py is still reused — we decided to treat it under the same commission
structure.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.services.wallet_service import PLATFORM_HOUSE_USER_ID

VerifyEligibilityFn = Callable[[str, str, UUID], Awaitable[bool]]


class ListingError(Exception):
    """FD-13.1/13.1b failure — router converts to 400/403/404."""


class Listing(BaseModel):
    id: int
    strategy_id: str
    strategy_version: str
    seller_user_id: UUID
    price: Decimal | None
    status: str
    created_at: datetime
    seller_type: str = "USER"


def _validate_price(price: Decimal | None) -> None:
    """Reflects full-audit rule (docs/FULL_AUDIT_2026-09-02.md §2) — negative
    prices would increase wallet balance at purchase time rather than deduct,
    so the service layer rejects them too (three layers: API schema
    `Field(ge=0)`, DB CHECK, and this check)."""
    if price is not None and price < 0:
        raise ListingError("가격은 0 이상이어야 합니다.")


class ListingService:
    def __init__(
        self, pool: asyncpg.Pool, *, verify_paper_trading_eligibility: VerifyEligibilityFn
    ) -> None:
        self._pool = pool
        self._verify_eligibility = verify_paper_trading_eligibility

    async def create_listing(
        self,
        seller_user_id: UUID,
        strategy_id: str,
        strategy_version: str,
        price: Decimal | None,
    ) -> Listing:
        _validate_price(price)
        async with self._pool.acquire() as conn:
            owner_user_id = await conn.fetchval(
                "SELECT owner_user_id FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
            if owner_user_id is None:
                raise ListingError("존재하지 않는 전략입니다.")
            if owner_user_id != seller_user_id:
                raise ListingError("본인이 소유한 전략만 리스팅할 수 있습니다.")

            seller_suspended = await conn.fetchval(
                "SELECT seller_suspended FROM users WHERE user_id = $1", seller_user_id
            )
            if seller_suspended:
                raise ListingError("판매 정지된 계정은 신규 리스팅을 등록할 수 없습니다.")

            row = await conn.fetchrow(
                "INSERT INTO strategy_listings "
                "(strategy_id, strategy_version, seller_user_id, price) "
                "VALUES ($1, $2, $3, $4) RETURNING *",
                strategy_id,
                strategy_version,
                seller_user_id,
                price,
            )
        return Listing(**dict(row))

    async def create_platform_listing(
        self, strategy_id: str, strategy_version: str, price: Decimal | None
    ) -> Listing:
        """ADR-2026-08-29 §2 — Listings registered directly by the platform
        (seller_type='PLATFORM') skip the third-party seller anti-fraud
        verification pipeline (DRAFT→PENDING_VERIFICATION→LISTED via
        submit_for_verification/decide) — the act of admin registration is
        itself the verification, so we publish directly to LISTED. The seller
        is fixed to wallet_service.PLATFORM_HOUSE_USER_ID (house account);
        this account is not a suspension target, so we also skip the
        seller_suspended check."""
        _validate_price(price)
        async with self._pool.acquire() as conn:
            owner_user_id = await conn.fetchval(
                "SELECT owner_user_id FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
            if owner_user_id is None:
                raise ListingError("존재하지 않는 전략입니다.")

            row = await conn.fetchrow(
                "INSERT INTO strategy_listings "
                "(strategy_id, strategy_version, seller_user_id, price, seller_type, status) "
                "VALUES ($1, $2, $3, $4, 'PLATFORM', 'LISTED') RETURNING *",
                strategy_id,
                strategy_version,
                PLATFORM_HOUSE_USER_ID,
                price,
            )
        return Listing(**dict(row))

    async def submit_for_verification(self, listing_id: int, seller_user_id: UUID) -> Listing:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM strategy_listings WHERE id = $1", listing_id
            )
            if row is None:
                raise ListingError("존재하지 않는 리스팅입니다.")
            if row["seller_user_id"] != seller_user_id:
                raise ListingError("본인의 리스팅만 제출할 수 있습니다.")
            if row["status"] != "DRAFT":
                raise ListingError(f"DRAFT 상태에서만 제출할 수 있습니다(현재: {row['status']}).")

            eligible = await self._verify_eligibility(
                row["strategy_id"], row["strategy_version"], seller_user_id
            )
            if not eligible:
                raise ListingError("3개월 이상의 Paper Trading 이력이 필요합니다.")

            updated = await conn.fetchrow(
                "UPDATE strategy_listings SET status = 'PENDING_VERIFICATION' "
                "WHERE id = $1 RETURNING *",
                listing_id,
            )
        return Listing(**dict(updated))
