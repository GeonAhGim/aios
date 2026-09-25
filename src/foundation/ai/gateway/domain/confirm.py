"""One-time confirmation ticket rules -- pure functions/value objects, no I/O.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md, table row AI-2
("One-time confirmation ticket rules (preview digest == execution digest,
single use, TTL)"), §1 "confirmation ticket" (tools requiring confirmation,
such as a PAPER execution request, link preview and execution through a
server-side one-time token), §5 (confirmation ticket: single-use conditional
UPDATE), §6 ("confirmation ticket reuse" -> 409, audited). INVARIANTS.md
I-11 (operations requiring confirmation link preview and execution through a
one-time server-side token -- a client-side flag alone is not sufficient).

`contracts/v1.py` (AI-1, not yet implemented) is where the spec's
`ConfirmTicket{ticket_id, action_digest, expires_at}` will eventually live
as a public wire schema. This module pre-implements that shape as a pure
domain type -- the same relationship as token_rules.py's AgentToken (the
domain module owns its own pure value object; a contract schema wraps it
later).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID


class ConfirmRuleError(Exception):
    """Common base for confirmation ticket rule violations."""


class ConfirmTicketExpiredError(ConfirmRuleError):
    """TTL exceeded -- spec §3 AI_CONFIRM_REQUIRED (428) requires a fresh
    preview to reissue a ticket."""


class ConfirmTicketReusedError(ConfirmRuleError):
    """Single-use violation -- spec §6 "confirmation ticket reuse" -> 409,
    audited."""


class ConfirmDigestMismatchError(ConfirmRuleError):
    """Preview digest != execution digest -- spec §3 AI_CONFIRM_MISMATCH
    (409)."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"action_digest mismatch: preview={expected!r} execute={actual!r}")


@dataclass(frozen=True)
class ConfirmTicket:
    """Spec §2.1 table: `ConfirmTicket{ticket_id, action_digest,
    expires_at}`. `consumed_at` is a field this domain module adds to
    express "single use" -- the actual atomic consumption is performed by
    adapters/postgres_token_repository.py (AI-4, not yet implemented) via a
    standard-105 conditional UPDATE; this module only judges an
    already-consumed state."""

    ticket_id: UUID
    action_digest: str
    expires_at: datetime
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.action_digest:
            # An empty digest means there is nothing to confirm -- a value
            # that should already have been computed at preview time.
            # Reject fail-closed so a "confirmation ticket without a
            # digest" can never pass verification.
            raise ConfirmRuleError("action_digest must not be empty")


def is_expired(ticket: ConfirmTicket, now: datetime) -> bool:
    return now >= ticket.expires_at


def is_consumed(ticket: ConfirmTicket) -> bool:
    return ticket.consumed_at is not None


def verify_and_consume(ticket: ConfirmTicket, execute_digest: str, now: datetime) -> ConfirmTicket:
    """Verify a ticket issued at preview time against the execution
    request (spec §2.1 "preview digest == execution digest, single use,
    TTL"). Check order: reuse -> expiry -> digest match -- an
    already-consumed ticket is always rejected as reuse (spec §6
    "confirmation ticket reuse" -> 409) regardless of whether it has also
    since expired, so "it was consumed but hasn't expired yet, so it's
    fine" can never bypass the single-use rule.

    On success, returns a new ConfirmTicket stamped with
    `consumed_at=now` -- persisting that (the single-use conditional
    UPDATE) is the application layer's responsibility to perform
    atomically; this function only performs the pure judgment that
    precedes it."""
    if is_consumed(ticket):
        raise ConfirmTicketReusedError(
            f"ticket {ticket.ticket_id} already consumed at {ticket.consumed_at}"
        )
    if is_expired(ticket, now):
        raise ConfirmTicketExpiredError(f"ticket {ticket.ticket_id} expired at {ticket.expires_at}")
    if ticket.action_digest != execute_digest:
        raise ConfirmDigestMismatchError(ticket.action_digest, execute_digest)
    return replace(ticket, consumed_at=now)
