"""13.1/13.2 — "3-month Paper Trading history" gate for marketplace listings (9.5-A principle).

Implementation of the verify_paper_trading_eligibility DI callback in
listing_service.py. Actually queries strategy_executions (FD-16) to confirm
that at least one execution started at least 3 months ago with mode='PAPER'
— if no such execution exists (not yet started or less than 3 months), it
rejects the listing (fail-closed). The previous implementation (_always_eligible)
always returned True before FD-16 existed, but now that the table exists, it
verifies actual history.

Two defect fixes from task-1803 (follow-up to review task-1799 REJECT):
(1) The query alone only filtered by strategy_id+strategy_version, allowing the
    gate to pass using PAPER history from other users (not the seller attempting
    the listing) — added strategy_executions.user_id = seller_user_id to only
    accept the seller's own history.
(2) DB query exceptions (asyncpg.PostgresError/OSError) were not caught, so
    failures propagated as 500 errors and "rejection" was not guaranteed — now
    catches exceptions to return False (rejection) and logs structured logs
    (strategy_id/strategy_version/seller_user_id only, no credentials or query
    text) (fail-closed).
"""
from __future__ import annotations

import logging
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

_MIN_PAPER_TRADING_MONTHS = 3


async def check_paper_trading_eligibility(
    pool: asyncpg.Pool, strategy_id: str, strategy_version: str, seller_user_id: UUID
) -> bool:
    """Binds check_paper_trading_eligibility(pool) via functools.partial at app
    assembly time to match the listing_service.VerifyEligibilityFn signature
    (strategy_id, strategy_version, seller_user_id) -> bool (same pattern as
    risk_matching.check_purchase_risk_warning).

    Query failure does not propagate the exception — it fails closed with False
    (rejection). A DB outage must not misinterpret "history unavailable" as
    "history exists" and let an unverified strategy pass the listing gate.
    """
    try:
        async with pool.acquire() as conn:
            eligible = await conn.fetchval(
                "SELECT EXISTS ("
                "SELECT 1 FROM strategy_executions "
                "WHERE strategy_id = $1 AND strategy_version = $2 AND user_id = $3 "
                "AND mode = 'PAPER' AND started_at IS NOT NULL "
                "AND started_at <= now() - ($4 * interval '1 month')"
                ")",
                strategy_id,
                strategy_version,
                seller_user_id,
                _MIN_PAPER_TRADING_MONTHS,
            )
    except (asyncpg.PostgresError, OSError):
        logger.exception(
            "check_paper_trading_eligibility: fail-closed rejection due to query failure "
            "(strategy_id=%s, strategy_version=%s, seller_user_id=%s)",
            strategy_id,
            strategy_version,
            seller_user_id,
        )
        return False
    return bool(eligible)
