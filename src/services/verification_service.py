"""13.3 — Strategy verification workflow (MVP manual-verification bridge).

Spec: FD-13.2, FD-13.8, FD-17.1; Policy 9.5-A, v3.2

Black/Killer Team automation (Phase 4-dependent) is not yet available, so
platform operators perform manual verification using the same criteria
(overfitting, look-ahead bias, survivorship bias checklists). On approve:
PENDING_VERIFICATION → LISTED; on reject: → DRAFT (with reason recorded).

Completion criterion (FD-13.2) — "Cannot transition to LISTED without
verifier approval" is satisfied by an application-level guarantee that this
service is the sole LISTED-transition path (no other code path sets status
directly to LISTED; DB trigger deferred as over-engineering at this MVP stage).

On APPROVE, verified_at is recorded together — FD-13.8 (search/sort) uses this
value as the default sort key (descending by verification-pass date rather than
creation date, to prevent re-registration manipulation).

FD-17.1 event publishing — on verification completion (approve or reject),
publishes "strategy.verification.completed" to the listing owner.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

VALID_DECISIONS = ("APPROVE", "REJECT")

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class VerificationError(Exception):
    """FD-13.2 failure — router converts to 400/404."""


class VerificationNotFoundError(VerificationError):
    """QA task-1163 — non-existent listing. RESOURCE_NOT_FOUND(404)."""


class VerificationInvalidTransitionError(VerificationError):
    """QA task-1163 — decision attempt on a non-PENDING_VERIFICATION state
    (same meaning at pre-check time or at UPDATE-time concurrency conflict:
    "current state does not permit this operation"). STATE_INVALID_TRANSITION(409)."""


class VerificationResult(BaseModel):
    listing_id: int
    status: str
    rejection_reason: str | None = None


class VerificationService:
    def __init__(self, pool: asyncpg.Pool, *, publish: PublishFn | None = None) -> None:
        self._pool = pool
        self._publish = publish

    async def _reject_if_backtest_failed(
        self, conn: asyncpg.pool.PoolConnectionProxy, listing_id: int
    ) -> None:
        """ADR-2026-09-04-C F-04/F-05, INVARIANTS.md I-07 — strategies already
        judged FAIL by the automated verification pipeline (FND-04,
        `strategy_validation_result`) cannot be manually approved by a person.
        Without this gate, the F-04 fix that actually populates
        `hard_fail_reasons` (enabling the FAIL judgment itself) would remain
        a decorative check that enforces nothing."""
        row = await conn.fetchrow(
            "SELECT r.outcome, r.hard_fail_reasons "
            "FROM strategy_listings l "
            "JOIN strategy_validation_run run "
            "  ON run.strategy_id = l.strategy_id AND run.strategy_version = l.strategy_version "
            "JOIN strategy_validation_result r ON r.run_id = run.id "
            "WHERE l.id = $1 AND run.check_type = 'backtest' "
            "ORDER BY run.completed_at DESC NULLS LAST LIMIT 1",
            listing_id,
        )
        if row is not None and row["outcome"] == "FAIL":
            raise VerificationError(
                "Automated verification (backtest) for this strategy has FAILED"
                f"(hard_fail_reasons={list(row['hard_fail_reasons'])}) — cannot approve."
            )

    async def decide(
        self,
        listing_id: int,
        verifier_id: UUID,
        decision: str,
        *,
        rejection_reason: str | None = None,
    ) -> VerificationResult:
        if decision not in VALID_DECISIONS:
            raise VerificationError(f"Unknown decision: {decision}")
        if decision == "REJECT" and not rejection_reason:
            raise VerificationError("A rejection reason is required for REJECT.")

        # Red-team audit (docs/RED_TEAM_FINDINGS.md #05) — with "read then write
        # separately", two different verifiers operating on the same listing nearly
        # simultaneously would silently overwrite: the later commit quietly wins.
        # Add status='PENDING_VERIFICATION' directly to the UPDATE (same pattern as
        # payment_confirmation_service.py::confirm_payment()) so an empty RETURNING
        # row means another verifier already handled it, and fail fast.
        async with self._pool.acquire() as conn:
            pre_check = await conn.fetchrow(
                "SELECT status, seller_user_id FROM strategy_listings WHERE id = $1", listing_id
            )
            if pre_check is None:
                raise VerificationNotFoundError("Listing does not exist.")
            if pre_check["status"] != "PENDING_VERIFICATION":
                raise VerificationInvalidTransitionError(
                    f"Verification is only allowed from PENDING_VERIFICATION state"
                    f"(current: {pre_check['status']})."
                )
            # Full-audit (docs/FULL_AUDIT_2026-09-02.md §2) — #15 §15.6 conflict-of-interest
            # rule "enforced at API level". Close the path at the service layer where a
            # verifier could approve their own listing (the verification_queue_service's
            # queue filter alone cannot block calls that specify listing_id directly).
            if pre_check["seller_user_id"] == verifier_id:
                raise VerificationError(
                    "Cannot verify a listing you are selling (conflict of interest)."
                )

            if decision == "APPROVE":
                await self._reject_if_backtest_failed(conn, listing_id)

            new_status = "LISTED" if decision == "APPROVE" else "DRAFT"
            if decision == "APPROVE":
                row = await conn.fetchrow(
                    "UPDATE strategy_listings SET status = $2, verified_at = now() "
                    "WHERE id = $1 AND status = 'PENDING_VERIFICATION' "
                    "RETURNING seller_user_id",
                    listing_id,
                    new_status,
                )
            else:
                # Red-team audit (#16) — store rejection reason directly in the UPDATE.
                # Previously verification happened but nothing was persisted, so the
                # reason vanished entirely the moment the response went out
                # (seller could no longer learn the reason).
                row = await conn.fetchrow(
                    "UPDATE strategy_listings SET status = $2, rejection_reason = $3 "
                    "WHERE id = $1 AND status = 'PENDING_VERIFICATION' "
                    "RETURNING seller_user_id",
                    listing_id,
                    new_status,
                    rejection_reason,
                )
            if row is None:
                raise VerificationInvalidTransitionError(
                    "Another verifier already handled this (concurrency conflict)."
                )

        if self._publish is not None:
            await self._publish(
                "strategy.verification.completed",
                {
                    "event_type": "strategy.verification.completed",
                    "user_id": str(row["seller_user_id"]),
                    "listing_id": listing_id,
                    "decision": decision,
                    "rejection_reason": rejection_reason if decision == "REJECT" else None,
                },
            )

        return VerificationResult(
            listing_id=listing_id,
            status=new_status,
            rejection_reason=rejection_reason if decision == "REJECT" else None,
        )
